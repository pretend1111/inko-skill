"""Inko's pen post-processing, ported verbatim from the Inko worker (worker/inko_worker/pen.py).

The server applies the pen AFTER generation, on the writer's raw ink, BEFORE the paper and the label: stroke weight,
darkness, pen texture and ink colour. scene.py uses this port to re-render an inko-scene package (scene.zip) locally,
so a page rendered here with a given pen is the same page the server would have produced with that pen.

- pen = {"weight": -1..1, "ink": -1..1, "type": original|gel|ballpoint|fountain|pencil, "color": black|blue|blueblack};
  missing = the original pen. With the original pen the caller skips apply() entirely (round(paper * ink)).
- Every step stores float32 and computes in float64 (same as the server and its TypeScript twin web/src/lib/pen.ts).
- Noise is sampled in page millimetres (pixel centre / px_per_mm), anchored at the page origin: the same sheet always
  gets the same texture, and a glyph that is moved picks up the texture of its new place.

Keep this file in lock-step with the worker: do not "improve" the maths here, the renderer must match the server.
Only numpy and the standard library are used.
"""
from __future__ import annotations

import math

import numpy as np

TYPES = ("original", "gel", "ballpoint", "fountain", "pencil")
COLORS = {"black": (0, 0, 0), "blue": (22, 58, 168), "blueblack": (22, 34, 86)}
GRAPHITE = (58, 58, 64)                       # pencil ignores the colour: always graphite
DEFAULT = {"weight": 0.0, "ink": 0.0, "type": "original", "color": "black"}
WEIGHT_MM = 0.09                              # weight = +-1: stroke edges grow / shrink by 0.09 mm
BLUR_MM = 0.035                               # fountain pen bleed (sigma)
BALL_CELL_MM, PENCIL_CELL_MM = 1.5, 0.08      # noise cell: ballpoint density waves / pencil grain
ROWS = 256                                    # noise is computed in row blocks to save memory


def _num(v) -> float:
    if isinstance(v, bool):
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):           # OverflowError: huge JSON integers
        return 0.0
    return min(1.0, max(-1.0, x)) if math.isfinite(x) else 0.0


def normalize(pen) -> dict:
    """Any input -> a valid pen: missing fields = default, numbers clamped to [-1, 1], unknown type / colour -> original / black."""
    p = pen if isinstance(pen, dict) else {}
    t, c = p.get("type"), p.get("color")
    return {"weight": _num(p.get("weight", 0.0)), "ink": _num(p.get("ink", 0.0)),
            "type": t if isinstance(t, str) and t in TYPES else "original",
            "color": c if isinstance(c, str) and c in COLORS else "black"}


def is_default(pen) -> bool:
    return normalize(pen) == DEFAULT


def ink_rgb(pen) -> tuple[int, int, int]:
    p = normalize(pen)
    return GRAPHITE if p["type"] == "pencil" else COLORS[p["color"]]


def is_colored(pen) -> bool:
    """The ink is not pure black (blue / blue-black / pencil graphite): the page cannot be saved as grayscale."""
    return ink_rgb(pen) != (0, 0, 0)


# -- steps 1-5: grayscale ink -> ink amount ------------------------------------------------------------------------------
def _half(r: int, dy: int) -> int:
    """Half width of row dy of the disk kernel of radius r (2(dx^2 + dy^2) <= 2r^2 + r: r = 1 is a 3x3 cross,
    r = 2 is 5x5 without corners)."""
    return math.isqrt((2 * r * r + r - 2 * dy * dy) // 2)


def _morph(a: np.ndarray, r: int, grow: bool) -> np.ndarray:
    """Max (grow: ink spreads out) / min filter with the disk kernel. The kernel is split into horizontal runs: running
    extrema for every half width first, then row-shifted extrema; pixels outside the image do not take part."""
    op = np.maximum if grow else np.minimum
    H, W = a.shape
    hor = [a]
    for w in range(1, r + 1):
        cur = hor[-1].copy()
        if w < W:
            op(cur[:, w:], a[:, :W - w], out=cur[:, w:])
            op(cur[:, :W - w], a[:, w:], out=cur[:, :W - w])
        hor.append(cur)
    out = hor[_half(r, 0)].copy()
    for dy in range(-r, r + 1):
        if dy == 0 or abs(dy) >= H:
            continue
        src = hor[_half(r, dy)]
        if dy > 0:
            op(out[:H - dy], src[dy:], out=out[:H - dy])
        else:
            op(out[-dy:], src[:H + dy], out=out[-dy:])
    return out


def _weight(a: np.ndarray, weight: float, s: float) -> np.ndarray:
    """Stroke weight: r = 0.09 mm * |weight| * s px; a fractional radius interpolates linearly between the results of
    radius floor(r) and floor(r) + 1 (r < 1: between the image itself and the cross)."""
    r = WEIGHT_MM * abs(weight) * s
    if not r > 0:
        return a
    lo = math.floor(r)
    f = r - lo
    A = a if lo == 0 else _morph(a, lo, weight > 0)
    if f == 0:
        return A
    B = _morph(a, lo + 1, weight > 0).astype(np.float64)
    A = A.astype(np.float64)
    return (A + (B - A) * f).astype(np.float32)


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian (radius ceil(3 sigma), at least 1; normalised weights); outside the image = no ink;
    horizontal pass, then vertical; float32 between the passes."""
    R = max(1, math.ceil(3 * sigma))
    ws = [math.exp(-(i * i) / (2 * sigma * sigma)) for i in range(-R, R + 1)]
    tot = 0.0
    for w in ws:
        tot += w
    ws = [w / tot for w in ws]
    for axis in (1, 0):
        src = a.astype(np.float64)
        n = src.shape[axis]
        acc = np.zeros(src.shape, np.float64)
        for t, w in enumerate(ws):
            d = t - R                                          # sample x + d
            if abs(d) >= n:
                continue
            lo, hi = max(0, -d), n - max(0, d)
            dst = acc[:, lo:hi] if axis == 1 else acc[lo:hi]
            dst += w * (src[:, lo + d:hi + d] if axis == 1 else src[lo + d:hi + d])
        a = acc.astype(np.float32)
    return a


def _hash(i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Lattice value ((i*73856093) XOR (j*19349663))*2654435761 mod 2^32 (32-bit unsigned products, like the TS
    Math.imul(...) >>> 0) / 2^32."""
    hi = i.astype(np.uint32) * np.uint32(73856093)
    hj = j.astype(np.uint32) * np.uint32(19349663)
    return ((hi ^ hj) * np.uint32(2654435761)).astype(np.float64) / 4294967296.0


def noise(shape: tuple[int, int], s: float, cell_mm: float, origin: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Value noise N(x, y; c) in [0, 1): which cell the pixel centre (in mm) / c falls in, bilinear inside the cell.
    origin = page pixel of this block's top-left corner."""
    h, w = shape
    u = ((np.arange(w, dtype=np.float64) + origin[0] + 0.5) / s) / cell_mm
    v = ((np.arange(h, dtype=np.float64) + origin[1] + 0.5) / s) / cell_mm
    i, j = np.floor(u), np.floor(v)
    fx, fy = (u - i)[None, :], (v - j)[:, None]
    i, j = i.astype(np.int64)[None, :], j.astype(np.int64)[:, None]
    h00, h10, h01, h11 = _hash(i, j), _hash(i + 1, j), _hash(i, j + 1), _hash(i + 1, j + 1)
    top = h00 + (h10 - h00) * fx
    bot = h01 + (h11 - h01) * fx
    return top + (bot - top) * fy


def _grain(a: np.ndarray, s: float, cell_mm: float, k: float, base: float, amp: float, origin: tuple[int, int]) -> np.ndarray:
    """a * k * (base + amp * N)."""
    out = np.empty_like(a)
    for y0 in range(0, a.shape[0], ROWS):
        blk = a[y0:y0 + ROWS]
        n = noise(blk.shape, s, cell_mm, (origin[0], origin[1] + y0))
        out[y0:y0 + ROWS] = (blk.astype(np.float64) * k * (base + amp * n)).astype(np.float32)
    return out


def apply(g: np.ndarray, pen, px_per_mm: float, origin: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Grayscale ink g in [0, 1] (1 = paper white) -> ink amount a in [0, 1] (float32), steps 1-5.
    px_per_mm = pixels per millimetre (Inko pages: 2480/210); origin: see noise()."""
    p = normalize(pen)
    s = float(px_per_mm)
    if not (math.isfinite(s) and s > 0):
        raise ValueError(f"invalid px_per_mm: {px_per_mm}")
    a = (1.0 - np.asarray(g, np.float64)).astype(np.float32)                   # 1. ink amount
    a = _weight(a, p["weight"], s)                                             # 2. weight
    if p["ink"] > 0:                                                           # 3. darkness
        a = (1.0 - (1.0 - a.astype(np.float64)) ** (1.0 + 1.2 * p["ink"])).astype(np.float32)
    elif p["ink"] < 0:
        a = (a.astype(np.float64) * (1.0 + 0.45 * p["ink"])).astype(np.float32)
    t = p["type"]                                                              # 4. pen texture
    if t == "gel":                                                             # gel: crisper edges, solid core
        a = np.clip((a.astype(np.float64) - 0.08) / 0.72, 0.0, 1.0).astype(np.float32)
    elif t == "ballpoint":                                                     # ballpoint: a bit lighter, slow density waves
        a = _grain(a, s, BALL_CELL_MM, 0.92, 0.88, 0.12, origin)
    elif t == "fountain":                                                      # fountain: wet, slightly bleeding edges
        a = np.clip(1.12 * _blur(a, BLUR_MM * s).astype(np.float64), 0.0, 1.0).astype(np.float32)
    elif t == "pencil":                                                        # pencil: gray, grainy
        a = _grain(a, s, PENCIL_CELL_MM, 0.68, 0.55, 0.45, origin)
    return a


# -- step 6: colour ------------------------------------------------------------------------------------------------------
def composite(region: np.ndarray, a: np.ndarray, pen) -> np.ndarray:
    """Ink multiplied onto the paper: out = paper * (1 - a * (1 - ink/255)); ruled lines still show through the ink.
    region = paper RGB (H x W x 3); returns uint8."""
    k = np.array([1.0 - c / 255.0 for c in ink_rgb(pen)], np.float64)
    mixed = np.asarray(region, np.float64) * (1.0 - a.astype(np.float64)[..., None] * k)
    return mixed.round().clip(0, 255).astype(np.uint8)
