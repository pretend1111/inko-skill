#!/usr/bin/env python3
"""Ink tools for Inko pages — all local, instant and free (no API call).

    python ink.py extract page-1.png -o ink.png
        Separate the handwriting from the paper: transparent RGBA layer, ruled lines / grid / paper colour removed.
        The corner label 「AI生成 · Inko」 stays in the layer (and is remembered in the file for later scripts).

    python ink.py restyle page-1.png -o page-1-blue.png --color blue --texture ballpoint --weight 0.4
        Change ink colour, stroke weight, darkness and pen texture of a page (keeps its paper) or of an ink layer.

    python ink.py info page-1.png
        What is in the image: page or layer, paper colour, ink colour, stroke width, label position.

Prefer the API's `pen` options when generating (they are applied at full quality before the label is drawn);
use restyle for quick after-the-fact tweaks, previews of alternatives, or colours the API doesn't offer.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse

import numpy as np

from _common import (Image, ImageFilter, die, emit, find_label_bbox, gaussian, label_crop, label_from_b64, label_to_b64, luminance,
                     open_image, px_per_mm_of, save_image, value_noise)

INK_COLORS = {
    "black": (24, 24, 28), "blue": (30, 62, 168), "blueblack": (30, 40, 78), "red": (196, 34, 42), "green": (28, 112, 64),
    "purple": (92, 42, 128), "brown": (96, 58, 32), "pencil": (74, 74, 80), "gray": (90, 90, 96),
}
TEXTURES = ("none", "gel", "ballpoint", "fountain", "pencil", "marker")


# ── separation ──────────────────────────────────────────────────────────────

def _structure_mask(rgb: np.ndarray, lum: np.ndarray, paper_l: float) -> tuple[np.ndarray, float]:
    """The paper's own printing (ruled lines, grid, margin line, header text) -> (mask, typical line luminance)."""
    chroma = rgb.max(2) - rgb.min(2)
    light = (lum < paper_l - 6) & (lum > 100)
    lines = np.zeros_like(light)
    rows = light.mean(1) > 0.25                              # long horizontal lines
    cols = light.mean(0) > 0.25                              # long vertical lines (grid, margin)
    if rows.any():
        lines[rows, :] |= light[rows, :]
    if cols.any():
        lines[:, cols] |= light[:, cols]
    mask = lines.copy()
    line_l = float(np.median(lum[lines])) if lines.sum() > 500 else paper_l - 70
    if lines.sum() > 500:                                    # printed text in the line colour (e.g. "No.__ Date__")
        med = np.median(rgb[lines & (chroma > 8)], axis=0) if (lines & (chroma > 8)).sum() > 200 else None
        if med is not None:
            mask |= (np.abs(rgb - med).max(2) < 30) & (chroma > 8)
            d0 = med - med.mean()                                # same hue as the lines, any lightness (anti-aliased header text)
            dv = rgb - rgb.mean(2, keepdims=True)
            cos = (dv @ d0) / (np.linalg.norm(dv, axis=2) * np.linalg.norm(d0) + 1e-6)
            mask |= (cos > 0.95) & (chroma > 6) & (lum > line_l - 30)
    hue_red = rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2])
    mask |= (hue_red > 18) & (hue_red > 0.28 * (255 - rgb.min(2)))   # red/pink margin lines, letterhead (Inko never writes red)
    return mask, line_l


def separate(img: Image.Image) -> dict:
    """page image -> {alpha (0..1), ink_rgb, paper_rgb, label_bbox, is_layer}"""
    if img.mode in ("RGBA", "LA") and np.asarray(img.getchannel("A")).min() < 250:
        rgba = np.asarray(img.convert("RGBA"), np.float32)
        alpha = rgba[..., 3] / 255.0
        core = alpha > 0.8
        ink = np.median(rgba[core][:, :3], axis=0) if core.any() else np.array([24, 24, 28], np.float32)
        return {"alpha": alpha, "ink_rgb": tuple(int(v) for v in ink), "paper_rgb": None, "label_bbox": None, "is_layer": True,
                "rgb": rgba[..., :3]}
    rgb = np.asarray(img.convert("RGB"), np.float32)
    lum = luminance(rgb)
    label = find_label_bbox(np.asarray(img.convert("RGB")))
    body = np.ones(lum.shape, bool)                                  # statistics ignore the gray AI label
    if label:
        x0, y0, x1, y1 = label
        body[y0:y1, x0:x1] = False
    bright = (lum >= np.percentile(lum[body], 55)) & body            # >=: a clean page is mostly one exact paper colour
    paper = np.median(rgb[bright], axis=0)
    paper_l = float(paper @ np.array([0.299, 0.587, 0.114]))
    dark = (lum < paper_l - 60) & body
    ink_l = float(np.percentile(lum[dark], 8)) if dark.sum() > 200 else 40.0
    ink_l = min(ink_l, paper_l - 80)
    alpha = np.clip((paper_l - lum) / max(20.0, paper_l - ink_l), 0, 1)
    struct, line_l = _structure_mask(rgb, lum, paper_l)
    alpha[struct & (lum > min(line_l, paper_l - 40) - 26)] = 0             # printed lines go; ink that crosses them is darker and stays
    alpha[alpha < 0.04] = 0
    core = (alpha > 0.85) & body
    ink = np.median(rgb[core], axis=0) if core.sum() > 50 else np.array([24, 24, 28], np.float32)
    return {"alpha": alpha.astype(np.float32), "ink_rgb": tuple(int(v) for v in ink), "paper_rgb": tuple(int(v) for v in paper),
            "label_bbox": label, "is_layer": False, "rgb": rgb, "body": body}


def body_mask(sep: dict, meta: dict | None = None) -> np.ndarray:
    """Pixels that are handwriting territory (everything except the AI label box)."""
    m = sep.get("body")
    if m is not None:
        return m
    m = np.ones(sep["alpha"].shape, bool)
    lab = sep.get("label_bbox") or ((meta or {}).get("ink") or {}).get("label_bbox")
    if lab:
        x0, y0, x1, y1 = lab
        m[y0:y1, x0:x1] = False
    return m


def stroke_width_px(alpha: np.ndarray) -> float:
    """Rough median stroke width: ink area / skeleton-ish length (edge count / 2)."""
    m = alpha > 0.5
    if m.sum() < 100:
        return 0.0
    edges = (m[:, 1:] != m[:, :-1]).sum() + (m[1:, :] != m[:-1, :]).sum()
    return float(2 * m.sum() / max(1, edges))


def clean_paper(rgb: np.ndarray, alpha: np.ndarray, width_px: float) -> np.ndarray:
    """Paper with the handwriting removed (fills stroke pixels from their surroundings)."""
    size = int(max(3, min(15, round(width_px * 2 + 3)))) | 1
    out = rgb.copy()
    hole = alpha > 0.03
    if not hole.any():
        return out
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    filled = np.asarray(im.filter(ImageFilter.MaxFilter(size)), np.float32)        # brightest neighbour = paper
    grow = np.asarray(Image.fromarray((hole * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)), np.uint8) > 0
    out[grow] = filled[grow]
    return out


# ── restyling ───────────────────────────────────────────────────────────────

def _dilate(a: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return a
    im = Image.fromarray((a * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(2 * px + 1))
    return np.asarray(im, np.float32) / 255.0


def _erode(a: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return a
    im = Image.fromarray((a * 255).astype(np.uint8), "L").filter(ImageFilter.MinFilter(2 * px + 1))
    return np.asarray(im, np.float32) / 255.0


def adjust_weight(alpha: np.ndarray, delta_px: float) -> np.ndarray:
    """Grow (delta > 0) or shrink strokes by delta pixels per side, fractional amounts blended."""
    if abs(delta_px) < 0.05:
        return alpha
    n = int(abs(delta_px))
    frac = abs(delta_px) - n
    op = _dilate if delta_px > 0 else _erode
    a0 = op(alpha, n) if n else alpha
    a1 = op(alpha, n + 1)
    out = a0 * (1 - frac) + a1 * frac
    return gaussian(out, 0.45) if delta_px > 0 else out


def adjust_darkness(alpha: np.ndarray, d: float) -> np.ndarray:
    if abs(d) < 0.01:
        return alpha
    if d > 0:
        return 1 - (1 - alpha) ** (1 + 1.6 * d)
    return (alpha ** (1 + 1.1 * -d)) * (1 - 0.3 * -d)


def apply_texture(alpha: np.ndarray, kind: str, ppm: float, seed: int) -> tuple[np.ndarray, np.ndarray | None]:
    """-> (alpha, per-pixel colour multiplier or None)"""
    rng = np.random.default_rng(seed)
    H, W = alpha.shape
    if kind in ("none", "", None):
        return alpha, None
    if kind == "gel":
        a = np.clip(alpha * 1.18, 0, 1)
        return np.where(alpha > 0.05, np.maximum(a, alpha), alpha), None
    if kind == "ballpoint":
        low = value_noise(H, W, 2.6 * ppm, rng)
        fine = value_noise(H, W, 0.22 * ppm, rng)
        skip = np.clip((fine - 0.55) * 3, 0, 1)
        a = alpha * (0.80 + 0.14 * low) * (1 - 0.42 * skip)
        return np.clip(a, 0, 1), (1.0 + 0.10 * low)[..., None]
    if kind == "fountain":
        b = gaussian(alpha, 0.10 * ppm)
        edge = np.clip(alpha - gaussian(alpha, 0.14 * ppm), 0, 1)
        a = np.clip(np.maximum(alpha, 0.45 * b) + 0.55 * edge, 0, 1)
        low = value_noise(H, W, 4 * ppm, rng)
        return a, (1.0 + 0.12 * low)[..., None]
    if kind == "pencil":
        grain = (value_noise(H, W, 0.11 * ppm, rng) + 1) / 2
        streak = (value_noise(H, W, 0.35 * ppm, rng) + 1) / 2
        a = gaussian(alpha, 0.03 * ppm) * (0.30 + 0.50 * grain + 0.20 * streak) * 0.92
        return np.clip(a, 0, 1), None
    if kind == "marker":
        a = np.clip(adjust_weight(alpha, 0.18 * ppm) * 1.3, 0, 1)
        return a, None
    die(f"unknown texture '{kind}' (use one of {', '.join(TEXTURES)})")
    return alpha, None


def parse_color(s: str | None, default) -> tuple[int, int, int]:
    if not s:
        return tuple(int(v) for v in default)
    s = s.strip().lower()
    if s in INK_COLORS:
        return INK_COLORS[s]
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    die(f"colour '{s}' not understood: use {', '.join(INK_COLORS)} or #RRGGBB")
    return (0, 0, 0)


def composite(paper_rgb: np.ndarray, alpha: np.ndarray, ink_rgb, mult: np.ndarray | None = None) -> np.ndarray:
    """Ink sits 'in' the paper: multiply blend (paper texture and lines show through faintly)."""
    ink = np.array(ink_rgb, np.float32)[None, None, :]
    if mult is not None:
        ink = np.clip(ink * mult, 0, 255)
    a = alpha[..., None]
    return np.clip(paper_rgb * (1 - a + a * ink / 255.0), 0, 255)


# ── commands ────────────────────────────────────────────────────────────────

def layer_image(alpha: np.ndarray, ink_rgb, mult: np.ndarray | None = None) -> Image.Image:
    H, W = alpha.shape
    out = np.zeros((H, W, 4), np.float32)
    col = np.array(ink_rgb, np.float32)[None, None, :] * (mult if mult is not None else 1.0)
    out[..., :3] = np.clip(col, 0, 255)
    out[..., 3] = np.clip(alpha, 0, 1) * 255
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def cmd_extract(a) -> None:
    img, meta = open_image(a.input)
    sep = separate(img)
    if sep["is_layer"]:
        die("input already is a transparent ink layer")
    alpha = sep["alpha"]
    lab = sep["label_bbox"]
    label_b64 = None
    if lab:
        crop = label_crop(np.asarray(img.convert("RGB")), lab)
        label_b64 = label_to_b64(crop)
    ppm = px_per_mm_of(img, meta)
    out = layer_image(alpha, sep["ink_rgb"])
    if lab:                                              # label stays visible in the layer, in its own gray
        x0, y0, x1, y1 = lab
        arr = np.asarray(out).copy()
        arr[y0:y1, x0:x1] = np.asarray(label_from_b64(label_b64))
        out = Image.fromarray(arr, "RGBA")
    body = body_mask(sep)
    ys, xs = np.nonzero((alpha > 0.3) & body)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
    meta = dict(meta)
    meta["label_png"] = label_b64 or meta.get("label_png")
    meta["ink"] = {"px_per_mm": ppm, "ink_rgb": sep["ink_rgb"], "paper_rgb": sep["paper_rgb"], "label_bbox": lab, "ink_bbox": bbox,
                   "stroke_px": round(stroke_width_px(alpha * body), 2), "source": str(a.input), "size": list(img.size)}
    save_image(out, a.out, meta, dpi=ppm * 25.4)
    emit({"out": a.out, "ink_rgb": sep["ink_rgb"], "paper_rgb": sep["paper_rgb"], "label": bool(lab), "label_bbox": lab,
          "ink_bbox_px": bbox, "px_per_mm": round(ppm, 3), "stroke_px": meta["ink"]["stroke_px"]})


def cmd_restyle(a) -> None:
    img, meta = open_image(a.input)
    sep = separate(img)
    alpha = sep["alpha"].copy()
    ppm = px_per_mm_of(img, meta)
    lab = sep["label_bbox"] or ((meta.get("ink") or {}).get("label_bbox"))
    keep = None
    if lab:                                               # never restyle the AI label: cut it out, paste it back unchanged
        x0, y0, x1, y1 = lab
        keep = (alpha[y0:y1, x0:x1].copy(), sep["rgb"][y0:y1, x0:x1].copy())
        alpha[y0:y1, x0:x1] = 0
    base_ink = sep["ink_rgb"]
    width = stroke_width_px(alpha)
    delta = a.weight_mm * ppm if a.weight_mm else a.weight * 0.09 * ppm    # ±1 ≈ ±0.09 mm per side, like the API
    if delta:
        alpha = adjust_weight(alpha, delta)
    alpha = adjust_darkness(alpha, a.darkness)
    color = parse_color(a.color, INK_COLORS["pencil"] if a.texture == "pencil" and not a.color else base_ink)
    alpha, mult = apply_texture(alpha, a.texture, ppm, a.seed)
    if a.bleed:
        alpha = np.clip(np.maximum(alpha, 0.55 * gaussian(alpha, a.bleed * 0.25 * ppm)), 0, 1)
    if a.opacity != 1.0:
        alpha = alpha * a.opacity
    if sep["is_layer"]:
        out = layer_image(alpha, color, mult)
        if keep is not None:
            arr = np.asarray(out).copy()
            x0, y0, x1, y1 = lab
            arr[y0:y1, x0:x1, :3] = 128
            arr[y0:y1, x0:x1, 3] = (keep[0] * 255).astype(np.uint8)
            out = Image.fromarray(arr, "RGBA")
    else:
        paper = clean_paper(sep["rgb"], sep["alpha"], width)
        res = composite(paper, alpha, color, mult)
        if keep is not None:
            x0, y0, x1, y1 = lab
            res[y0:y1, x0:x1] = keep[1]
        out = Image.fromarray(res.round().astype(np.uint8), "RGB")
    save_image(out, a.out, meta, dpi=ppm * 25.4, quality=a.quality)
    emit({"out": a.out, "color": color, "texture": a.texture, "weight": a.weight, "darkness": a.darkness, "bleed": a.bleed,
          "kept_label": bool(lab)})


def cmd_info(a) -> None:
    img, meta = open_image(a.input)
    sep = separate(img)
    ppm = px_per_mm_of(img, meta)
    body = body_mask(sep, meta)
    alpha = sep["alpha"] * body
    ys, xs = np.nonzero(alpha > 0.3)
    emit({"size_px": list(img.size), "px_per_mm": round(ppm, 3), "kind": "ink layer" if sep["is_layer"] else "page",
          "paper_rgb": sep["paper_rgb"], "ink_rgb": sep["ink_rgb"], "stroke_px": round(stroke_width_px(alpha), 2),
          "stroke_mm": round(stroke_width_px(alpha) / ppm, 3), "label_bbox": sep["label_bbox"] or (meta.get("ink") or {}).get("label_bbox"),
          "ink_bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None,
          "aigc_metadata": bool(meta.get("aigc"))})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("extract", help="page -> transparent ink layer")
    s.add_argument("input")
    s.add_argument("-o", "--out", required=True, help="output .png (transparent)")
    s = sp.add_parser("restyle", help="change colour / weight / darkness / texture")
    s.add_argument("input")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--color", help=f"{', '.join(INK_COLORS)} or #RRGGBB (default: keep)")
    s.add_argument("--weight", type=float, default=0.0, help="-1..1 like the API's pen.weight (±1 ≈ ±0.09 mm per side); larger values allowed")
    s.add_argument("--weight-mm", type=float, help="grow/shrink strokes by this many mm per side instead")
    s.add_argument("--darkness", type=float, default=0.0, help="-1..1")
    s.add_argument("--texture", default="none", choices=TEXTURES)
    s.add_argument("--bleed", type=float, default=0.0, help="0..1 ink soaking into cheap paper")
    s.add_argument("--opacity", type=float, default=1.0)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--quality", type=int, default=92, help="JPEG/WebP quality if the output is .jpg/.webp")
    s = sp.add_parser("info")
    s.add_argument("input")
    a = ap.parse_args()
    {"extract": cmd_extract, "restyle": cmd_restyle, "info": cmd_info}[a.cmd](a)


if __name__ == "__main__":
    main()
