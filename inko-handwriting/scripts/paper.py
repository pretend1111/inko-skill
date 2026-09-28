#!/usr/bin/env python3

"""Draw standard flat paper backgrounds (blank, ruled, grid, dots, tian, compo).

No image input or camera effects.
    python paper.py make -o paper.png --kind ruled --size A4
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
from pathlib import Path

import numpy as np

from _common import Image, ImageDraw, emit, note, parse_size_mm, save_image


def suggest_layout(pitch_mm: float, width_mm: float, n_free: int) -> dict:
    """Suggest an A4 layout with writing proportions derived from the flat paper."""
    k = min(1.0, 186.0 / max(1.0, width_mm))              # keep the line inside an A4 frame
    p = pitch_mm * k
    size = round(0.62 * p, 2)                              # Inko's own ruled papers write at 0.62 x line spacing
    w = round(width_mm * k, 1)
    cpl = int(w // (size * 1.06))
    return {"paperId": "blank", "d": {"size": size, "line": round(p / size, 4), "margins": [22, round(210 - 20 - w, 1), 26, 20], "indent": 2},
            "scale": round(k, 4), "chars_per_line_est": cpl, "lines_per_generated_page": int((297 - 22 - 26) // p),
            "free_lines": n_free, "capacity_chars_est": cpl * n_free}

LINE_COLORS = {"blue": (152, 179, 211), "gray": (175, 175, 178), "green": (108, 159, 128), "red": (207, 138, 128), "black": (90, 90, 90)}

PAPER_COLORS = {"white": (253, 253, 250), "cream": (250, 245, 232), "yellow": (252, 244, 205), "kraft": (214, 190, 150), "gray": (236, 236, 234)}

def make_paper(kind: str, size_mm: tuple, dpi: float, pitch: float, color: str, paper: str, margin_line: float | None,
               top: float, bottom: float, side: float) -> tuple[Image.Image, dict]:
    ppm = dpi / 25.4
    W, H = round(size_mm[0] * ppm), round(size_mm[1] * ppm)
    im = Image.new("RGB", (W, H), PAPER_COLORS.get(paper, PAPER_COLORS["white"]))
    d = ImageDraw.Draw(im)
    c = LINE_COLORS.get(color, LINE_COLORS["blue"])
    lw = max(1, round(0.22 * ppm))
    info: dict = {"kind": kind, "size_mm": list(size_mm), "dpi": dpi, "pitch_mm": pitch}
    if kind == "ruled":
        ys, y = [], top + pitch
        while y <= size_mm[1] - bottom + 1e-6:
            d.line([(side * ppm, y * ppm), ((size_mm[0] - side) * ppm, y * ppm)], fill=c, width=lw)
            ys.append(round(y, 3))
            y += pitch
        if margin_line:
            d.line([(margin_line * ppm, (top - 2) * ppm), (margin_line * ppm, (size_mm[1] - bottom + 4) * ppm)], fill=(227, 164, 155),
                   width=max(1, round(0.35 * ppm)))
        info["lines_mm"] = ys
    elif kind in ("grid", "dots"):
        xs = list(np.arange(side, size_mm[0] - side + 1e-6, pitch))
        ys = list(np.arange(top, size_mm[1] - bottom + 1e-6, pitch))
        for x in xs:
            if kind == "grid":
                d.line([(x * ppm, top * ppm), (x * ppm, ys[-1] * ppm)], fill=c, width=max(1, lw // 2 + 1))
        for y in ys:
            if kind == "grid":
                d.line([(side * ppm, y * ppm), (xs[-1] * ppm, y * ppm)], fill=c, width=max(1, lw // 2 + 1))
            else:
                r = max(1, round(0.25 * ppm))
                for x in xs:
                    d.ellipse([x * ppm - r, y * ppm - r, x * ppm + r, y * ppm + r], fill=c)
        info["lines_mm"] = [round(float(y), 3) for y in ys]
    elif kind in ("tian", "compo"):
        cell, gap = pitch, (0 if kind == "tian" else pitch / 3)
        cols = int((size_mm[0] - 2 * side) // cell)
        x0 = (size_mm[0] - cols * cell) / 2
        y, rows = top, []
        while y + cell <= size_mm[1] - bottom + 1e-6:
            d.rectangle([x0 * ppm, y * ppm, (x0 + cols * cell) * ppm, (y + cell) * ppm], outline=c, width=lw)
            for q in range(1, cols):
                d.line([((x0 + q * cell) * ppm, y * ppm), ((x0 + q * cell) * ppm, (y + cell) * ppm)], fill=c, width=lw)
            if kind == "tian":
                dash = 0.9 * ppm
                for q in range(cols):
                    cx, cy = (x0 + q * cell + cell / 2) * ppm, (y + cell / 2) * ppm
                    for t in np.arange(0, cell * ppm, 2 * dash):
                        d.line([(cx, y * ppm + t), (cx, y * ppm + min(cell * ppm, t + dash))], fill=c, width=1)
                        d.line([((x0 + q * cell) * ppm + t, cy), ((x0 + q * cell) * ppm + min(cell * ppm, t + dash), cy)], fill=c, width=1)
            rows.append(round(y, 3))
            y += cell + gap
        info["cells"] = {"x0_mm": round(x0, 3), "cols": cols, "cell_mm": cell, "gap_mm": round(gap, 3), "rows_top_mm": rows}
    return im, info

def paper_json_for_made(info: dict, size_mm: tuple, dpi: float, margin_line: float | None, side: float, out: str,
                        row_step: int = 1) -> dict:
    """Exact paper.json for a ruled or grid paper drawn by `make` (no detection needed). On grid paper a line of text
    uses row_step squares (2 = one line per two 5 mm squares) and sits on a grid line."""
    ppm = dpi / 25.4
    W, H = round(size_mm[0] * ppm), round(size_mm[1] * ppm)
    info = dict(info)
    if info["kind"] == "grid" and row_step > 1:
        info["lines_mm"] = info["lines_mm"][row_step::row_step]
    p = info["pitch_mm"] * (row_step if info["kind"] == "grid" else 1)
    x0, x1 = side * ppm, (size_mm[0] - side) * ppm
    wx0 = (margin_line + 0.3 * p) * ppm if margin_line else x0 + max(0.3 * p, 8 - side) * ppm
    wx1 = x1 - max(0.3 * p, 8 - side) * ppm
    lines = [{"i": i + 1, "y": round(y * ppm, 1), "poly": [y * ppm, 0.0, 0.0], "x0": round(x0, 1), "x1": round(x1, 1), "write_x0": round(wx0, 1),
              "write_x1": round(wx1, 1), "written": False, "written_x1": None, "inferred": False, "regular": True, "strength": 1.0}
             for i, y in enumerate(info.get("lines_mm", []))]
    res = {"source": str(Path(out).resolve()), "analyzed_image": str(Path(out).resolve()), "image_px": [W, H], "kind": info["kind"], "tilt_deg": 0.0, "pitch_px": round(p * ppm, 3),
           "px_per_mm": round(ppm, 4), "scale_from": "exact (made by paper.py)", "pitch_mm": p,
           "margin_line_x_px": round(margin_line * ppm, 1) if margin_line else None, "right_margin_line_x_px": None,
           "write_x0_px": round(wx0, 1), "write_x1_px": round(wx1, 1), "write_width_mm": round((wx1 - wx0) / ppm, 1),
           "lines": lines, "free_lines": [l["i"] for l in lines], "written_lines": [], "first_free_line": 1 if lines else None}
    if lines:
        res["suggested_layout"] = suggest_layout(p, res["write_width_mm"], len(lines))
    return res

def cmd_make(a) -> None:
    size = parse_size_mm(a.size)
    if a.landscape:
        size = (size[1], size[0])
    im, info = make_paper(a.kind, size, a.dpi, a.pitch, a.color, a.paper, a.margin_line, a.top, a.bottom, a.side)
    save_image(im, a.out, None, dpi=a.dpi)
    out = {"out": a.out, **{k: v for k, v in info.items() if k != "lines_mm"}, "lines": len(info.get("lines_mm", []))}
    if a.json:
        if a.kind not in ("ruled", "grid"):
            note("--json is only written for ruled and grid paper; the requested PNG was saved")
        else:
            pj = paper_json_for_made(info, size, a.dpi, a.margin_line, a.side, a.out, a.row_step if a.kind == "grid" else 1)
            Path(a.json).write_text(json.dumps(pj, ensure_ascii=False, indent=1), encoding="utf-8")
            out.update({"json": a.json, "suggested_layout": pj.get("suggested_layout")})
    emit(out)

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("make")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--kind", default="ruled", choices=["ruled", "grid", "dots", "blank", "tian", "compo"])
    s.add_argument("--size", default="A4")
    s.add_argument("--landscape", action="store_true")
    s.add_argument("--pitch", type=float, default=8.0, help="line spacing / grid cell in mm")
    s.add_argument("--color", default="blue", choices=list(LINE_COLORS))
    s.add_argument("--paper", default="white", choices=list(PAPER_COLORS))
    s.add_argument("--margin-line", type=float, help="red vertical margin line at this x (mm)")
    s.add_argument("--top", type=float, default=20)
    s.add_argument("--bottom", type=float, default=25, help="bottom margin (mm) — keeps the corner free for the AI label")
    s.add_argument("--side", type=float, default=10)
    s.add_argument("--dpi", type=float, default=300)
    s.add_argument("--json", help="also write the paper geometry JSON (ruled and grid paper)")
    s.add_argument("--row-step", type=int, default=2, help="grid paper: squares per line of text in the paper.json (default 2)")
    a = ap.parse_args()
    cmd_make(a)


if __name__ == "__main__":
    main()
