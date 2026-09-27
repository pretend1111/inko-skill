#!/usr/bin/env python3
"""Synthetic test inputs with known ground truth (no network, no real user data).

    python tests/make_fixtures.py OUT_DIR

  notebook_photo.jpg   B5 ruled 8 mm, red margin line, 3 lines already written, shot at an angle on a desk
  ruled_scan.png       A4 ruled 7 mm scan, rotated 1.2°, empty
  grid_scan.png        A5 5 mm grid scan, empty
  notebook_closeup.jpg the B5 page filling the frame: no edges visible, keystone perspective + page curl, 2 lines written
  truth.json           the parameters above, for the tests
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "inko-handwriting" / "scripts"))

import numpy as np  # noqa: E402

from _common import Image, ImageDraw, cjk_font, gaussian, value_noise  # noqa: E402
from paper import _homography, make_paper  # noqa: E402


def scribble(im: Image.Image, ppm: float, lines_mm: list[float], x0_mm: float, x1_mm: float, rng) -> None:
    d = ImageDraw.Draw(im)
    f = cjk_font(int(5.2 * ppm), kai=True)
    words = ["今天的作业是整理笔记", "把第三章的公式抄一遍", "明天上午交给老师检查"]
    for y, w in zip(lines_mm, words):
        d.text((x0_mm * ppm + rng.uniform(0, 3) * ppm, (y - 0.8) * ppm), w, font=f, fill=(35, 40, 70), anchor="ls")


def camera_corners(sheet_mm, img_wh, tilt_x=16.0, tilt_y=-6.0, roll=2.0, fill=0.8) -> list:
    """Where a real phone camera (pinhole, principal point at the centre) sees the sheet's corners."""
    W, H = img_wh
    f = 0.85 * max(W, H)
    w, h = sheet_mm
    pts = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]], np.float64)
    ax, ay, az = np.radians([tilt_x, tilt_y, roll])
    Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    Ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    Rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    P = pts @ (Rz @ Ry @ Rx).T
    Z = f * h / (fill * H)                                   # distance so the sheet fills ~fill of the frame height
    P[:, 2] += Z
    return [(float(f * x / z + W / 2), float(f * y / z + H / 2)) for x, y, z in P]


def desk_photo(sheet: Image.Image, sheet_mm, out_wh=(2400, 3000), corners=None, seed=3) -> tuple[Image.Image, list]:
    rng = np.random.default_rng(seed)
    W, H = out_wh
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    wood = 118 + 18 * np.sin(x / 37.0 + 3 * value_noise(H, W, 180, rng)) + 10 * value_noise(H, W, 60, rng)
    bg = np.stack([wood * 1.0, wood * 0.78, wood * 0.58], 2)
    photo = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")
    corners = corners or camera_corners(sheet_mm, (W, H))
    # paste the sheet with a perspective: forward-map via inverse warp of the whole frame
    sw, sh = sheet.size
    coeffs = _homography(corners, [(0, 0), (sw, 0), (sw, sh), (0, sh)])  # output(photo) -> input(sheet)
    warped = sheet.transform((W, H), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC, fillcolor=(0, 0, 0))
    mask = Image.new("L", sheet.size, 255).transform((W, H), Image.PERSPECTIVE, tuple(coeffs), Image.BILINEAR, fillcolor=0)
    # soft shadow under the sheet
    sh_mask = np.asarray(mask, np.float32) / 255
    shadow = gaussian(np.roll(np.roll(sh_mask, 18, 0), 12, 1), 14)
    base = np.asarray(photo, np.float32) * (1 - 0.45 * shadow[..., None])
    m = sh_mask[..., None]
    comp = base * (1 - m) + np.asarray(warped, np.float32) * m
    light = 0.86 + 0.16 * (1 - (x / W) * 0.7 - (y / H) * 0.5)[..., None]          # lamp from the top-left
    comp = comp * light + rng.normal(0, 3.0, comp.shape)
    return Image.fromarray(np.clip(comp, 0, 255).astype(np.uint8), "RGB"), corners


def remap(src: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Bilinear sample src (H,W,3) at float coords (sx, sy)."""
    H, W = src.shape[:2]
    x0 = np.clip(np.floor(sx).astype(int), 0, W - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, H - 2)
    fx = np.clip(sx - x0, 0, 1)[..., None]
    fy = np.clip(sy - y0, 0, 1)[..., None]
    a, b = src[y0, x0], src[y0, x0 + 1]
    c, d = src[y0 + 1, x0], src[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def closeup(sheet: Image.Image, ppm: float, out_wh=(2250, 3000), seed=5) -> Image.Image:
    """The page fills the frame: keystone (top narrower) + page curl (lines bow), lamp light, sensor noise."""
    rng = np.random.default_rng(seed)
    W, H = out_wh
    src = np.asarray(sheet, np.float32)
    v, u = np.mgrid[0:H, 0:W].astype(np.float32)
    un, vn = u / W, v / H
    # visible part of the sheet (mm): x 6..170, y 14..244; top edge 8 % narrower (camera tilted back)
    squeeze = 0.08 * (1 - vn)
    xmm = 6 + (un - 0.5) / (1 - squeeze) * 164 + 82
    ymm = 14 + vn * 230 + 2.2 * np.sin(np.pi * un) * (0.4 + 0.6 * vn)       # curl: middle of each line sags up to 2.2 mm
    img = remap(src, xmm * ppm, ymm * ppm)
    light = 0.80 + 0.22 * (1 - 0.6 * un - 0.4 * vn)
    img = img * light[..., None] + rng.normal(0, 3.5, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    truth = {}
    # 1. notebook photo
    dpi = 200
    ppm = dpi / 25.4
    sheet, info = make_paper("ruled", (176, 250), dpi, 8.0, "blue", "white", 20.0, 22.0, 12.0, 0.0)
    scribble(sheet, ppm, info["lines_mm"][:3], 23.0, 160.0, rng)
    photo, corners = desk_photo(sheet, (176, 250))
    photo.save(out / "notebook_photo.jpg", quality=90)
    truth["notebook_photo"] = {"paper": "B5", "pitch_mm": 8.0, "lines": len(info["lines_mm"]), "written_lines": [1, 2, 3],
                               "margin_mm": 20.0, "corners": corners}
    # 2. rotated ruled scan
    dpi = 150
    sheet, info = make_paper("ruled", (210, 297), dpi, 7.0, "gray", "white", None, 25.0, 15.0, 8.0)
    sheet = sheet.rotate(1.2, resample=Image.BICUBIC, fillcolor=(253, 253, 250))
    sheet.save(out / "ruled_scan.png")
    truth["ruled_scan"] = {"paper": "A4", "pitch_mm": 7.0, "lines": len(info["lines_mm"]), "angle_deg": 1.2, "dpi": dpi}
    # 3. grid scan
    sheet, info = make_paper("grid", (148, 210), 150, 5.0, "green", "white", None, 10.0, 10.0, 8.0)
    sheet.save(out / "grid_scan.png")
    truth["grid_scan"] = {"paper": "A5", "pitch_mm": 5.0, "kind": "grid"}
    # 4. close-up: no sheet edges, perspective + curl
    dpi = 200
    ppm = dpi / 25.4
    sheet, info = make_paper("ruled", (176, 250), dpi, 8.0, "blue", "white", 20.0, 22.0, 12.0, 0.0)
    scribble(sheet, ppm, info["lines_mm"][:2], 23.0, 160.0, rng)
    closeup(sheet, ppm).save(out / "notebook_closeup.jpg", quality=88)
    vis = [y for y in info["lines_mm"] if 14 + 3 < y < 244 - 3]
    truth["notebook_closeup"] = {"pitch_mm": 8.0, "lines": len(vis), "written_lines": [1, 2], "curl_mm": 2.2}
    (out / "truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "files": sorted(p.name for p in out.iterdir())}))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures"))
