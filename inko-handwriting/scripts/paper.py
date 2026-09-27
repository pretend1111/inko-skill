#!/usr/bin/env python3
"""Understand the user's own paper (a scan or a phone photo) so handwriting lands exactly on its lines.

    python paper.py analyze notebook.jpg -o paper.json --overlay paper-check.png [--paper-size B5 | --line-mm 8]
        Finds the ruled lines (tilted, curved or in perspective), their spacing, the margin line, the writing area and
        which lines already have writing. Straightens a photo first when the sheet's edges are visible.
        Writes paper.json (used by compose.py) and an overlay image — SHOW THE OVERLAY TO THE USER before generating
        ("I found 28 lines, 8 mm apart; lines 1-3 already have writing — start on line 4?").

    python paper.py blanks worksheet.jpg -o blanks.json --overlay blanks-check.png [--paper-size A4]
        Empty areas of a worksheet / form (in mm and px), e.g. the space under each question for its answer.

    python paper.py rectify photo.jpg -o flat.png [--corners x1,y1,x2,y2,x3,y3,x4,y4] [--paper-size A4]
        Straighten a photo of a sheet. Auto-detects the sheet on a darker background; otherwise give the corners
        (top-left, top-right, bottom-right, bottom-left, in pixels).

    python paper.py make -o b5.png --kind ruled --size B5 --pitch 8 [--margin-line 20] [--json b5.json]
        Draw a clean paper (ruled / grid / dots / blank / tian 田字格 / compo 作文格) of any size and colour.
        With --json it also writes the exact paper.json, so compose.py needs no detection.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import json
import math
from pathlib import Path

import numpy as np

from _common import (PAPER_SIZES_MM, Image, ImageDraw, ImageFilter, _box1d, cjk_font, die, emit, gaussian, luminance, note, open_image,
                     parse_size_mm, save_image)

WORK_MAX = 2400                     # analysis resolution (long side, px)
TYPICAL_PITCH_MM = 8.0              # most CJK notebooks; US college ruled ≈ 7.1, wide ruled ≈ 8.7, Japanese B罫 6


# ── geometry ────────────────────────────────────────────────────────────────

def _homography(src: list, dst: list) -> np.ndarray:
    """8 coefficients mapping src points -> dst points (Pillow PERSPECTIVE wants output -> input)."""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    return np.linalg.solve(np.array(A, np.float64), np.array(b, np.float64))


def apply_h(coeffs, x, y):
    a, b, c, d, e, f, g, h = coeffs
    w = g * x + h * y + 1
    return (a * x + b * y + c) / w, (d * x + e * y + f) / w


def warp_quad(img: Image.Image, corners: list, out_w: int, out_h: int) -> Image.Image:
    """Map the quad (TL, TR, BR, BL) of img onto an out_w x out_h rectangle."""
    coeffs = _homography([(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)], corners)
    return img.transform((out_w, out_h), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


def _order_corners(pts: np.ndarray) -> list:
    c = pts.mean(0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]
    pts = np.roll(pts, -int(np.argmin(pts.sum(1))), axis=0)
    return [(float(x), float(y)) for x, y in pts]


def find_sheet(img: Image.Image) -> list | None:
    """Corners (TL, TR, BR, BL) of a bright sheet on a darker background, or None if the sheet fills the frame."""
    small = img.convert("L")
    k = min(1.0, 900 / max(small.size))
    if k < 1:
        small = small.resize((max(1, int(small.width * k)), max(1, int(small.height * k))), Image.BILINEAR)
    g = gaussian(np.asarray(small, np.float32), 2.0)
    try:
        import cv2  # optional: more robust contour finding
        th = cv2.threshold(np.clip(g, 0, 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        c = max(cnts, key=cv2.contourArea)
        area = cv2.contourArea(c) / th.size
        if area < 0.2 or area > 0.995:
            return None
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        pts = approx.reshape(-1, 2).astype(np.float64) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float64)
    except ImportError:
        hist = np.histogram(g, 256, (0, 256))[0].astype(np.float64)       # Otsu by hand
        w = np.cumsum(hist)
        mu = np.cumsum(hist * np.arange(256))
        with np.errstate(divide="ignore", invalid="ignore"):
            between = (mu[-1] * w - mu * w[-1]) ** 2 / (w * (w[-1] - w))
        m = g > int(np.nanargmax(between))
        frac = m.mean()
        border = min(m[0].mean(), m[-1].mean(), m[:, 0].mean(), m[:, -1].mean())
        if frac < 0.2 or frac > 0.995 or border > 0.9:
            return None
        ys, xs = np.nonzero(m)
        s, d = xs + ys, xs - ys
        pts = np.array([[xs[s.argmin()], ys[s.argmin()]], [xs[d.argmax()], ys[d.argmax()]],
                        [xs[s.argmax()], ys[s.argmax()]], [xs[d.argmin()], ys[d.argmin()]]], np.float64)
    corners = _order_corners(pts)
    # a real sheet: its quad is filled by bright pixels, little bright area leaks outside, and it clearly
    # stands out from the background (a lighting gradient across a close-up page must not count)
    q = Image.new("L", (g.shape[1], g.shape[0]), 0)
    ImageDraw.Draw(q).polygon([tuple(c) for c in corners], fill=255)
    inside = np.asarray(q) > 0
    bright = g > (np.median(g[inside]) + np.median(g[~inside])) / 2 if (~inside).any() else None
    if bright is None or inside.mean() > 0.995:
        return None
    fill = (bright & inside).sum() / max(1, inside.sum())
    leak = (bright & ~inside).sum() / max(1, inside.sum())
    contrast = float(np.median(g[inside]) - np.median(g[~inside]))
    big = inside.mean() > 0.9                                 # a sheet filling most of the frame (scan-like shot)
    if fill < (0.96 if big else 0.9) or leak > (0.02 if big else 0.06) or contrast < (38 if big else 45):
        return None
    return [(x / k, y / k) for x, y in corners]


STD_ASPECTS = {"√2 (A4, A5, B5, 16K …)": math.sqrt(2), "US Letter": 279.4 / 215.9}


def true_aspect(corners: list, img_wh: tuple[int, int]) -> float | None:
    """Height/width of the real rectangle seen as this quad (Zhang & He, whiteboard rectification; principal point = centre)."""
    u0, v0 = img_wh[0] / 2, img_wh[1] / 2
    tl, tr, br, bl = [np.array([x, y, 1.0]) for x, y in corners]
    m1, m2, m3, m4 = tl, tr, bl, br
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    except ZeroDivisionError:
        return None
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    if abs(n2[2]) < 1e-9 or abs(n3[2]) < 1e-9:              # sides parallel in the photo: no perspective to undo
        wh = math.sqrt((n2[0] ** 2 + n2[1] ** 2) / (n3[0] ** 2 + n3[1] ** 2))
        return 1 / wh
    f2 = -((n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 * u0)
           + (n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0 + n2[2] * n3[2] * v0 * v0)) / (n2[2] * n3[2])
    if not f2 > 0:
        wh = math.sqrt((n2[0] ** 2 + n2[1] ** 2) / (n3[0] ** 2 + n3[1] ** 2))
        return 1 / wh
    A = np.array([[math.sqrt(f2), 0, u0], [0, math.sqrt(f2), v0], [0, 0, 1]])
    Ai = np.linalg.inv(A)
    M = Ai.T @ Ai
    wh = math.sqrt(float(n2 @ M @ n2) / float(n3 @ M @ n3))
    return 1 / wh if wh > 0 else None


def rectify_size(corners: list, paper_mm: tuple | None, img_wh: tuple[int, int] | None = None) -> tuple[int, int, str]:
    """Output size for the straightened sheet: the known paper ratio, else the ratio recovered from the perspective."""
    wtop = max(math.dist(corners[0], corners[1]), math.dist(corners[3], corners[2]))
    if paper_mm:
        return int(wtop), int(wtop * paper_mm[1] / paper_mm[0]), "paper size"
    r = true_aspect(corners, img_wh) if img_wh else None
    how = "estimated from the perspective"
    if r and 0.3 < r < 4:
        snap = [(name, c) for name, std in STD_ASPECTS.items() for c in (std, 1 / std) if abs(r / c - 1) < 0.035]
        if snap:                                             # snap to a standard sheet ratio when close
            how, r = f"estimated ≈ {snap[0][0]}", snap[0][1]
    else:
        r = max(math.dist(corners[0], corners[3]), math.dist(corners[1], corners[2])) / wtop
        how = "photo side lengths (uncertain)"
    return int(wtop), int(wtop * r), how


def _rot_affine(W: int, H: int, deg: float) -> tuple:
    """Affine coefficients: output (x, y) samples the input at (x, y) rotated by deg about the centre.
    Lines that descend to the right by `deg` in the input come out horizontal."""
    p = math.radians(deg)
    c, s = math.cos(p), math.sin(p)
    cx, cy = W / 2, H / 2
    return (c, -s, cx - cx * c + cy * s, s, c, cy - cx * s - cy * c)


def _rot_img(a: np.ndarray, deg: float) -> np.ndarray:
    if abs(deg) < 1e-3:
        return a
    H, W = a.shape
    fill = float(np.median(a))                              # corners outside the image: neutral, not black
    im = Image.fromarray(a.astype(np.float32), "F").transform((W, H), Image.AFFINE, _rot_affine(W, H, deg), Image.BILINEAR, fillcolor=fill)
    return np.asarray(im, np.float32)


def _unrot(x, y, W: int, H: int, deg: float):
    """De-tilted frame -> original frame."""
    a, b, c, d, e, f = _rot_affine(W, H, deg)
    return a * x + b * y + c, d * x + e * y + f


# ── line responses ──────────────────────────────────────────────────────────

def valley(L: np.ndarray, axis: int, ks=(1, 2, 3, 4, 6)) -> np.ndarray:
    """Thin dark line response across `axis` (0 = horizontal lines): how much darker a pixel is than both neighbours k px away."""
    out = np.zeros_like(L)
    n = L.shape[axis]
    for k in ks:
        pad = [(0, 0), (0, 0)]
        pad[axis] = (k, k)
        p = np.pad(L, pad, mode="edge")
        lo = np.take(p, np.arange(0, n), axis=axis)
        hi = np.take(p, np.arange(2 * k, 2 * k + n), axis=axis)
        np.maximum(out, np.minimum(lo, hi) - L, out=out)
    return out


def _fundamental(ac: np.ndarray, lo: int, hi: int) -> float:
    """Period of a line pattern from its autocorrelation, preferring the fundamental over its multiples."""
    if hi <= lo + 2:
        return 0.0
    best = lo + int(np.argmax(ac[lo:hi]))
    top = ac[best]
    for n in (5, 4, 3, 2):
        c = best / n
        if c < lo:
            continue
        r = max(1, int(round(0.12 * c)))
        a0, a1 = max(1, int(round(c)) - r), min(len(ac) - 1, int(round(c)) + r + 1)
        j = a0 + int(np.argmax(ac[a0:a1]))
        if ac[j] >= 0.45 * top and ac[j] >= ac[j - 1] and ac[j] >= ac[j + 1]:
            best = j
            break
    y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
    den = y0 - 2 * y1 + y2
    return float(best + (0.5 * (y0 - y2) / den if den else 0.0))


def _peaks(prof: np.ndarray, thr: float, min_dist: float) -> list[int]:
    idx = np.flatnonzero((prof[1:-1] >= prof[:-2]) & (prof[1:-1] > prof[2:]) & (prof[1:-1] > thr)) + 1
    idx = sorted(idx, key=lambda i: -prof[i])
    taken: list[int] = []
    for i in idx:
        if all(abs(i - j) >= min_dist for j in taken):
            taken.append(int(i))
    return sorted(taken)


def _fit(xs: np.ndarray, ys: np.ndarray, deg: int, tol: float) -> np.ndarray:
    deg = min(deg, len(xs) - 1)
    keep = np.ones(len(xs), bool)
    c = np.polyfit(xs, ys, deg)
    for _ in range(3):
        r = np.abs(np.polyval(c, xs) - ys)
        k2 = r <= max(tol, 2.5 * float(np.median(r[keep])))
        if k2.sum() < deg + 2 or (k2 == keep).all():
            break
        keep = k2
        c = np.polyfit(xs[keep], ys[keep], deg)
    return np.pad(c, (3 - len(c), 0))                       # always quadratic form [c2, c1, c0]


# ── analysis ────────────────────────────────────────────────────────────────

def detect_lines(Lw: np.ndarray) -> dict:
    """Horizontal ruled lines in a (work-resolution) luminance image. Coordinates are in the de-tilted frame."""
    H, W = Lw.shape
    Lh = _box1d(Lw, max(1, round(W / 500)), 1)
    # 1) tilt: maximise the variance of the row profile of the line response (low resolution)
    k = min(1.0, 1000 / max(H, W))
    small = np.asarray(Image.fromarray(Lh, "F").resize((max(8, int(W * k)), max(8, int(H * k))), Image.BILINEAR), np.float32)
    Rs = valley(small, 0, (1, 2, 3))

    def score(deg):
        r = _rot_img(Rs, deg)
        w = r.shape[1]
        return float(np.var(r[:, int(w * .15):int(w * .85)].mean(1)))
    angs = np.arange(-8, 8.01, 0.5)
    a0 = float(angs[int(np.argmax([score(a) for a in angs]))])
    fine = np.arange(a0 - 0.5, a0 + 0.51, 0.05)
    tilt = float(fine[int(np.argmax([score(a) for a in fine]))])
    # 2) line response in the de-tilted frame, per vertical strip (median = robust to handwriting)
    Lr = _rot_img(Lh, tilt)
    R = valley(Lr, 0)
    n_s = int(np.clip(W / 80, 10, 40))
    edges = np.linspace(W * 0.02, W * 0.98, n_s + 1).astype(int)
    xc = (edges[:-1] + edges[1:]) / 2
    P = np.stack([gaussian(np.median(R[:, edges[i]:edges[i + 1]], axis=1)[None, :], 0.7)[0] for i in range(n_s)], 1)
    # 3) pitch from the strips' summed autocorrelation
    Pc = P - P.mean(0)
    F = np.fft.rfft(Pc, n=2 * H, axis=0)
    ac = np.fft.irfft(F * np.conj(F), axis=0)[:H].sum(1)
    lo, hi = max(5, int(H / 250)), int(H / 5)
    pitch = _fundamental(ac, lo, hi)
    periodic = float(ac[int(round(pitch))] / ac[0]) if pitch and ac[0] > 0 else 0.0
    out = {"tilt_deg": tilt, "pitch": pitch, "periodic": periodic, "lines": [], "R": R, "Lr": Lr, "xc": xc}
    if not pitch or periodic < 0.08:
        return out
    # 4) peaks per strip, linked across strips into lines (follows tilt changes, curl and perspective)
    peaks = []
    for i in range(n_s):
        pr = P[:, i]
        cand = _peaks(pr, 0, 0.55 * pitch)
        vals = np.array([pr[j] for j in cand]) if cand else np.zeros(0)
        thr = 0.3 * np.percentile(vals, 90) if len(vals) else 1e9
        peaks.append([j for j in cand if pr[j] >= max(thr, 1.0)])
    claimed = [set() for _ in range(n_s)]
    tracks = []
    for s0 in sorted(range(n_s), key=lambda i: abs(i - n_s / 2)):
        for y0 in peaks[s0]:
            if y0 in claimed[s0]:
                continue
            tr = {s0: y0}
            claimed[s0].add(y0)
            for step in (1, -1):
                s, miss, last = s0, 0, [(s0, y0)]
                while 0 <= s + step < n_s and miss <= 3:
                    s += step
                    slope = (last[-1][1] - last[-2][1]) / (last[-1][0] - last[-2][0]) if len(last) >= 2 else 0.0
                    pred = last[-1][1] + slope * (s - last[-1][0])
                    near = [y for y in peaks[s] if abs(y - pred) <= 0.3 * pitch and y not in claimed[s]]
                    if near:
                        y = min(near, key=lambda y: abs(y - pred))
                        tr[s] = y
                        claimed[s].add(y)
                        last.append((s, y))
                        miss = 0
                    else:
                        miss += 1
            if len(tr) >= max(3, int(0.3 * n_s)):
                tracks.append(tr)
    lines = []
    for tr in tracks:
        ss = np.array(sorted(tr))
        xs, ys = xc[ss], np.array([tr[s] for s in ss], np.float64)
        c = _fit(xs, ys, 2 if len(xs) >= 6 else 1, max(1.5, 0.08 * pitch))
        lines.append({"c": c, "strength": float(np.median([P[tr[s], s] for s in ss])), "cover": len(ss) / n_s})
    lines.sort(key=lambda l: np.polyval(l["c"], W / 2))
    # 5) drop near-duplicates, then fill gaps where a line is hidden (heavy writing, a fold, glare)
    ded: list = []
    for l in lines:
        if ded and np.polyval(l["c"], W / 2) - np.polyval(ded[-1]["c"], W / 2) < 0.6 * pitch:
            if l["strength"] * l["cover"] > ded[-1]["strength"] * ded[-1]["cover"]:
                ded[-1] = l
            continue
        ded.append(l)
    filled: list = []
    for l in ded:
        if filled:
            prev = filled[-1]
            gap = np.polyval(l["c"], W / 2) - np.polyval(prev["c"], W / 2)
            n = int(round(gap / pitch))
            if 2 <= n <= 4 and abs(gap / pitch - n) < 0.2:
                for j in range(1, n):
                    t = j / n
                    filled.append({"c": (1 - t) * prev["c"] + t * l["c"], "strength": 0.0, "cover": 0.0, "inferred": True})
        filled.append(l)
    # 6) lines off the regular spacing (a header rule, the page edge) are flagged, not used for writing
    ys = np.array([np.polyval(l["c"], W / 2) for l in filled])
    for i, l in enumerate(filled):
        gaps = [abs(ys[i] - ys[j]) for j in (i - 1, i + 1) if 0 <= j < len(ys)]
        l["regular"] = any(abs(g / pitch - round(g / pitch)) < 0.2 and round(g / pitch) >= 1 for g in gaps)
    out["lines"] = filled
    return out


def detect_vlines(Lr: np.ndarray, pitch: float) -> dict:
    """Vertical lines (margin line, grid) in the de-tilted frame."""
    H, W = Lr.shape
    Rv = valley(_box1d(Lr, max(1, round(H / 500)), 0), 1)
    bands = [(0.12, 0.32), (0.32, 0.52), (0.52, 0.72), (0.72, 0.92)]
    Q = np.stack([gaussian(np.median(Rv[int(H * a):int(H * b)], axis=0)[None, :], 0.7)[0] for a, b in bands])
    cand = []
    for q in Q:
        pk = _peaks(q, 0, max(4, 0.3 * pitch) if pitch else 6)
        vals = np.array([q[j] for j in pk]) if pk else np.zeros(0)
        thr = max(2.0, 0.3 * np.percentile(vals, 95)) if len(vals) else 1e9
        cand.append([j for j in pk if q[j] >= thr])
    tol = max(3, int(0.006 * W))
    found: list[int] = []
    for x in cand[1] + cand[2]:
        hits = [min((abs(x - y) for y in c), default=1e9) <= tol for c in cand]
        if sum(hits) >= 3 and all(abs(x - q) > tol for q in found):
            found.append(x)
    found.sort()
    strength = {x: float(np.mean([q[x] for q in Q])) for x in found}
    grid = False
    if len(found) >= 6:
        d = np.diff(found)
        grid = bool(np.std(d) < 0.2 * np.median(d) and (not pitch or 0.6 < np.median(d) / pitch < 1.7))
    return {"xs": found, "strength": strength, "grid": grid}


def analyze(img: Image.Image, line_mm: float | None, paper_mm: tuple | None) -> dict:
    W0, H0 = img.size
    f = min(1.0, WORK_MAX / max(W0, H0))
    work = img.convert("RGB")
    if f < 1:
        work = work.resize((max(8, round(W0 * f)), max(8, round(H0 * f))), Image.LANCZOS)
    Lw = luminance(np.asarray(work, np.float32))
    H, W = Lw.shape
    det = detect_lines(Lw)
    tilt, pitch, lines = det["tilt_deg"], det["pitch"], det["lines"]
    Lr, R = det["Lr"], det["R"]
    reg = [l for l in lines if l.get("regular")]
    kind = "ruled" if len(reg) >= 4 else "blank"
    vl = detect_vlines(Lr, pitch) if kind == "ruled" else {"xs": [], "strength": {}, "grid": False}
    if vl["grid"]:
        kind = "grid"
    line_str = float(np.median([l["strength"] for l in reg if l["strength"] > 0])) if reg else 0.0
    left_m = right_m = None                                           # margin lines: strongest vertical line left / right
    if kind == "ruled":
        strong = [x for x in vl["xs"] if vl["strength"][x] >= 0.3 * line_str]
        lefts = [x for x in strong if 0.02 * W < x < 0.42 * W]
        rights = [x for x in strong if 0.58 * W < x < 0.98 * W]
        left_m = max(lefts, key=lambda x: vl["strength"][x]) if lefts else None
        right_m = max(rights, key=lambda x: vl["strength"][x]) if rights else None
    # existing writing: clearly darker than the printed lines, and not on them
    small = Image.fromarray(np.clip(Lr, 0, 255).astype(np.uint8)).resize((max(4, W // 4), max(4, H // 4)), Image.BILINEAR)
    msz = int(max(3, (0.8 * pitch / 4) if pitch else 5)) | 1
    bg = gaussian(np.asarray(small.filter(ImageFilter.MaxFilter(msz)).resize((W, H), Image.BILINEAR), np.float32),
                  max(2.0, 0.3 * pitch if pitch else 4))
    depth = bg - Lr
    cols = np.arange(W)
    samples = []
    for l in reg:
        for x in det["xc"][::2]:
            y = int(round(np.polyval(l["c"], x)))
            if 0 <= y < H:
                samples.append(R[y, int(x)])
    d_line = float(np.median(samples)) if samples else 40.0
    ink = depth > max(28.0, 0.7 * d_line + 12)
    struct = np.zeros_like(ink)
    t = max(2, int(round(0.1 * pitch))) if pitch else 2
    for l in lines:
        yy = np.polyval(l["c"], cols)
        for dy in range(-t, t + 1):
            struct[np.clip(np.round(yy + dy).astype(int), 0, H - 1), cols] = True
    for x in vl["xs"]:
        struct[:, max(0, x - t):x + t + 1] = True
    ink &= ~struct | (depth > 1.8 * d_line + 25)                     # a pen stroke crossing a printed line still counts
    ys_c = [float(np.polyval(l["c"], W / 2)) for l in lines]
    wx0_all = (left_m + 0.3 * pitch) if left_m is not None else None
    wx1_all = (right_m - 0.3 * pitch) if right_m is not None else None
    per_line, inks, sits = [], [], []
    wrgb = np.asarray(work, np.float32)
    rgb_r = np.stack([_rot_img(wrgb[..., ch], tilt) for ch in range(3)], 2)
    for i, l in enumerate(lines):
        yy = np.polyval(l["c"], cols)
        yi = np.clip(np.round(yy).astype(int), 1, H - 2)
        r = np.maximum(np.maximum(R[yi - 1, cols], R[yi, cols]), R[yi + 1, cols])
        r = _box1d(r[None, :].astype(np.float32), max(2, W // 100), 1)[0]
        lvl = float(np.median(r[int(W * .25):int(W * .75)]))
        on = np.flatnonzero(r > 0.3 * lvl) if lvl > 0 else np.array([], int)
        x0, x1 = (int(on[0]), int(on[-1])) if len(on) else (int(W * .05), int(W * .95))
        lx0 = wx0_all if wx0_all is not None else x0 + (0.3 * pitch if x0 > 0.03 * W else 0.06 * W)
        lx1 = wx1_all if wx1_all is not None else x1 - (0.3 * pitch if x1 < 0.97 * W else 0.05 * W)
        top = yy - (ys_c[i] - ys_c[i - 1] if i > 0 else pitch) + 0.1 * pitch
        bot = yy - 0.06 * pitch
        a, b = int(max(0, np.floor(top.min()))), int(min(H, np.ceil(bot.max()) + 1))
        rows = np.arange(a, b)[:, None]
        band = ink[a:b] & (rows >= top[None, :]) & (rows <= bot[None, :])
        band[:, :max(0, int(lx0 - 0.25 * pitch))] = False
        hit = np.flatnonzero(band.any(0))
        written = len(hit) >= 0.35 * pitch and band.sum() >= 0.12 * pitch * pitch
        if written:                                                  # how the existing writing sits: height, lift, ink
            by, bx = np.nonzero(band)
            above = yy[bx] - (a + by)                                # px above the line
            lo_, hi_ = np.percentile(above, 6), np.percentile(above, 96)
            core = band & (depth[a:b] > max(45.0, 1.4 * d_line + 15))
            px = rgb_r[a:b][core]
            pap = rgb_r[a:b][~band & ~struct[a:b] & (depth[a:b] < 10)]
            if len(px) > 30 and len(pap) > 30:
                inks.append(np.median(px, 0) / np.maximum(1.0, np.median(pap, 0)) * 255.0)
            sits.append((lo_ / pitch, (hi_ - lo_) / pitch))
        per_line.append({"c": l["c"], "x0": x0, "x1": x1, "strength": l["strength"], "inferred": bool(l.get("inferred")),
                         "regular": bool(l.get("regular")), "written": bool(written), "wend": int(hit[-1]) if written else None,
                         "wx0": lx0, "wx1": lx1})
    # back to the analysed image's own pixels (undo the tilt and the downscale), refit each line there
    res_lines = []
    for n, l in enumerate(per_line):
        xs = np.linspace(l["x0"], l["x1"], 24)
        xo, yo = _unrot(xs, np.polyval(l["c"], xs), W, H, tilt)
        c = np.polyfit(xo / f, yo / f, 2)

        def orig_x(xr, c_r=l["c"]):
            if xr is None:
                return None
            return round(float(_unrot(xr, np.polyval(c_r, xr), W, H, tilt)[0] / f), 1)
        res_lines.append({"i": n + 1, "y": round(float(np.polyval(c, W0 / 2)), 1), "poly": [float(v) for v in c[::-1]],
                          "x0": orig_x(l["x0"]), "x1": orig_x(l["x1"]), "write_x0": orig_x(l["wx0"]), "write_x1": orig_x(l["wx1"]),
                          "written": l["written"], "written_x1": orig_x(l["wend"]), "inferred": l["inferred"],
                          "regular": l["regular"], "strength": round(l["strength"], 2)})
    pitch_px = pitch / f if pitch else None
    regs = [l["y"] for l in res_lines if l["regular"]]
    if len(regs) >= 4 and pitch_px:                         # refine: straight-line fit of position vs. line number
        idx = np.round((np.array(regs) - regs[0]) / pitch_px)
        pitch_px = float(np.polyfit(idx, regs, 1)[0])
    if paper_mm:
        ppm, how = W0 / paper_mm[0], "paper size"
    elif line_mm and pitch_px:
        ppm, how = pitch_px / line_mm, "line spacing you gave"
    elif pitch_px:
        ppm, how = pitch_px / TYPICAL_PITCH_MM, f"ASSUMED {TYPICAL_PITCH_MM:g} mm line spacing (ask the user, or pass --paper-size / --line-mm)"
    else:
        ppm, how = W0 / 210.0, "ASSUMED A4 width"
    usable = [l for l in res_lines if l["regular"] or l["inferred"]]
    free = [l["i"] for l in usable if not l["written"]]
    wx0s = [l["write_x0"] for l in usable if l["write_x0"] is not None]
    wx1s = [l["write_x1"] for l in usable if l["write_x1"] is not None]
    wx0 = float(np.median(wx0s)) if wx0s else W0 * 0.1
    wx1 = float(np.median(wx1s)) if wx1s else W0 * 0.9
    res = {"image_px": [W0, H0], "kind": kind, "tilt_deg": round(tilt, 2), "periodicity": round(det["periodic"], 3),
           "pitch_px": round(pitch_px, 2) if pitch_px else None, "px_per_mm": round(ppm, 4), "scale_from": how,
           "pitch_mm": round(pitch_px / ppm, 2) if pitch_px else None,
           "margin_line_x_px": round(float(_unrot(left_m, H / 2, W, H, tilt)[0] / f), 1) if left_m is not None else None,
           "right_margin_line_x_px": round(float(_unrot(right_m, H / 2, W, H, tilt)[0] / f), 1) if right_m is not None else None,
           "write_x0_px": round(wx0, 1), "write_x1_px": round(wx1, 1), "write_width_mm": round((wx1 - wx0) / ppm, 1),
           "lines": res_lines, "free_lines": free, "written_lines": [l["i"] for l in usable if l["written"]],
           "first_free_line": free[0] if free else None}
    if kind == "grid":
        res["grid_x_px"] = [round(float(_unrot(x, H / 2, W, H, tilt)[0] / f), 1) for x in vl["xs"]]
    if sits:                                                   # the writing already on the page — compose.py can match it
        res["existing_writing"] = {"lines": res["written_lines"],
                                   "lift": round(float(np.median([s[0] for s in sits])), 3),
                                   "height": round(float(np.median([s[1] for s in sits])), 3),
                                   "ink_rgb": [int(v) for v in np.clip(np.median(inks, 0), 0, 255)] if inks else None,
                                   "note": "lift/height in line spacings; ink_rgb = the pen colour as it appears on this paper"}
    res["sheet_mm"] = [round(W0 / ppm, 1), round(H0 / ppm, 1)]
    if how.startswith("ASSUMED") and pitch_px:                 # whole sheet visible? then its size checks the assumption
        for name, (sw, sh) in PAPER_SIZES_MM.items():
            if abs(W0 / ppm / sw - 1) < 0.035 and abs(H0 / ppm / sh - 1) < 0.035:
                res["scale_from"] = f"assumed {TYPICAL_PITCH_MM:g} mm lines — consistent with a whole {name} sheet ({sw}×{sh} mm)"
                break
    if pitch_px and kind == "ruled":
        res["suggested_layout"] = suggest_layout(res["pitch_mm"], res["write_width_mm"], len(free))
    return res


def suggest_layout(pitch_mm: float, width_mm: float, n_free: int) -> dict:
    """Generate on Inko's blank A4 with the same proportions as this paper; compose.py maps every line back."""
    k = min(1.0, 186.0 / max(1.0, width_mm))              # keep the line inside an A4 frame (scaled back when composing)
    p = pitch_mm * k
    size = round(0.62 * p, 2)                              # Inko's own ruled papers write at 0.62 x line spacing
    w = round(width_mm * k, 1)
    cpl = int(w // (size * 1.06))
    return {"paperId": "blank", "d": {"size": size, "line": round(p / size, 4), "margins": [22, round(210 - 20 - w, 1), 26, 20], "indent": 2},
            "scale": round(k, 4), "chars_per_line_est": cpl, "lines_per_generated_page": int((297 - 22 - 26) // p),
            "free_lines": n_free, "capacity_chars_est": cpl * n_free}


def overlay(img: Image.Image, a: dict) -> Image.Image:
    im = img.convert("RGB")
    k = min(1.0, 1800 / max(im.size))
    if k < 1:
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    im = Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.3)
    d = ImageDraw.Draw(im)
    font = cjk_font(max(12, int(im.width / 70)))
    for ln in a["lines"]:
        c = ln["poly"]
        xs = np.linspace(ln["x0"], ln["x1"], 30)
        pts = [(float(x * k), float((c[0] + c[1] * x + c[2] * x * x) * k)) for x in xs]
        usable = ln["regular"] or ln["inferred"]
        col = (150, 150, 150) if not usable else (220, 50, 40) if ln["written"] else (0, 160, 70)
        d.line(pts, fill=col, width=3 if usable else 1)
        d.text((max(2, pts[0][0] - font.size * 1.6), pts[0][1] - font.size * 0.9), str(ln["i"]), fill=col, font=font)
    for key in ("write_x0_px", "write_x1_px"):
        x = a[key] * k
        d.line([(x, 0), (x, im.height)], fill=(40, 90, 230), width=2)
    txt = (f"{len(a['lines'])} lines · {a.get('pitch_mm')} mm apart ({a['scale_from'].split(' (')[0]}) · "
           f"green = free · red = has writing · gray = not used · blue = writing area")
    d.rectangle([0, 0, im.width, font.size * 1.8], fill=(255, 255, 255))
    d.text((8, 4), txt, fill=(20, 20, 20), font=font)
    return im


# ── blank areas of a worksheet ──────────────────────────────────────────────

def blanks(img: Image.Image, ppm: float, min_h_mm: float) -> list[dict]:
    W0, H0 = img.size
    f = min(1.0, 1600 / max(W0, H0))
    L = luminance(np.asarray(img.convert("RGB").resize((max(8, int(W0 * f)), max(8, int(H0 * f))), Image.BILINEAR), np.float32))
    H, W = L.shape
    bg = gaussian(np.asarray(Image.fromarray(np.clip(L, 0, 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9)), np.float32), 8)
    occ = (bg - L) > 38
    pm = ppm * f
    grow = max(1, int(1.2 * pm))
    occ = np.asarray(Image.fromarray((occ * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * grow + 1)), np.uint8) > 0
    ys, xs = np.nonzero(occ)
    if not len(xs):
        return [{"n": 1, "x_px": 0, "y_px": 0, "w_px": W0, "h_px": H0, "x_mm": 0, "y_mm": 0, "w_mm": round(W0 / ppm, 1), "h_mm": round(H0 / ppm, 1)}]
    inner = (xs > 0.03 * W) & (xs < 0.97 * W) & (ys > 0.02 * H) & (ys < 0.98 * H)   # ignore scraps of border at the edges
    if inner.any():
        cx0, cx1 = int(xs[inner].min()), int(xs[inner].max())
    else:
        cx0, cx1 = int(0.04 * W), int(0.96 * W)
    free = occ[:, cx0:cx1].mean(1) < 0.004
    runs, y = [], 0
    while y < H:
        if free[y]:
            y1 = y
            while y1 < H and free[y1]:
                y1 += 1
            if y1 - y >= min_h_mm * pm and y > 0.02 * H and y1 < 0.985 * H:
                runs.append((y, y1))
            y = y1
        else:
            y += 1
    res = []
    for n, (a, b) in enumerate(runs):
        pad = 1.5 * pm
        x0, x1, y0, y1 = cx0 / f, cx1 / f, (a + pad) / f, (b - pad) / f
        res.append({"n": n + 1, "x_px": round(x0), "y_px": round(y0), "w_px": round(x1 - x0), "h_px": round(y1 - y0),
                    "x_mm": round(x0 / ppm, 1), "y_mm": round(y0 / ppm, 1), "w_mm": round((x1 - x0) / ppm, 1), "h_mm": round((y1 - y0) / ppm, 1),
                    # where an answer box looks natural: a little in from the question's left edge, clear of the text above
                    "suggested_box_mm": [round(x0 / ppm + 4, 1), round(y0 / ppm + 2, 1), round((x1 - x0) / ppm - 8, 1),
                                         round((y1 - y0) / ppm - 4, 1)]})
    return res


# ── paper maker ─────────────────────────────────────────────────────────────

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


# ── commands ────────────────────────────────────────────────────────────────

def _corners_arg(s: str) -> list:
    v = [float(t) for t in s.split(",")]
    if len(v) != 8:
        die("--corners needs 8 numbers: x1,y1,x2,y2,x3,y3,x4,y4 (TL, TR, BR, BL)")
    return [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]


def _prepare(a) -> tuple[Image.Image, dict]:
    """Open the image; straighten it if the sheet's edges are visible. Returns (image to analyse, info for the JSON)."""
    img, meta = open_image(a.image)
    info: dict = {"source": str(Path(a.image).resolve()), "analyzed_image": str(Path(a.image).resolve())}
    paper_mm = parse_size_mm(a.paper_size) if getattr(a, "paper_size", None) else None
    corners = _corners_arg(a.corners) if getattr(a, "corners", None) else None if getattr(a, "no_rectify", False) else find_sheet(img)
    if corners:
        ow, oh, how = rectify_size(corners, paper_mm, img.size)
        flat = warp_quad(img, corners, ow, oh)
        fp = Path(a.out).with_name(Path(a.out).stem + "-flat.png")
        save_image(flat, fp, meta)
        rect = [(0, 0), (ow, 0), (ow, oh), (0, oh)]
        info.update({"analyzed_image": str(fp.resolve()), "source_px": list(img.size), "corners": [[round(x, 1), round(y, 1)] for x, y in corners],
                     "sheet_ratio_from": how,
                     "flat_to_source": [float(v) for v in _homography(rect, corners)],
                     "source_to_flat": [float(v) for v in _homography(corners, rect)]})
        note(f"sheet found and straightened -> {fp}")
        img = flat
    return img, info


def cmd_analyze(a) -> None:
    img, info = _prepare(a)
    res = {**info, **analyze(img, a.line_mm, parse_size_mm(a.paper_size) if a.paper_size else None)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.overlay:
        overlay(img, res).save(a.overlay)
    summary = {k: res.get(k) for k in ("analyzed_image", "kind", "pitch_mm", "px_per_mm", "scale_from", "tilt_deg", "write_width_mm",
                                       "first_free_line", "written_lines")}
    summary.update({"lines": len(res["lines"]), "free_lines": len(res["free_lines"]), "suggested_layout": res.get("suggested_layout"),
                    "json": a.out, "overlay": a.overlay})
    summary["next"] = ("No ruled lines found. Use compose.py page/place for free placement, or pass --line-mm if the lines are very faint."
                       if res["kind"] != "ruled" else
                       "Show the overlay to the user; confirm line spacing, start line and pen, then generate with the suggested layout.")
    emit(summary)


def cmd_blanks(a) -> None:
    img, info = _prepare(a)
    paper_mm = parse_size_mm(a.paper_size) if a.paper_size else None
    ppm = img.width / paper_mm[0] if paper_mm else img.width / 210.0
    bl = blanks(img, ppm, a.min_height)
    res = {**info, "image_px": list(img.size), "px_per_mm": round(ppm, 4), "scale_from": "paper size" if paper_mm else "ASSUMED A4 width",
           "page_mm": [round(img.width / ppm, 1), round(img.height / ppm, 1)], "blanks": bl}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.overlay:
        k = min(1.0, 1600 / max(img.size))
        im = img.convert("RGB").resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        font = cjk_font(max(14, int(im.width / 50)))
        for b in bl:
            d.rectangle([b["x_px"] * k, b["y_px"] * k, (b["x_px"] + b["w_px"]) * k, (b["y_px"] + b["h_px"]) * k], outline=(0, 150, 70), width=3)
            d.text((b["x_px"] * k + 6, b["y_px"] * k + 4), f"#{b['n']}  {b['w_mm']}×{b['h_mm']} mm", fill=(0, 120, 60), font=font)
        im.save(a.overlay)
    emit({"blanks": bl, "page_mm": res["page_mm"], "scale_from": res["scale_from"], "json": a.out, "overlay": a.overlay,
          "next": "Match blanks to questions with the user, then put answer boxes at these mm positions (references/paper-matching.md)."})


def cmd_rectify(a) -> None:
    img, meta = open_image(a.image)
    corners = _corners_arg(a.corners) if a.corners else find_sheet(img)
    if not corners:
        die("could not find the sheet's edges automatically; pass --corners (TL,TR,BR,BL in pixels)")
    ow, oh, how = rectify_size(corners, parse_size_mm(a.paper_size) if a.paper_size else None, img.size)
    save_image(warp_quad(img, corners, ow, oh), a.out, meta)
    emit({"out": a.out, "corners": [[round(x, 1), round(y, 1)] for x, y in corners], "size_px": [ow, oh], "ratio_from": how})


def cmd_make(a) -> None:
    size = parse_size_mm(a.size)
    if a.landscape:
        size = (size[1], size[0])
    im, info = make_paper(a.kind, size, a.dpi, a.pitch, a.color, a.paper, a.margin_line, a.top, a.bottom, a.side)
    save_image(im, a.out, None, dpi=a.dpi)
    out = {"out": a.out, **{k: v for k, v in info.items() if k != "lines_mm"}, "lines": len(info.get("lines_mm", []))}
    if a.json:
        if a.kind not in ("ruled", "grid"):
            note("--json is only written for ruled and grid paper (other kinds: compose.py page / place)")
        else:
            pj = paper_json_for_made(info, size, a.dpi, a.margin_line, a.side, a.out, a.row_step if a.kind == "grid" else 1)
            Path(a.json).write_text(json.dumps(pj, ensure_ascii=False, indent=1), encoding="utf-8")
            out.update({"json": a.json, "suggested_layout": pj.get("suggested_layout")})
    emit(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "blanks"):
        s = sp.add_parser(name)
        s.add_argument("image")
        stem = "paper" if name == "analyze" else "blanks"
        s.add_argument("-o", "--out", default=f"{stem}.json")
        s.add_argument("--overlay", default=f"{stem}-check.png")
        s.add_argument("--paper-size", help="A4, B5, A5, Letter, 16K … or WxH in mm — only if the image shows the whole sheet")
        s.add_argument("--corners", help="sheet corners TL,TR,BR,BL in px, if auto-detection fails")
        s.add_argument("--no-rectify", action="store_true", help="don't straighten (already a flat scan)")
        if name == "analyze":
            s.add_argument("--line-mm", type=float, help="line spacing in mm if known (common: 6, 7, 8, 9, 10)")
        else:
            s.add_argument("--min-height", type=float, default=12, help="smallest blank band to report (mm)")
    s = sp.add_parser("rectify")
    s.add_argument("image")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--corners")
    s.add_argument("--paper-size")
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
    s.add_argument("--json", help="also write the exact paper.json for compose.py (ruled and grid paper)")
    s.add_argument("--row-step", type=int, default=2, help="grid paper: squares per line of text in the paper.json (default 2)")
    a = ap.parse_args()
    {"analyze": cmd_analyze, "blanks": cmd_blanks, "rectify": cmd_rectify, "make": cmd_make}[a.cmd](a)


if __name__ == "__main__":
    main()
