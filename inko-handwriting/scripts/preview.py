#!/usr/bin/env python3
"""Render a layout plan (the JSON from `inko.py layout --out plan.json` or POST /v1/layout) as a quick preview image,
so the user can check where every line goes BEFORE paying for a generation.

    python preview.py plan.json -o preview.png [--spec layout.json] [--scale 6]

The preview uses a system font (楷体 if available), not the real handwriting — positions and sizes are exact, the
glyph shapes are not. Formulas are shown as their LaTeX source in a light-blue box of the right size.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import json
import math
from pathlib import Path

from _common import Image, ImageDraw, LABEL_TEXT, cjk_font, die, emit

PAPERS = {  # same geometry as the Inko paper presets (mm, origin top-left)
    "blank": {"w": 210, "h": 297},
    "ruled8": {"w": 210, "h": 297, "rules": (34, 274, 8), "rx": (12, 198), "margin": 24, "color": (0x98, 0xb3, 0xd3), "head": True},
    "ruled7": {"w": 210, "h": 297, "rules": (33, 276, 7), "rx": (12, 198), "margin": 24, "color": (0x98, 0xb3, 0xd3), "head": True},
    "letter": {"w": 210, "h": 297, "rules": (52, 268, 9), "rx": (22, 188), "color": (0xe2, 0xa5, 0x9d), "letter": True},
    "compo": {"w": 210, "h": 297, "grid": (15, 34, 20, 20, 9, 9, 3, False), "color": (0x6c, 0x9f, 0x80)},
    "tian": {"w": 210, "h": 297, "grid": (21, 30, 12, 15, 14, 14, 2.4, True), "color": (0xcf, 0x8a, 0x80)},
}


def draw_paper(pid: str, k: float) -> Image.Image:
    p = PAPERS.get(pid, PAPERS["blank"])
    W, H = round(p["w"] * k), round(p["h"] * k)
    im = Image.new("RGB", (W, H), (255, 254, 251))
    d = ImageDraw.Draw(im)
    lw = max(1, round(0.22 * k))
    if "rules" in p:
        y0, y1, sp = p["rules"]
        x0, x1 = p["rx"]
        y = y0
        while y <= y1 + 1e-6:
            d.line([(x0 * k, y * k), (x1 * k, y * k)], fill=p["color"], width=lw)
            y += sp
        if p.get("margin"):
            d.line([(p["margin"] * k, 26 * k), (p["margin"] * k, (p["h"] - 16) * k)], fill=(0xe3, 0xa4, 0x9b), width=max(1, round(0.35 * k)))
            # the exercise-book header exactly as on generated pages: right end at x = 192 mm, baseline y = 24 mm, 3 mm text.
            # Date blanks ≈ x 175.9–182.5 and 185.3–191.9 mm (a box at y 20, h 5.4, size 4 writes the date on it)
            d.text(((p["w"] - 18) * k, 24 * k), "No. ________   Date ____ / ____", fill=p["color"], font=cjk_font(max(6, 3 * k), kai=False),
                   anchor="rs")
        if p.get("letter"):
            d.text((p["w"] / 2 * k, 22 * k), "Inko 信笺", fill=(0xc8, 0x45, 0x2f), font=cjk_font(max(6, 6.4 * k), kai=False), anchor="ms")
            d.line([(20 * k, 30 * k), ((p["w"] - 20) * k, 30 * k)], fill=(0xd6, 0x77, 0x6a), width=max(1, round(0.55 * k)))
            d.line([(20 * k, 31.4 * k), ((p["w"] - 20) * k, 31.4 * k)], fill=(0xd6, 0x77, 0x6a), width=max(1, round(0.2 * k)))
    if "grid" in p:
        gx, gy, cols, rows, cw, ch, gap, tian = p["grid"]
        for r in range(rows):
            top = gy + r * (ch + gap)
            d.rectangle([gx * k, top * k, (gx + cols * cw) * k, (top + ch) * k], outline=p["color"], width=lw)
            for q in range(1, cols):
                d.line([((gx + q * cw) * k, top * k), ((gx + q * cw) * k, (top + ch) * k)], fill=p["color"], width=lw)
    return im


def _text_img(txt: str, size_px: float, color, kai: bool = True) -> tuple[Image.Image, int]:
    """Text drawn on a transparent canvas; returns (image, baseline y inside the image)."""
    f = cjk_font(max(6, size_px), kai=kai)
    try:
        asc, desc = f.getmetrics()
    except AttributeError:
        asc, desc = int(size_px), int(size_px * 0.25)
    w = max(1, int(f.getlength(txt)) + 4) if hasattr(f, "getlength") else max(1, int(size_px * len(txt)) + 4)
    im = Image.new("RGBA", (w, asc + desc + 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((2, asc + 2), txt, font=f, fill=color, anchor="ls")
    return im, asc + 2


def render(res: dict, out: str, scale: float = 6.0, spec: dict | None = None) -> list[str]:
    plan = res.get("plan", res)
    paper = plan.get("paper", {"id": "blank", "w": 210, "h": 297})
    k = scale
    files = []
    pages = plan.get("pages", [])
    for pi, items in enumerate(pages):
        im = draw_paper(paper.get("id", "blank"), k).convert("RGBA")
        d = ImageDraw.Draw(im)
        for b in (spec or {}).get("boxes", []):                      # the boxes you asked for (dashed outlines)
            if b.get("page", 0) != pi:
                continue
            col = (60, 110, 200, 255) if b.get("block") else (170, 110, 60, 255) if b.get("own") is not None else (110, 110, 110, 255)
            if b.get("kind") == "rect":
                x, y, w, h = (b[t] * k for t in ("x", "y", "w", "h"))
                cx, cy = x + w / 2, y + h / 2
                th = math.radians(b.get("rot", 0) or 0)
                pts = [(cx + (px - cx) * math.cos(th) - (py - cy) * math.sin(th), cy + (px - cx) * math.sin(th) + (py - cy) * math.cos(th))
                       for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y))]
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    n = max(1, int(math.hypot(bx - ax, by - ay) / 8))
                    for t in range(0, n, 2):
                        d.line([(ax + (bx - ax) * t / n, ay + (by - ay) * t / n), (ax + (bx - ax) * (t + 1) / n, ay + (by - ay) * (t + 1) / n)], fill=col, width=2)
                d.text((x + 2, y - 14), b.get("id", ""), fill=col)
            elif b.get("kind") == "area":
                for st in b.get("strokes", []):
                    if st.get("erase"):
                        continue
                    r = st["r"] * k
                    for px, py in st["pts"]:
                        d.ellipse([px * k - r, py * k - r, px * k + r, py * k + r], fill=(col[0], col[1], col[2], 28))
            elif b.get("kind") == "path":
                for pth in b.get("paths", []):
                    d.line([(px * k, py * k) for px, py in pth["pts"]], fill=col, width=2)
        for it in items:                                               # every character / formula at its planned place
            s = it["s"] * k
            if it.get("m"):
                a, dd = it["m"]["a"], it["m"]["d"]
                top, bot = it["y"] * k - a * s, it["y"] * k + dd * s
                x0 = it["x"] * k - (it["w"] * k / 2 if it.get("cx") else 0)
                d.rounded_rectangle([x0, top, x0 + it["w"] * k, bot], radius=3, fill=(214, 228, 250, 255), outline=(120, 150, 210, 255))
                t, _ = _text_img(it["c"], max(6, (bot - top) * 0.45), (30, 60, 140, 255), kai=False)
                if t.width > it["w"] * k:
                    t = t.resize((max(1, int(it["w"] * k)), max(1, int(t.height * it["w"] * k / t.width))))
                im.alpha_composite(t, (int(x0 + (it["w"] * k - t.width) / 2), int((top + bot) / 2 - t.height / 2)))
                continue
            g, base = _text_img(it["c"], s * 1.05, (25, 25, 30, 255))
            x = it["x"] * k - (g.width / 2 if it.get("cx") else 0) - 2
            y = it["y"] * k - base
            if it.get("r"):                                        # rotate about the glyph's baseline start, like the real page
                cx_, cy_ = 2, base
                g2 = g.rotate(-it["r"], expand=True, resample=Image.BICUBIC, center=(cx_, cy_))
                x -= (g2.width - g.width) / 2
                y -= (g2.height - g.height) / 2
                g = g2
            im.alpha_composite(g, (int(x), int(y)))
        lab, lb = _text_img(LABEL_TEXT, 0.05 * min(im.size) * 1.12, (128, 128, 128, 200), kai=False)   # where the AI label will be
        im.alpha_composite(lab, (int(im.width - 8 * k - lab.width), int(im.height - 7 * k - lb)))
        p = Path(out) if pi == 0 else Path(out).with_name(f"{Path(out).stem}-{pi + 1}{Path(out).suffix}")
        p.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(p)
        files.append(str(p))
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="plan JSON from `inko.py layout --out`")
    ap.add_argument("-o", "--out", default="preview.png")
    ap.add_argument("--spec", help="the layout JSON you sent (draws your boxes / areas as outlines)")
    ap.add_argument("--scale", type=float, default=6.0, help="pixels per mm (6 ≈ 1260 x 1780 for A4)")
    a = ap.parse_args()
    try:
        res = json.loads(Path(a.plan).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        die(f"cannot read plan: {e}")
    spec = None
    if a.spec:
        spec = json.loads(Path(a.spec).read_text(encoding="utf-8-sig"))
        spec = spec.get("layout", spec)
    emit({"files": render(res, a.out, a.scale, spec), "note": "System font stand-in: positions/sizes are exact, glyph shapes are not."})


if __name__ == "__main__":
    main()
