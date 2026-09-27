#!/usr/bin/env python3
"""Make a page look photographed or scanned — locally, in a second, no API call.

    python photo.py page-1.png -o photo.jpg --preset desk
    python photo.py page-1.png -o scan.jpg --preset scan
    python photo.py page-1.png -o photo.jpg --preset flat --background my-desk.jpg --light warm

Presets (every value can be overridden):
    desk      phone photo of the sheet lying on a wooden desk, shot at an angle, lamp light, soft shadow
    flat      phone photo from straight above (homework-app style): sheet fills most of the frame, gentle shading
    notebook  page of a bound notebook: spine shadow and curl on the left, darker cloth around
    scan      flatbed / scanner-app look: straight, even light, faint grey scanner border, paper grain
    copy      photocopy: grey-scale, contrasty, toner speckles, slightly crooked

Input can be an Inko page, a restyled page (ink.py) or a composed page (compose.py). The visible label
「AI生成 · Inko」 is taken off the paper and put back on the final photo at >= 5 % of its shortest side, and the
implicit AIGC metadata is kept. Use --seed to get a different but repeatable variation.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import math

import numpy as np

from _common import (Image, ImageFilter, LABEL_MIN_FRAC, apply_label, die, emit, erase_label, find_label_bbox, gaussian, label_box,
                     label_from_b64, label_from_page, luminance, open_image, save_image, value_noise)

PRESETS = {
    "desk": dict(tilt=14.0, turn=-5.0, roll=3.0, fill=0.82, bg="wood", light="lamp", shadow=0.55, curl=0.25, blur=0.45, dof=0.9,
                 noise=3.0, vignette=0.25, size=3000, quality=87),
    "flat": dict(tilt=4.0, turn=2.0, roll=-1.2, fill=0.9, bg="table", light="daylight", shadow=0.35, curl=0.12, blur=0.6, dof=0.5,
                 noise=2.6, vignette=0.18, size=3000, quality=88),
    "notebook": dict(tilt=9.0, turn=4.0, roll=1.5, fill=0.86, bg="cloth", light="warm", shadow=0.45, curl=0.6, blur=0.7, dof=1.0,
                     noise=3.0, vignette=0.22, size=3000, quality=86, spine=True),
    "scan": dict(tilt=0.0, turn=0.0, roll=0.45, fill=0.97, bg="scanner", light="even", shadow=0.12, curl=0.0, blur=0.35, dof=0.0,
                 noise=1.4, vignette=0.0, size=0, quality=90),
    "copy": dict(tilt=0.0, turn=0.0, roll=-0.8, fill=0.96, bg="scanner", light="even", shadow=0.0, curl=0.0, blur=0.8, dof=0.0,
                 noise=2.0, vignette=0.0, size=0, quality=85, copy=True),
}
LIGHTS = {"lamp": (1.0, 0.965, 0.9), "warm": (1.0, 0.975, 0.935), "daylight": (0.97, 0.99, 1.0), "cloudy": (0.95, 0.97, 1.0),
          "even": (1.0, 1.0, 1.0)}


def backdrop(kind: str, W: int, H: int, rng) -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    if kind == "wood":
        grain = np.sin(x / (W / 60) + 4 * value_noise(H, W, W / 6, rng)) * 0.5 + 0.5
        base = 104 + 26 * grain + 10 * value_noise(H, W, W / 40, rng)
        return np.stack([base * 1.0, base * 0.76, base * 0.55], 2)
    if kind == "cloth":
        weave = 0.5 + 0.5 * np.sin(x / 1.6) * np.sin(y / 1.6)
        base = 70 + 8 * weave + 8 * value_noise(H, W, W / 10, rng)
        return np.stack([base * 0.85, base * 0.9, base * 1.05], 2)
    if kind == "table":
        base = 196 + 6 * value_noise(H, W, W / 8, rng) + 2 * value_noise(H, W, 6, rng)
        return np.stack([base * 0.98, base * 0.96, base * 0.92], 2)
    if kind == "scanner":
        base = 214 + 3 * value_noise(H, W, W / 10, rng)
        return np.stack([base, base, base * 1.01], 2)
    die(f"unknown background '{kind}'")
    return np.zeros((H, W, 3), np.float32)


def camera_quad(page_wh, out_wh, tilt, turn, roll, fill) -> list:
    """Corners of the page as a phone camera sees it (pinhole, principal point at the centre)."""
    W, H = out_wh
    pw, ph = page_wh
    f = 0.9 * max(W, H)
    pts = np.array([[-pw / 2, -ph / 2, 0], [pw / 2, -ph / 2, 0], [pw / 2, ph / 2, 0], [-pw / 2, ph / 2, 0]], np.float64)
    ax, ay, az = np.radians([-tilt, turn, roll])
    Rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    Rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    P = pts @ (Rz @ Ry @ Rx).T
    P[:, 2] += 1.0
    proj = lambda Z: np.stack([f * P[:, 0] / (P[:, 2] - 1 + Z), f * P[:, 1] / (P[:, 2] - 1 + Z)], 1)
    lo, hi = 0.05, 1e7                                          # distance so the sheet fills `fill` of the frame
    for _ in range(60):
        Z = math.sqrt(lo * hi)
        q = proj(Z)
        ext = max((q[:, 0].max() - q[:, 0].min()) / W, (q[:, 1].max() - q[:, 1].min()) / H)
        lo, hi = (Z, hi) if ext > fill else (lo, Z)
    q = proj(math.sqrt(lo * hi))
    q[:, 0] += W / 2 - (q[:, 0].max() + q[:, 0].min()) / 2
    q[:, 1] += H / 2 - (q[:, 1].max() + q[:, 1].min()) / 2
    return [(float(a), float(b)) for a, b in q]


def _homography(src, dst):
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    return np.linalg.solve(np.array(A, np.float64), np.array(b, np.float64))


def paper_look(rgb: np.ndarray, rng, amount: float = 1.0) -> np.ndarray:
    """Real paper is never pure white: faint fibre texture and an off-white tint."""
    H, W = rgb.shape[:2]
    fib = 1.6 * value_noise(H, W, 3.0, rng) + 1.2 * value_noise(H, W, 18.0, rng) + 1.5 * value_noise(H, W, max(40.0, W / 12), rng)
    tint = np.array([0.985, 0.978, 0.955], np.float32)
    return np.clip(rgb * tint[None, None, :] + amount * fib[..., None], 0, 255)


def curl_page(rgb: np.ndarray, curl: float, spine: bool) -> np.ndarray:
    """Bend the page slightly: rows sag towards the middle (or rise near a notebook spine), with matching shading."""
    if curl <= 0:
        return rgb
    H, W = rgb.shape[:2]
    x = np.arange(W, dtype=np.float32) / W
    if spine:                                                       # bound notebook: page rises out of the gutter
        dy = curl * 0.035 * H * np.exp(-x / 0.16)
        shade = 1 - 0.62 * curl * np.exp(-x / 0.045) - 0.16 * curl * np.exp(-x / 0.22)
    else:
        dy = curl * 0.006 * H * np.sin(np.pi * x)
        shade = 1 - 0.06 * curl * np.cos(np.pi * x) ** 2
    # every column gets its own vertical shift (bilinear), so printed lines bend smoothly instead of in steps
    mesh = []
    step = 8
    for x0 in range(0, W, step):
        x1 = min(W, x0 + step)
        s0, s1 = float(dy[x0]), float(dy[x1 - 1])
        mesh.append(((x0, 0, x1, H), (x0, s0, x0, H + s0, x1, H + s1, x1, s1)))
    out = np.stack([np.asarray(Image.fromarray(rgb[..., c].astype(np.float32), "F").transform((W, H), Image.MESH, mesh, Image.BILINEAR),
                               np.float32) for c in range(3)], 2)
    return out * shade[None, :, None]


def lighting(H: int, W: int, kind: str, vignette: float, rng) -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    xn, yn = x / W - 0.5, y / H - 0.5
    if kind == "even":
        g = np.ones((H, W), np.float32)
    else:
        ang = rng.uniform(0, 2 * math.pi)
        g = 1.0 + 0.10 * (math.cos(ang) * xn + math.sin(ang) * yn) * 2
        if kind == "lamp":
            cx, cy = rng.uniform(-0.6, 0.6), rng.uniform(-0.7, -0.2)
            g *= 0.86 + 0.2 * np.exp(-((xn - cx) ** 2 + (yn - cy) ** 2) / 0.35)
    g *= 1 - vignette * (xn ** 2 + yn ** 2) * 1.6
    tint = np.array(LIGHTS.get(kind, LIGHTS["daylight"]), np.float32)
    return g[..., None] * tint[None, None, :]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--out", required=True, help=".jpg (typical for photos) / .png / .webp")
    ap.add_argument("--preset", default="desk", choices=list(PRESETS))
    ap.add_argument("--background", help="your own background photo (desk, table …) instead of the built-in one")
    ap.add_argument("--tilt", type=float, help="camera tilt in degrees (0 = straight above)")
    ap.add_argument("--turn", type=float, help="sideways camera angle in degrees")
    ap.add_argument("--roll", type=float, help="rotation of the sheet in degrees")
    ap.add_argument("--fill", type=float, help="how much of the frame the sheet fills, 0.5–0.98")
    ap.add_argument("--light", choices=list(LIGHTS), help="colour/shape of the light")
    ap.add_argument("--shadow", type=float, help="0–1 drop shadow under the sheet")
    ap.add_argument("--curl", type=float, help="0–1 page bend")
    ap.add_argument("--blur", type=float, help="lens softness in px")
    ap.add_argument("--noise", type=float, help="sensor noise in grey levels")
    ap.add_argument("--size", type=int, help="long side of the output in px (0 = keep the page size)")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("-q", "--quality", type=int, help="JPEG/WebP quality")
    ap.add_argument("--label-corner", default="br", choices=["br", "bl", "tr", "tl"])
    a = ap.parse_args()
    P = dict(PRESETS[a.preset])
    for k in ("tilt", "turn", "roll", "fill", "light", "shadow", "curl", "blur", "noise", "size", "quality"):
        v = getattr(a, k)
        if v is not None:
            P[k] = v
    rng = np.random.default_rng(a.seed)
    img, meta = open_image(a.image)
    rgb = np.asarray(img.convert("RGB"), np.float32)
    # 1) take the label off the paper (it goes back on top of the finished photo)
    bbox = find_label_bbox(rgb.astype(np.uint8))
    label = label_from_b64(meta.get("label_png"))
    if bbox:                                             # paper under the label rebuilt (ruled lines / grid continue)
        clean = erase_label(rgb, bbox)
        label = label or label_from_page(rgb, bbox, clean)
        rgb = clean.astype(np.float32)
    had_label = bool(bbox) or label is not None
    # 2) paper + bend
    page = paper_look(rgb, rng, 0.0 if P.get("copy") else 1.0)
    page = curl_page(page, P["curl"], bool(P.get("spine")))
    if P.get("copy"):
        L = luminance(page)
        L = np.clip((L - 40) * 1.35, 0, 255)
        speck = (rng.random(L.shape) < 0.0006) * rng.uniform(80, 200, L.shape)
        L = np.clip(L - speck, 0, 255)
        page = np.repeat(L[..., None], 3, 2)
    ph, pw = page.shape[:2]
    # 3) where the sheet lands in the photo
    if P["size"]:
        k = P["size"] / max(pw, ph)
        W, H = int(round(pw * k * (1.0 if P["tilt"] else 1.0))), int(round(ph * k))
        W, H = (int(H * 3 / 4), H) if ph >= pw else (W, int(W * 3 / 4))      # phone frame 3:4
    else:
        W, H = pw, ph
    if a.background:
        bgi, _ = open_image(a.background)
        bgi = bgi.convert("RGB")
        s = max(W / bgi.width, H / bgi.height)
        bgi = bgi.resize((max(W, round(bgi.width * s)), max(H, round(bgi.height * s))), Image.LANCZOS)
        l, t = (bgi.width - W) // 2, (bgi.height - H) // 2
        bg = np.asarray(bgi.crop((l, t, l + W, t + H)), np.float32)
    else:
        bg = backdrop(P["bg"], W, H, rng)
    corners = camera_quad((pw, ph), (W, H), P["tilt"], P["turn"], P["roll"], P["fill"])
    coeffs = _homography(corners, [(0, 0), (pw, 0), (pw, ph), (0, ph)])        # photo -> page
    page_im = Image.fromarray(np.clip(page, 0, 255).astype(np.uint8), "RGB")
    warped = np.asarray(page_im.transform((W, H), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC), np.float32)
    mask = np.asarray(Image.new("L", (pw, ph), 255).transform((W, H), Image.PERSPECTIVE, tuple(coeffs), Image.BILINEAR), np.float32) / 255
    # 4) shadow under the sheet, lighting, lens, sensor
    if P["shadow"] > 0:
        off = int(0.006 * max(W, H))
        sh = gaussian(np.roll(np.roll(mask, off, 0), off // 2, 1), 0.012 * max(W, H))
        bg = bg * (1 - P["shadow"] * sh[..., None] * (1 - mask[..., None]))
    comp = bg * (1 - mask[..., None]) + warped * mask[..., None]
    comp *= lighting(H, W, P["light"], P["vignette"], rng)
    if P["blur"] > 0 or P.get("dof"):
        sharp = comp
        soft = np.stack([gaussian(comp[..., c], P["blur"]) for c in range(3)], 2)
        comp = soft
        if P.get("dof"):                                                      # far edge (top) a bit softer
            far = np.stack([gaussian(comp[..., c], P["blur"] + P["dof"]) for c in range(3)], 2)
            t = np.clip((0.4 - np.arange(H, dtype=np.float32) / H) / 0.4, 0, 1)[:, None, None] ** 1.5 * min(1.0, P["tilt"] / 14)
            comp = comp * (1 - t) + far * t
        del sharp
    if P["noise"] > 0:
        lum_n = rng.normal(0, P["noise"], (H, W, 1))
        chroma_n = rng.normal(0, P["noise"] * 0.5, (H, W, 3))
        comp = comp + lum_n + chroma_n
    if P.get("copy"):
        comp = np.repeat(luminance(np.clip(comp, 0, 255))[..., None], 3, 2)
    out = Image.fromarray(np.clip(comp, 0, 255).round().astype(np.uint8), "RGB")
    # 5) the label goes back on, readable, >= 5 % of the shortest side
    if had_label:                                        # never half on the sheet, half on the table
        at = None
        x0, y0, x1, y1 = label_box((W, H), label, where=a.label_corner)
        cover = float(mask[y0:y1, x0:x1].mean()) if y1 > y0 and x1 > x0 else 0.0
        if 0.03 < cover < 0.97 and a.label_corner == "br":
            br, c = np.array(corners[2]), np.mean(np.array(corners), 0)
            for t in (0.05, 0.08, 0.12, 0.16):          # slide from the sheet's corner towards its centre until inside
                ax, ay = br + t * (c - br)
                bx0, by0 = int(ax - (x1 - x0)), int(ay - (y1 - y0))
                if bx0 >= 0 and by0 >= 0 and mask[by0:int(ay), bx0:int(ax)].mean() > 0.985:
                    at = (ax, ay)
                    break
        out = apply_label(out, label, frac=LABEL_MIN_FRAC * 1.06, where=a.label_corner, at=at)
    save_image(out, a.out, meta, quality=int(P["quality"]))
    emit({"out": a.out, "preset": a.preset, "size_px": [W, H], "sheet_corners": [[round(x), round(y)] for x, y in corners],
          "visible_label": had_label, "aigc_metadata": bool(meta.get("aigc")),
          "tip": "Different look: --seed N, --tilt/--turn/--roll, --light, --background your-desk.jpg"})


if __name__ == "__main__":
    main()
