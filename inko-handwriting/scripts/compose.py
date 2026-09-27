#!/usr/bin/env python3
"""Put Inko handwriting onto real paper — the user's own ruled sheet, a worksheet, or any photo.

    python compose.py lines --job inko-output/<run>/ --paper paper.json -o final.jpg [--start-line 4]
        Snap every generated line onto the ruled lines found by `paper.py analyze` (or drawn by `paper.py make --json`).
        Follows tilt, page curl and perspective; photos straightened by paper.py are written back into the ORIGINAL photo.
        Text that runs past the last line continues on the next --paper (or another copy of the last one): -2, -3 …
        --drift natural (default) lets the left edge creep right line by line like a real hand (random | none;
        --drift-amount 0.5 = half as much); each line's shift is reported in `check` (drift_mm).

    python compose.py drift --job inko-output/<run>/ -o drifted/ [--drift natural|random] [--drift-amount 1] [--seed N]
        Inko pages handed over as they are: move each written line a little left/right so the left edges aren't ruler-
        straight (creeping right, re-anchored at a new problem). Only the ink moves — ruled lines, margin line, grid and
        paper stay put; boxed answers stay put; the label and AIGC metadata are kept; inko.pdf is rebuilt if the job had one.

    python compose.py page --job inko-output/<run>/ --onto worksheet.jpg -o out.jpg [--paper-size A4] [--sheet blanks.json]
        Lay whole generated pages over a sheet of the same paper size (answer boxes were planned in mm on that sheet).

    python compose.py place ink.png --onto photo.jpg --box x,y,w,h -o out.jpg [--fit contain] [--rotate -2]
        Put handwriting (a page or an ink layer, auto-cropped to its writing) into a pixel box of any image.

Common options: --color (auto|match|page|black|blue|blueblack|red|pencil|#hex), --texture, --weight, --soften, --grain,
--label-corner, -q/--quality. `--color auto` (default) uses the colour of the pen the job was generated with (blue / blue-black / black gel… /
pencil) at the darkness real ink has in a photo; if the job used the original pen, the colour of the writing already on
the paper; else the page's ink darkened. `--color match` always copies the existing writing's colour.
The visible label 「AI生成 · Inko」 is re-applied to every output at >= 5 % of its shortest side (auto contrast), and the
implicit AIGC metadata is carried over. Pages generated without a visible label stay without one.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import json
import re
import shutil
import time
import warnings
import zlib
from pathlib import Path

import numpy as np

from _common import (PRODUCER, SKILL_TAG, Image, ImageFilter, LABEL_MIN_FRAC, apply_label, die, emit, find_label_bbox, gaussian, label_box,
                     label_from_b64, label_from_page, luminance, merge_meta, note, open_image, parse_size_mm, px_per_mm_of, save_image,
                     xmp_packet)
import ink as INK

LIFT = 0.14                  # Inko's own ruled papers: the writing's baseline sits 0.14 line-spacings above the line


# ── generated pages ─────────────────────────────────────────────────────────

def _natural(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def job_inputs(paths: list[str], job: str | None) -> tuple[list[Path], dict | None, dict | None, dict]:
    """-> (page files, plan, layout, generation params) of a job folder from inko.py (or of the pages given)."""
    plan = layout = None
    params: dict = {}
    files: list[Path] = [Path(p) for p in paths]
    if job:
        jd = Path(job)
        if not jd.is_dir():
            die(f"--job {jd} is not a folder (give the folder inko.py generate printed as out_dir)")
        files = files or sorted([p for p in jd.glob("page-*.png")], key=_natural)
        if not files:
            files = sorted([p for p in jd.glob("page-*.webp")], key=_natural)
        for name in ("plan.json", "layout.json", "job.json"):
            f = jd / name
            if f.exists():
                obj = json.loads(f.read_text(encoding="utf-8"))
                if name == "plan.json":
                    plan = obj
                elif name == "layout.json":
                    layout = obj
                else:
                    params = obj.get("params") or {}
        if layout is None and isinstance(params.get("layout"), dict):
            layout = params["layout"]
    if not files:
        die("no pages given (pass page PNGs or --job <folder with page-1.png …>)")
    return files, plan, layout, params


def load_pages(paths: list[str], job: str | None) -> tuple[list[dict], dict | None, dict | None]:
    """-> ([{path, alpha, ink_rgb, label, meta, ppm, size, params}], plan, layout)"""
    files, plan, layout, params = job_inputs(paths, job)
    pages = []
    for f in files:
        img, meta = open_image(f)
        sep = INK.separate(img)
        alpha = sep["alpha"].copy()
        lab_img = label_from_b64(meta.get("label_png"))
        bbox = sep.get("label_bbox") or ((meta.get("ink") or {}).get("label_bbox"))
        if bbox:
            x0, y0, x1, y1 = bbox
            if lab_img is None and not sep["is_layer"]:
                lab_img = label_from_page(np.asarray(img.convert("RGB")), bbox)
            alpha[y0:y1, x0:x1] = 0                                # the label is handled separately, never as handwriting
        pages.append({"path": str(f), "alpha": alpha, "ink_rgb": sep["ink_rgb"], "label": lab_img, "meta": meta,
                      "ppm": px_per_mm_of(img, meta), "size": img.size, "params": params, "is_layer": sep["is_layer"]})
    return pages, plan, layout


INKO_RULES = {"ruled8": (34.0, 274.0, 8.0), "ruled7": (33.0, 276.0, 7.0)}   # first rule, last rule, spacing (mm) on Inko's A4
PLAIN_CHAR_PX = {"small": 71, "medium": 83, "large": 100}               # plain generation: character height at 300 dpi
PLAIN_LINE_ADV = 1.75                                                     # … and line advance in character heights


def page_rules(page: dict, layout: dict | None) -> tuple[list[float], float] | None:
    """Ruled lines of a generated page (known for Inko's ruled papers, detected otherwise): (rule y's in px, spacing px)."""
    ppm = page["ppm"]
    pid = (layout or {}).get("paperId")
    if pid in INKO_RULES:
        a, b, sp = INKO_RULES[pid]
        return [y * ppm for y in np.arange(a, b + 1e-6, sp)], sp * ppm
    if page.get("is_layer"):
        return None
    import paper as PAPER
    img, _ = open_image(page["path"])
    small = img.convert("RGB").resize((img.width // 2, img.height // 2), Image.BILINEAR)
    det = PAPER.detect_lines(luminance(np.asarray(small, np.float32)))
    reg = [l for l in det["lines"] if l.get("regular")]
    if len(reg) < 4 or not det["pitch"]:
        return None
    W2 = small.width
    ys = sorted(float(np.polyval(l["c"], W2 / 2)) * 2 for l in reg)
    return ys, float(np.median(np.diff(ys)))


def rule_rows(alpha: np.ndarray, rules: list[float], sp: float, lift: float) -> list[dict]:
    """Rows of a page written on ruled lines: the writing that sits on each rule."""
    H, W = alpha.shape
    rows = []
    for ry in rules:
        a0, b0 = int(max(0, ry - 0.92 * sp)), int(min(H, ry + 0.08 * sp))
        band = alpha[a0:b0] > 0.25
        if band.sum() < 0.02 * sp * sp:
            continue
        cols = np.flatnonzero(band.any(0))
        rows.append({"base": float(ry - lift * sp), "x0": float(cols[0]), "x1": float(cols[-1] + 1),
                     "a": float((0.9 - lift) * sp), "d": float((lift + 0.06) * sp), "s": 0.62 * sp})
    return rows


def plan_rows(plan: dict, page_i: int, ppm: float) -> list[dict]:
    """Rows of body text on one page from the layout plan (items in boxes are skipped): baseline & extent in px."""
    pages = (plan.get("plan") or {}).get("pages") or []
    if page_i >= len(pages):
        return []
    rows: dict = {}
    for it in pages[page_i]:
        if it.get("box"):
            continue
        rows.setdefault(it.get("row", 0), []).append(it)
    out = []
    for r, items in rows.items():
        base = float(np.median([it["y"] for it in items]))
        s = max(it["s"] for it in items)
        a = max((it["m"]["a"] * it["s"]) if it.get("m") else 0.9 * it["s"] for it in items)
        d = max((it["m"]["d"] * it["s"]) if it.get("m") else 0.12 * it["s"] for it in items)
        out.append({"base": base * ppm, "x0": min(it["x"] for it in items) * ppm, "x1": max(it["x"] + it["w"] for it in items) * ppm,
                    "a": a * ppm, "d": d * ppm, "s": s * ppm, "i0": min(items, key=lambda it: it["x"]).get("i")})
    return sorted(out, key=lambda r: r["base"])


def detect_rows(alpha: np.ndarray, ppm: float, prior: float | None = None) -> list[dict]:
    """Rows of handwriting from the ink itself (no plan): row centres = peaks of the horizontal ink profile at the
    dominant row period; each row's baseline = bottom of its dense core."""
    H, W = alpha.shape
    ink = (alpha > 0.25).mean(1).astype(np.float32)
    if not ink.any():
        return []
    prof = gaussian(ink[None, :], 0.6 * ppm)[0]
    pc = prof - prof.mean()
    ac = np.correlate(pc, pc, mode="full")[H - 1:]
    lo, hi = (int(0.75 * prior), int(1.35 * prior)) if prior else (int(5.5 * ppm), int(min(H // 2, 40 * ppm)))
    lag = int(10 * ppm)
    if hi > lo + 2:                                          # first strong local maximum = the row period
        seg = ac[lo - 1:hi + 1]
        mx = [i for i in range(1, len(seg) - 1) if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]]
        top = max((seg[i] for i in mx), default=0)
        strong = [i for i in mx if seg[i] >= 0.5 * top and seg[i] > 0]
        if strong:
            lag = lo - 1 + strong[0]
    sm = gaussian(ink[None, :], 0.18 * lag)[0]
    thr = 0.12 * np.percentile(sm[sm > 0], 95)
    peaks = []
    for i in np.argsort(-sm):
        if sm[i] < thr:
            break
        if all(abs(i - j) >= 0.6 * lag for j in peaks):
            peaks.append(int(i))
    peaks.sort()
    rows = []
    for n, c in enumerate(peaks):
        top = int((peaks[n - 1] + c) / 2) if n else max(0, int(c - 0.6 * lag))
        bot = int((c + peaks[n + 1]) / 2) if n + 1 < len(peaks) else min(H, int(c + 0.6 * lag))
        band = ink[top:bot]
        if band.sum() <= 0:
            continue
        cum = np.cumsum(band) / band.sum()
        base = top + int(np.searchsorted(cum, 0.9))
        t = top + int(np.searchsorted(cum, 0.04))
        cols = np.flatnonzero((alpha[top:bot] > 0.25).any(0))
        s = max(1.0, (base - t) / 0.92)
        rows.append({"base": float(base), "x0": float(cols[0]), "x1": float(cols[-1] + 1), "a": float(base - top), "d": float(bot - base), "s": s})
    return rows


def gen_pitch(pages_rows: list[list[dict]], layout: dict | None, ppm: float) -> float:
    if layout:
        d = layout.get("d") or {}
        pid = layout.get("paperId") or "blank"
        if pid in ("ruled8", "ruled7"):
            return (8.0 if pid == "ruled8" else 7.0) * max(1, int(d.get("lineStep") or 1)) * ppm
        if pid == "blank":
            return float(d.get("size", 7.0)) * float(d.get("line", 1.7)) * ppm
    diffs = np.concatenate([np.diff([r["base"] for r in rows]) for rows in pages_rows if len(rows) >= 2] or [np.zeros(0)])
    diffs = diffs[diffs > 2 * ppm]
    if not len(diffs):
        return 12.0 * ppm
    d0 = np.percentile(diffs, 20)
    return float(np.median(diffs[diffs < 1.35 * d0]))


# ── natural left edges (drift) ──────────────────────────────────────────────
# Inko's layout engine starts every continuation line at exactly the same x; a real hand doesn't: the left edge creeps
# right line by line, snaps back at a new problem, or wobbles a little. Offsets are in character heights (s).

DRIFT_MODES = ("natural", "random", "none")
DRIFT_CAP = 0.45                     # |offset| <= 0.45 s (x --drift-amount)
PROBLEM_RE = re.compile(r"[ \t　]*(?:\d{1,3}\s*(?:[.．](?!\d)|[、:：)）])|[(（]\s*\d{1,3}\s*[)）]|[①-⑳]|"
                        r"第\s*[\d一二三四五六七八九十]+\s*[题问]|[一二三四五六七八九十]{1,3}\s*[、.．])")


def drift_seed(seed: int, key: str | None) -> list[int]:
    """--seed mixed with the job's id: deterministic for one job, but two jobs never drift alike."""
    return [int(seed) & 0xFFFFFFFF, zlib.crc32((key or "").encode("utf-8"))]


def page_key(meta: dict, path) -> str:
    return str(((meta or {}).get("aigc") or {}).get("ProduceID") or Path(str(path)).name)


def drift_series(n: int, mode: str, amount: float, seed, resets=()) -> np.ndarray:
    """Left-edge offsets of n successive written lines, in character heights (+ = to the right).
    natural: starts at 0..0.08, each line adds 0.015..0.05 (slow creep) plus a smooth AR(1) wobble (σ 0.03, ρ 0.6); the
      creep tops out near 0.42, where the writer now and then re-anchors; a line in `resets` (after a blank line, at a new
      problem) keeps only 30 % of the creep.
    random: the wobble alone (σ 0.06) around +0.12, so it rarely needs clipping at a margin line.  none: zeros."""
    out = np.zeros(n)
    if mode == "none" or n == 0 or not amount:
        return out
    rng = np.random.default_rng(seed)
    rho, sig = 0.6, (0.06 if mode == "random" else 0.03)
    e, creep, reset = rng.normal(0, sig), rng.uniform(0, 0.08), set(resets)
    for k in range(n):
        if k:
            e = rho * e + np.sqrt(1 - rho * rho) * rng.normal(0, sig)
            if mode == "natural":
                if k in reset:
                    creep *= 0.3
                else:
                    creep += rng.uniform(0.015, 0.05)
                if creep > 0.42:
                    creep = 0.42 if rng.random() > 0.25 else 0.3 * creep
        out[k] = (0.12 if mode == "random" else creep) + e
    return np.clip(out, -DRIFT_CAP, DRIFT_CAP) * amount


def _py_index(text: str):
    """UTF-16 offsets (the layout engine's `i`) -> Python string indices."""
    if all(ord(c) < 0x10000 for c in text):
        return lambda i: i
    m: list[int] = []
    for n, c in enumerate(text):
        m.extend([n] * (2 if ord(c) > 0xFFFF else 1))
    return lambda i: m[i] if 0 <= i < len(m) else len(text)


def starts_problem(text: str | None, i16, pidx=None) -> bool:
    """Does the layout text at this offset start a paragraph with a problem number (1. / (2) / 3、 / ① / 第4题)?"""
    if not text or i16 is None:
        return False
    i = (pidx or _py_index(text))(int(i16))
    if i >= len(text) or text[:i].rstrip(" \t　")[-1:] not in ("", "\n"):
        return False
    return bool(PROBLEM_RE.match(text, i))


def clamp_drift(d: float, left: float, right: float, lo: float, hi: float) -> float:
    """Shift d (px) limited so the line's ink [left, right] stays inside [lo, hi] — a line already outside is not pulled
    back, only kept from going further."""
    return float(min(max(d, min(0.0, lo - left)), max(0.0, hi - right)))


def drift_resets(bases: list[float], pitch: float, firsts: list, text: str | None) -> list[int]:
    """Indices of lines that start after a blank line (gap > 1.5 pitch) or begin a new problem (needs the layout text)."""
    pidx = _py_index(text) if text else None
    return [k for k in range(1, len(bases))
            if (bases[k] is not None and bases[k - 1] is not None and bases[k] - bases[k - 1] > 1.5 * pitch)
            or starts_problem(text, firsts[k], pidx)]


# ── ink styling shared by all modes ─────────────────────────────────────────

def style_alpha(alpha: np.ndarray, a, ppm: float) -> tuple[np.ndarray, np.ndarray | None]:
    if a.weight:
        alpha = INK.adjust_weight(alpha, a.weight * 0.09 * ppm)
    if a.darkness:
        alpha = INK.adjust_darkness(alpha, a.darkness)
    alpha, mult = INK.apply_texture(alpha, a.texture, ppm, a.seed)
    return np.clip(alpha, 0, 1), mult


def ink_color(a, pages: list[dict], paper: dict | None = None) -> tuple[tuple[int, int, int], str]:
    """-> (ink colour, why). auto: the pen the job was generated with (if it had a colour or type), else the colour of the
    writing already on the paper, else the page's ink made as dark as real pen ink looks in a photo (Inko's page inks are
    rendered lighter than that). match: always the existing writing."""
    page_ink = tuple(int(v) for v in pages[0]["ink_rgb"])
    c = (a.color or "auto").lower()
    ew = (paper or {}).get("existing_writing") or {}
    if c == "match" and not ew.get("ink_rgb"):
        note("--color match: the paper has no writing to match; using auto")
        c = "auto"
    if c == "match":
        return tuple(int(v) for v in ew["ink_rgb"]), "matched the writing already on this paper"
    if c == "page":
        return page_ink, "the generated page's own ink"
    if c != "auto":
        return INK.parse_color(a.color, page_ink), a.color
    pen = ((pages[0].get("params") or {}).get("pen") or {})
    if pen.get("type") == "pencil":                         # a pen the user chose when generating wins …
        return INK.INK_COLORS["pencil"], "pencil (the job's pen)"
    if pen.get("color") in ("blue", "blueblack") or (pen.get("color") == "black" and pen.get("type") not in (None, "original")):
        return INK.INK_COLORS[pen["color"]], f"{pen['color']} (the job's pen)"
    if ew.get("ink_rgb"):                                   # … else look like the writing already on the paper
        return tuple(int(v) for v in ew["ink_rgb"]), "matched the writing already on this paper"
    lum = 0.299 * page_ink[0] + 0.587 * page_ink[1] + 0.114 * page_ink[2]
    if lum > 55:
        k = 55 / lum
        return tuple(int(v * k) for v in page_ink), "the page's ink, darkened to real-pen darkness"
    return page_ink, "the generated page's own ink"


def pick_label_corner(a, size: tuple[int, int], ink: np.ndarray | None, label) -> str:
    """auto: the first corner (br, bl, tr, tl) whose label box has no handwriting under it."""
    if a.label_corner != "auto":
        return a.label_corner
    if ink is None:
        return "br"
    for c in ("br", "bl", "tr", "tl"):
        x0, y0, x1, y1 = label_box(size, label, where=c)
        if not ink[y0:y1, x0:x1].any():
            return c
    note("handwriting reaches every corner; the label goes bottom-right over it")
    return "br"


def paper_noise(rgb: np.ndarray, mask: np.ndarray | None = None) -> float:
    L = luminance(rgb)
    hp = L - gaussian(L, 1.5)
    v = hp[mask] if mask is not None and mask.any() else hp
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def blend(base: np.ndarray, alpha: np.ndarray, color, mult, soften: float, grain: float, seed: int) -> np.ndarray:
    """Multiply the ink into the paper (so shadows and paper texture stay), matched to the photo's softness and noise."""
    if soften > 0:
        alpha = gaussian(alpha, soften)
    fac = 1 - alpha[..., None] * (1 - np.clip(np.array(color, np.float32)[None, None, :] * (mult if mult is not None else 1.0), 0, 255) / 255.0)
    if grain > 0:
        rng = np.random.default_rng(seed)
        n = rng.normal(0, grain / 255.0, alpha.shape).astype(np.float32)
        fac = fac + (n * np.clip(alpha * 1.5, 0, 1))[..., None]
    return np.clip(fac, 0, 1.2)


def finish(result: Image.Image, a, pages: list[dict], out: str, extra: dict, ink: np.ndarray | None = None,
           meta_extra: dict | None = None) -> dict:
    """Re-apply the visible label (if the pages had one; clear of the new handwriting) and save with the AIGC metadata."""
    has_label = any(p["label"] is not None for p in pages)
    corner = None
    if has_label:
        lab = next(p["label"] for p in pages if p["label"] is not None)
        corner = pick_label_corner(a, result.size, ink, lab)
        result = apply_label(result, lab, frac=LABEL_MIN_FRAC * 1.06, where=corner)
    meta = merge_meta(*[p["meta"] for p in pages])
    meta = {**meta, "ink": None, "label_png": None, **(meta_extra or {})}
    save_image(result, out, meta, quality=a.quality)
    return {"out": out, "size_px": list(result.size), "visible_label": has_label, "label_corner": corner,
            "aigc_metadata": bool(meta.get("aigc")), **extra}


# ── lines: snap to ruled paper ──────────────────────────────────────────────

def load_paper(path: str) -> dict:
    """paper.json from paper.py; image paths are made absolute (relative ones are taken next to the JSON)."""
    p = Path(path)
    if not p.exists():
        die(f"paper file not found: {p} (make it with: python paper.py analyze <photo> -o {p.name})")
    pj = json.loads(p.read_text(encoding="utf-8"))
    for k in ("analyzed_image", "source"):
        if pj.get(k) and not Path(pj[k]).exists() and (p.parent / Path(pj[k]).name).exists():
            pj[k] = str(p.parent / Path(pj[k]).name)
    return pj


def _poly(line: dict):
    c = line["poly"]
    return lambda x: c[0] + c[1] * x + c[2] * x * x


def _local_pitch(lines: list[dict], j: int, fallback: float) -> float:
    ys = [l["y"] for l in lines]
    g = [abs(ys[j] - ys[k]) for k in (j - 1, j + 1) if 0 <= k < len(ys)]
    g = [v for v in g if 0.6 * fallback < v < 1.4 * fallback]
    return float(np.mean(g)) if g else fallback


def warp_row(crop: np.ndarray, base_c: float, X: float, B, out_h_pad: tuple[float, float], step: int = 12) -> tuple[np.ndarray, int, int]:
    """Bend a (scaled) row image so its baseline follows the paper line B(x). Returns (image, x_origin, y_origin)."""
    h, w = crop.shape
    xs = np.arange(0, w + step, step, dtype=np.float64)
    xs[-1] = min(xs[-1], w)
    bs = np.array([B(X + x) for x in xs])
    top = int(np.floor(bs.min() - out_h_pad[0]))
    bot = int(np.ceil(bs.max() + out_h_pad[1]))
    ox = int(np.floor(X))
    dx = X - ox
    H = bot - top
    mesh = []
    for i in range(len(xs) - 1):
        xa, xb = xs[i], xs[i + 1]
        if xb <= xa:
            continue
        ya, yb = bs[i], bs[i + 1]
        box = (int(round(xa + dx)), 0, int(round(xb + dx)), H)
        if box[2] <= box[0]:
            continue
        sx0, sx1 = box[0] - dx, box[2] - dx
        quad = (sx0, base_c + (top - ya), sx0, base_c + (bot - ya), sx1, base_c + (bot - yb), sx1, base_c + (top - yb))
        mesh.append((box, quad))
    im = Image.fromarray(crop.astype(np.float32), "F").transform((int(np.ceil(w + dx)) + 1, H), Image.MESH, mesh, Image.BILINEAR, fillcolor=0.0)
    return np.asarray(im, np.float32), ox, top


def cmd_lines(a) -> None:
    if a.drift_amount < 0:
        die("--drift-amount must be >= 0 (0 = straight left edges, like --drift none)")
    pages, plan, layout = load_pages(a.pages, a.job)
    if a.plan:
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    if a.layout:
        layout = json.loads(Path(a.layout).read_text(encoding="utf-8"))
        layout = layout.get("layout", layout)
    ppm_g = pages[0]["ppm"]
    rows_per_page, known_pitch, how = [], None, "plan"
    for i, p in enumerate(pages):
        if plan:
            rows_per_page.append(plan_rows(plan, i, p["ppm"]))
            continue
        rules = page_rules(p, layout)
        if rules:
            lift = float(((layout or {}).get("d") or {}).get("lift", LIFT))
            rows_per_page.append(rule_rows(p["alpha"], rules[0], rules[1], lift))
            known_pitch, how = rules[1], "the page's ruled lines"
        else:
            size = (p.get("params") or {}).get("size")
            prior = PLAIN_CHAR_PX[size] * PLAIN_LINE_ADV * p["ppm"] / (300 / 25.4) if size in PLAIN_CHAR_PX else None
            rows_per_page.append(detect_rows(p["alpha"], p["ppm"], prior))
            how = "the ink (no plan.json — pass --plan for exact baselines)"
    if not any(rows_per_page):
        die("found no lines of handwriting in the pages")
    pitch_g = known_pitch or gen_pitch(rows_per_page, layout, ppm_g)
    if plan:                                                 # text boxes are not part of the line flow: leave them out
        boxed = 0
        for i, p in enumerate(pages):
            items = ((plan.get("plan") or {}).get("pages") or [[]] * (i + 1))[i] if i < len((plan.get("plan") or {}).get("pages") or []) else []
            for it in items:
                if it.get("box"):
                    k = p["ppm"]
                    x0, x1 = int((it["x"] - 0.3 * it["s"]) * k), int((it["x"] + it["w"] + 0.3 * it["s"]) * k)
                    y0, y1 = int((it["y"] - 1.1 * it["s"]) * k), int((it["y"] + 0.45 * it["s"]) * k)
                    p["alpha"][max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 0
                    boxed += 1
        if boxed:
            note(f"{boxed} characters in text boxes are left out: `lines` follows the text flow only (use `place` for boxes)")
    note(f"generated rows found from {how}: {sum(len(r) for r in rows_per_page)} rows, {pitch_g / ppm_g:.2f} mm apart")
    owners = []
    for rows, p in zip(rows_per_page, pages):                # which row owns each pixel row of the page
        Hg = p["alpha"].shape[0]
        y = np.arange(Hg, dtype=np.float32)[:, None]
        for n, r in enumerate(rows):
            r["id"] = n
        if rows:
            top = np.array([r["base"] - r["a"] for r in rows], np.float32)[None, :]
            bot = np.array([r["base"] + r["d"] for r in rows], np.float32)[None, :]
            dist = np.maximum(top - y, 0) + np.maximum(y - bot, 0)
            owners.append(np.argmin(dist, axis=1))
        else:
            owners.append(np.full(Hg, -1))
    if layout and layout.get("d", {}).get("margins"):
        left_g = float(layout["d"]["margins"][3]) * ppm_g
    elif layout and (layout.get("paperId") or "blank") in ("ruled8", "ruled7"):
        left_g = 24 * ppm_g
    else:
        left_g = min(r["x0"] for rows in rows_per_page for r in rows)
    # slot numbers: keep blank lines and paragraph gaps as they were generated
    seq, offset = [], 0
    for pi, rows in enumerate(rows_per_page):
        if not rows:
            continue
        b0 = rows[0]["base"]
        ks = [int(round((r["base"] - b0) / pitch_g)) for r in rows]
        for r, k in zip(rows, ks):
            seq.append((pi, r, offset + k))
        offset += ks[-1] + 1
    papers = [load_paper(p) for p in a.paper]
    sheets = []                                        # [(paper_json, [line dicts usable as slots])]

    def slots_of(pj: dict, first: bool) -> list[dict]:
        usable = [l for l in pj["lines"] if l.get("regular") or l.get("inferred")]
        start = a.start_line if (first and a.start_line) else (pj.get("first_free_line") or 1)
        cand = [l for l in usable if l["i"] >= start]
        if not a.over_writing:
            cand = [l for l in cand if not l.get("written")]
        if a.every > 1:
            cand = cand[::a.every]
        return cand

    need = seq[-1][2] + 1
    k = 0
    while sum(len(s) for _, s in sheets) < need and k < 50:
        pj = papers[min(k, len(papers) - 1)]
        sl = slots_of(pj, k == 0)
        if not sl:
            die("the paper has no free lines to write on (see paper.json free_lines, or use --over-writing / --start-line)")
        if k >= len(papers):
            note(f"text continues on another copy of {pj.get('source')} (sheet {k + 1})" +
                 (" — careful: that photo already shows writing on lines " + str(pj.get("written_lines")) if pj.get("written_lines") else ""))
        sheets.append((pj, sl))
        k += 1
    color, color_why = ink_color(a, pages, papers[0])
    ew = papers[0].get("existing_writing") or {}
    # how this handwriting's ink sits relative to its own baseline (measured like paper.py measures existing writing)
    offs, hts = [], []
    for pi, r, _ in seq:
        al = pages[pi]["alpha"]
        ya, yb = int(max(0, r["base"] - 1.1 * pitch_g)), int(min(al.shape[0], r["base"] + 0.5 * pitch_g))
        own = owners[pi][ya:yb] == r["id"]
        yy, _xx = np.nonzero((al[ya:yb] > 0.3) & own[:, None])
        if len(yy) >= 50:
            above = r["base"] - (ya + yy)
            lo, hi = np.percentile(above, 6), np.percentile(above, 96)
            offs.append(lo / pitch_g)
            hts.append((hi - lo) / pitch_g)
    gen_off = float(np.median(offs)) if offs else 0.0
    gen_h = float(np.median(hts)) if hts else 0.0
    if a.lift is not None:
        lift_f, lift_why = a.lift, "given"
    elif 0.03 <= (ew.get("lift") or 0) <= 0.35:
        lift_f = max(0.0, ew["lift"] - gen_off)                # ink bottoms level with the writing already there
        lift_why = (f"matched the writing already on this paper (its ink sits {ew['lift']:.2f} above the line; this "
                    f"handwriting's ink sits {gen_off:.2f} above its baseline)")
    else:
        lift_f, lift_why = LIFT, "Inko default 0.14"
    size_hint = None
    if ew.get("height") and gen_h:
        ratio = ew["height"] / (gen_h * a.scale)
        if abs(ratio - 1) > 0.25:                              # rough: different hands spread ink differently
            size_hint = (f"new writing measures {gen_h * a.scale:.2f} line spacings tall vs {ew['height']:.2f} for the writing already "
                         f"on the page (a rough measure) — compare them by eye; if it really looks bigger/smaller, try "
                         f"--scale {a.scale * ratio:.2f}")
    first_rows = [r for pi, r, slot in seq if slot == 0]
    if first_rows and first_rows[0]["s"] > 1.15 * float(np.median([r["s"] for _, r, _ in seq])) and not a.start_line:
        note("the first row is an enlarged title: on the paper's first line it may stick above it — consider --start-line 2")
    # natural left edges: one offset per written line (slot), carried across sheets
    drift_mode, drift_note, drift_mark = a.drift, None, None
    already = next((p["meta"]["drift"] for p in pages if p["meta"].get("drift")), None)
    if already:
        drift_mark = already
        if drift_mode != "none":
            drift_mode, drift_note = "none", "these pages were drifted already (compose.py drift): their left edges are kept, not drifted again"
            note(drift_note)
    slot_rows: dict = {}
    for _pi, r, slot in seq:
        slot_rows.setdefault(slot, []).append(r)
    slot_ids = sorted(slot_rows)
    text = (layout or {}).get("text") if plan else None
    resets = drift_resets([float(k) for k in slot_ids], 1.0, [min(slot_rows[k], key=lambda r: r["x0"]).get("i0") for k in slot_ids], text)
    drift_of = dict(zip(slot_ids, drift_series(len(slot_ids), drift_mode, a.drift_amount,
                                               drift_seed(a.seed, page_key(pages[0]["meta"], pages[0]["path"])), resets)))
    s_med = float(np.median([r["s"] for _, r, _ in seq]))              # character height of the generated page, px
    if drift_mode != "none" and a.drift_amount:
        drift_mark = {"by": "compose.py lines", "mode": drift_mode, "amount": a.drift_amount, "seed": a.seed}
    drift_mm: dict = {}
    outs = []
    slot_base = 0
    placed_total = 0
    for si, (pj, slots) in enumerate(sheets):
        src_img, src_meta = open_image(pj["analyzed_image"])
        rgb = np.asarray(src_img.convert("RGB"), np.float32)
        H, W = rgb.shape[:2]
        canvas = np.zeros((H, W), np.float32)
        lines_all = [l for l in pj["lines"] if l.get("regular") or l.get("inferred")]
        pitch_p = pj.get("pitch_px") or 40.0
        ppm_p = pj.get("px_per_mm") or ppm_g
        margin_x = pj.get("margin_line_x_px")
        placed = 0
        rows_done: list = []
        prep: list = []                                       # 1) where each row goes without drift
        for pi, r, slot in seq:
            j = slot - slot_base
            if not 0 <= j < len(slots):
                continue
            line = slots[j]
            idx = next((n for n, l in enumerate(lines_all) if l["i"] == line["i"]), 0)
            p_loc = _local_pitch(lines_all, idx, pitch_p)
            sc = p_loc / pitch_g * a.scale
            alpha = pages[pi]["alpha"]
            Hg, Wg = alpha.shape
            s = r["s"]
            ya = int(max(0, r["base"] - min(max(r["a"] + 0.35 * s, 0.8 * pitch_g), 1.25 * pitch_g)))
            yb = int(min(Hg, r["base"] + min(max(r["d"] + 0.3 * s, 0.3 * pitch_g), 0.9 * pitch_g)))
            own = owners[pi][ya:yb] == r["id"]                    # strokes of the rows above/below stay with them
            band = alpha[ya:yb] * own[:, None]
            cols = np.flatnonzero(band.max(0) > 0.05)
            if not len(cols):
                continue
            xa, xb = int(max(0, cols[0] - 0.2 * s)), int(min(Wg, cols[-1] + 1 + 0.2 * s))
            crop = band[:, xa:xb]
            nh, nw = max(1, round(crop.shape[0] * sc)), max(1, round(crop.shape[1] * sc))
            wx0 = line.get("write_x0") if line.get("write_x0") is not None else pj["write_x0_px"]
            wx1 = line.get("write_x1") if line.get("write_x1") is not None else pj["write_x1_px"]
            X = wx0 + (xa - left_g) * sc + a.x_offset * p_loc
            if X + nw > wx1 + 0.6 * p_loc:
                note(f"line {line['i']}: generated line is {X + nw - wx1:.0f}px longer than the paper's writing area — "
                     "use the suggested layout's margins (or --scale 0.95)")
            prep.append({"slot": slot, "line": line, "r": r, "ya": ya, "crop": crop, "sc": sc, "nh": nh, "nw": nw, "p_loc": p_loc,
                         "X": X, "ink0": X + (cols[0] - xa) * sc, "ink1": X + (cols[-1] + 1 - xa) * sc, "wx0": wx0, "wx1": wx1,
                         "drift": 0.0})
        by_slot: dict = {}                                    # 2) one shift per written line, kept inside the writing area
        for q in prep:
            by_slot.setdefault(q["slot"], []).append(q)
        for slot, qs in by_slot.items():
            sp = s_med * qs[0]["sc"]                              # character height on this paper, px
            left, right = min(q["ink0"] for q in qs), max(q["ink1"] for q in qs)
            lo = left if margin_x is not None else qs[0]["wx0"] - 0.2 * sp     # a margin line: never further left …
            dv = abs(drift_of.get(slot, 0.0)) if margin_x is not None else drift_of.get(slot, 0.0)   # … the wobble folds back
            dpx = clamp_drift(dv * sp, left, right, lo, qs[0]["wx1"] + 0.3 * sp)
            for q in qs:
                q["drift"] = dpx
            drift_mm[slot] = dpx / ppm_p
        for q in prep:                                        # 3) write
            line, r, sc, nh, nw, p_loc = q["line"], q["r"], q["sc"], q["nh"], q["nw"], q["p_loc"]
            scaled = np.asarray(Image.fromarray(q["crop"], "F").resize((nw, nh), Image.LANCZOS), np.float32).clip(0, 1)
            B0 = _poly(line)
            lift = lift_f * p_loc
            B = (lambda x, B0=B0, lift=lift: B0(x) - lift)
            X = q["X"] + q["drift"]
            base_c = (r["base"] - q["ya"]) * sc
            img_row, ox, oy = warp_row(scaled, base_c, X, B, (base_c + 2, nh - base_c + 2))
            y0, x0 = max(0, oy), max(0, ox)
            y1, x1 = min(H, oy + img_row.shape[0]), min(W, ox + img_row.shape[1])
            if y1 > y0 and x1 > x0:
                np.maximum(canvas[y0:y1, x0:x1], img_row[y0 - oy:y1 - oy, x0 - ox:x1 - ox], out=canvas[y0:y1, x0:x1])
                placed += 1
                rows_done.append((line, X, X + nw, p_loc, q["drift"]))
        slot_base += len(slots)
        done: dict = {}                                       # pieces of one line (plan rows) are checked together
        for line, xa_, xb_, p_, dpx in rows_done:
            e = done.setdefault(line["i"], [line, xa_, xb_, p_, dpx])
            e[1], e[2] = min(e[1], xa_), max(e[2], xb_)
        check = []                                             # measured on the result: how each new line sits
        for line, xa_, xb_, p_, dpx in done.values():
            c = line["poly"]
            xs = np.arange(max(0, int(xa_)), min(W, int(xb_)))
            if not len(xs):
                continue
            yl = c[0] + c[1] * xs + c[2] * xs * xs
            ta, tb = int(max(0, yl.min() - 0.95 * p_)), int(min(H, yl.max() + 0.2 * p_))
            yy, xx = np.nonzero(canvas[ta:tb, xs[0]:xs[-1] + 1] > 0.3)
            if len(yy) < 30:
                continue
            above = yl[xx] - (ta + yy)
            lo, hi = np.percentile(above, 6), np.percentile(above, 96)
            check.append({"line": line["i"], "sits": round(float(lo / p_), 3), "height": round(float((hi - lo) / p_), 3),
                          "drift_mm": round(float(dpx / ppm_p), 2)})
        alpha, mult = style_alpha(canvas, a, ppm_p)
        grain = a.grain if a.grain is not None else paper_noise(rgb) * 0.9
        soften = a.soften if a.soften is not None else (0.35 if pj.get("scale_from", "").startswith("exact") else 0.6)
        fac = blend(rgb, alpha, color, mult, soften, grain, a.seed + si)
        if pj.get("flat_to_source"):                          # write the ink back into the original photo
            photo, photo_meta = open_image(pj["source"])
            prgb = np.asarray(photo.convert("RGB"), np.float32)
            coeffs = tuple(pj["source_to_flat"])
            chans = [np.asarray(Image.fromarray(fac[..., c].astype(np.float32), "F").transform(photo.size, Image.PERSPECTIVE, coeffs,
                                                                                               Image.BILINEAR, fillcolor=1.0), np.float32)
                     for c in range(3)]
            res = np.clip(prgb * np.stack(chans, 2), 0, 255)
            ink_mask = np.stack(chans, 2).min(2) < 0.9
        else:
            res = np.clip(rgb * fac, 0, 255)
            ink_mask = alpha > 0.1
        out = a.out if si == 0 else str(Path(a.out).with_name(f"{Path(a.out).stem}-{si + 1}{Path(a.out).suffix}"))
        outs.append(finish(Image.fromarray(res.round().astype(np.uint8), "RGB"), a, pages, out,
                           {"sheet": si + 1, "paper": pj.get("source"), "lines_written": placed,
                            "first_line": slots[0]["i"] if slots else None, "check": check}, ink_mask,
                           {"drift": drift_mark} if drift_mark else None))
        placed_total += placed
    result = {"outputs": outs, "rows": len(seq), "rows_placed": placed_total, "generated_pitch_px": round(pitch_g, 2),
              "ink_rgb": color, "ink_from": color_why, "lift": round(lift_f, 3), "lift_from": lift_why,
              "drift": {"mode": drift_mode, "amount": a.drift_amount, "seed": a.seed,
                        "offsets_mm": [round(float(drift_mm[k]), 2) for k in slot_ids if k in drift_mm],
                        "reanchored_lines": [k + 1 for k in resets], **({"note": drift_note} if drift_note else {})},
              "check_help": "check: per new line, where the ink bottom sits above the line and how tall the writing is, in line "
                            "spacings (same measure as paper.json existing_writing); drift_mm = how far its left edge was moved "
                            "right (natural, uneven left edges; --drift none keeps them straight)"}
    if ew:
        result["existing_writing"] = {"sits": ew.get("lift"), "height": ew.get("height")}
    if size_hint:
        result["size_hint"] = size_hint
    result["next"] = "Look at the result (zoom into a few lines) before handing it over."
    emit(result)


# ── page: whole pages over a same-size sheet ────────────────────────────────

def cmd_page(a) -> None:
    pages, _, _ = load_pages(a.pages, a.job)
    sheet_info = load_paper(a.sheet) if a.sheet else {}
    onto = a.onto or sheet_info.get("analyzed_image")
    if not onto:
        die("give --onto IMAGE (or --sheet blanks.json / paper.json from paper.py)")
    base_img, _ = open_image(onto)
    rgb = np.asarray(base_img.convert("RGB"), np.float32)
    H, W = rgb.shape[:2]
    pw, ph = parse_size_mm(a.paper_size) if a.paper_size else (sheet_info.get("page_mm") or (210.0, 297.0))
    ppm_t = W / pw
    color, color_why = ink_color(a, pages, sheet_info)
    outs = []
    for i, p in enumerate(pages):
        ppm_g = p["ppm"]
        dx, dy = (float(v) for v in a.offset_mm.split(",")) if a.offset_mm else (0.0, 0.0)
        crop = p["alpha"][:int(round(ph * ppm_g)), :int(round(pw * ppm_g))]
        k = ppm_t / ppm_g
        scaled = np.asarray(Image.fromarray(crop, "F").resize((max(1, round(crop.shape[1] * k)), max(1, round(crop.shape[0] * k))),
                                                              Image.LANCZOS), np.float32).clip(0, 1)
        canvas = np.zeros((H, W), np.float32)
        ox, oy = int(round(dx * ppm_t)), int(round(dy * ppm_t))
        h, w = min(H - max(0, oy), scaled.shape[0]), min(W - max(0, ox), scaled.shape[1])
        canvas[max(0, oy):max(0, oy) + h, max(0, ox):max(0, ox) + w] = scaled[:h, :w]
        alpha, mult = style_alpha(canvas, a, ppm_t)
        fac = blend(rgb, alpha, color, mult, a.soften if a.soften is not None else 0.45,
                    a.grain if a.grain is not None else paper_noise(rgb) * 0.9, a.seed + i)
        if sheet_info.get("flat_to_source"):
            photo, _ = open_image(sheet_info["source"])
            prgb = np.asarray(photo.convert("RGB"), np.float32)
            chans = [np.asarray(Image.fromarray(fac[..., c].astype(np.float32), "F").transform(photo.size, Image.PERSPECTIVE,
                                                                                               tuple(sheet_info["source_to_flat"]),
                                                                                               Image.BILINEAR, fillcolor=1.0), np.float32)
                     for c in range(3)]
            res = np.clip(prgb * np.stack(chans, 2), 0, 255)
        else:
            res = np.clip(rgb * fac, 0, 255)
        out = a.out if i == 0 else str(Path(a.out).with_name(f"{Path(a.out).stem}-{i + 1}{Path(a.out).suffix}"))
        ink_mask = (np.stack(chans, 2).min(2) < 0.9) if sheet_info.get("flat_to_source") else (alpha > 0.1)
        outs.append(finish(Image.fromarray(res.round().astype(np.uint8), "RGB"), a, [p], out, {"page": p["path"], "ink_from": color_why},
                           ink_mask))
    emit({"outputs": outs, "px_per_mm": round(ppm_t, 3), "paper_mm": [pw, ph]})


# ── place: into a box ───────────────────────────────────────────────────────

def cmd_place(a) -> None:
    pages, _, _ = load_pages([a.ink], None)
    p = pages[0]
    alpha = p["alpha"]
    if a.from_mm:                                         # only this part of the page (e.g. one answer out of several)
        fx, fy, fw, fh = (float(v) * p["ppm"] for v in a.from_mm.split(","))
        keep = np.zeros_like(alpha)
        ya_, xa_ = int(max(0, fy)), int(max(0, fx))
        keep[ya_:int(fy + fh), xa_:int(fx + fw)] = 1
        alpha = alpha * keep
    ys, xs = np.nonzero(alpha > 0.08)
    if not len(xs):
        die("no handwriting found in " + a.ink + (" inside --from-mm" if a.from_mm else ""))
    pad = int(0.8 * p["ppm"])
    x0, x1 = max(0, xs.min() - pad), min(alpha.shape[1], xs.max() + pad + 1)
    y0, y1 = max(0, ys.min() - pad), min(alpha.shape[0], ys.max() + pad + 1)
    crop = alpha[y0:y1, x0:x1]
    base_img, _ = open_image(a.onto)
    rgb = np.asarray(base_img.convert("RGB"), np.float32)
    H, W = rgb.shape[:2]
    bx, by, bw, bh = (float(v) for v in a.box.split(","))
    ch, cw = crop.shape
    if a.fit == "width":
        k = bw / cw
    elif a.fit == "height":
        k = bh / ch
    elif a.fit == "scale":
        k = a.scale
    else:
        k = min(bw / cw, bh / ch)
    im = Image.fromarray(crop, "F").resize((max(1, round(cw * k)), max(1, round(ch * k))), Image.LANCZOS)
    if a.rotate:
        im = im.rotate(a.rotate, resample=Image.BICUBIC, expand=True, fillcolor=0.0)
    sc = np.asarray(im, np.float32).clip(0, 1)
    h, w = sc.shape
    ox = bx + (0 if a.align == "left" else (bw - w) / 2 if a.align == "center" else bw - w)
    oy = by + (0 if a.valign == "top" else (bh - h) / 2 if a.valign == "middle" else bh - h)
    ox, oy = int(round(ox)), int(round(oy))
    canvas = np.zeros((H, W), np.float32)
    cx0, cy0 = max(0, ox), max(0, oy)
    cx1, cy1 = min(W, ox + w), min(H, oy + h)
    if cx1 <= cx0 or cy1 <= cy0:
        die("the box is outside the image")
    canvas[cy0:cy1, cx0:cx1] = sc[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
    ppm_t = p["ppm"] * k
    alpha2, mult = style_alpha(canvas, a, ppm_t)
    color, color_why = ink_color(a, pages)
    fac = blend(rgb, alpha2, color, mult, a.soften if a.soften is not None else 0.45,
                a.grain if a.grain is not None else paper_noise(rgb) * 0.9, a.seed)
    res = np.clip(rgb * fac, 0, 255)
    emit(finish(Image.fromarray(res.round().astype(np.uint8), "RGB"), a, pages, a.out,
                {"scale": round(k, 4), "placed_px": [cx0, cy0, cx1 - cx0, cy1 - cy0], "ink_from": color_why}, alpha2 > 0.1))


# ── drift: uneven left edges on Inko pages handed over as they are ──────────

AREA_X_MM = {"ruled8": (24.0, 194.0), "ruled7": (24.0, 194.0), "letter": (22.0, 188.0), "blank": (20.0, 190.0)}
CELL_PAPERS = ("compo", "tian")


def plan_lines(plan: dict, page_i: int, ppm: float) -> tuple[list[dict], list[tuple[int, int, int, int]]]:
    """Visual lines of one page from the plan, in px: body items grouped by baseline (a plan row can be just a piece of a
    line); plus the rectangles of boxed items (answers in boxes / fields), which never move."""
    pages = (plan.get("plan") or {}).get("pages") or []
    items = pages[page_i] if page_i < len(pages) else []
    boxes, body = [], []
    for it in items:
        s = it["s"]
        if it.get("box"):
            boxes.append((int((it["x"] - 0.3 * s) * ppm), int((it["y"] - 1.1 * s) * ppm),
                          int(np.ceil((it["x"] + it["w"] + 0.3 * s) * ppm)), int(np.ceil((it["y"] + 0.45 * s) * ppm))))
        else:
            body.append(it)
    groups: list[list[dict]] = []
    for it in sorted(body, key=lambda it: it["y"]):
        g = groups[-1] if groups else None
        if g and abs(it["y"] - float(np.median([q["y"] for q in g]))) < 0.3 * max(it["s"], max(q["s"] for q in g)):
            g.append(it)
        else:
            groups.append([it])
    lines = []
    for g in groups:
        s = float(np.median([q["s"] for q in g]))
        base = float(np.median([q["y"] for q in g]))
        # each item's own baseline: a formula with a fraction sits higher than the line (its y is not the median)
        top = min(min(q["y"] - (q["m"]["a"] if q.get("m") else 0.92) * q["s"] for q in g), base - 0.95 * s)
        bot = max(max(q["y"] + (q["m"]["d"] if q.get("m") else 0.25) * q["s"] for q in g), base + 0.3 * s)
        first = min(g, key=lambda q: q["x"])
        lines.append({"base": base * ppm, "top": top * ppm, "bot": bot * ppm, "x0": first["x"] * ppm,
                      "x1": max(q["x"] + q["w"] for q in g) * ppm, "s": s * ppm, "i0": first.get("i"), "items": g})
    return lines, boxes


def margin_line_x(rgb: np.ndarray, paper_id: str | None, ppm: float) -> float | None:
    """Right edge (px) of a red vertical margin line in the left 40 % of the page (Inko's ruled papers: 24 mm)."""
    small = rgb[::4, ::4]
    red = (small[..., 0] - np.maximum(small[..., 1], small[..., 2])) > 18
    cols = np.flatnonzero(red.mean(0)[:int(0.4 * small.shape[1])] > 0.35)
    if len(cols):
        return float((cols[-1] + 1) * 4)
    return 24.3 * ppm if paper_id in ("ruled8", "ruled7") else None


def _lum(v: np.ndarray) -> np.ndarray:
    return v[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)


def _interp_across(rgb: np.ndarray, hole: np.ndarray, ys: np.ndarray, xs: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """Colour at hole pixels (ys, xs), linear between the nearest non-hole pixels along the row (axis 1) or column (0)."""
    n = hole.shape[axis]
    idx = np.broadcast_to(np.arange(n, dtype=np.int32).reshape((1, n) if axis == 1 else (n, 1)), hole.shape)
    lo = np.maximum.accumulate(np.where(hole, -1, idx), axis=axis)
    hi = np.flip(np.minimum.accumulate(np.flip(np.where(hole, n, idx), axis), axis=axis), axis)
    a, b = lo[ys, xs], hi[ys, xs]
    pos = xs if axis == 1 else ys
    ha, hb = a >= 0, b < n
    ac, bc = np.clip(a, 0, n - 1), np.clip(b, 0, n - 1)
    ca, cb = (rgb[ys, ac], rgb[ys, bc]) if axis == 1 else (rgb[ac, xs], rgb[bc, xs])
    t = np.where(ha & hb, (pos - a) / np.maximum(b - a, 1), np.where(ha, 0.0, 1.0)).astype(np.float32)[:, None]
    return ca * (1 - t) + cb * t, ha | hb


def _grow1(m: np.ndarray) -> np.ndarray:
    g = m.copy()
    g[1:] |= m[:-1]
    g[:-1] |= m[1:]
    return g


def paper_under_ink(rgb: np.ndarray, hole: np.ndarray, paper_rgb) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The paper under the handwriting -> (ys, xs, colours). Printed lines run the whole page, so they show up in the
    row / column medians: a pixel on a horizontal line (rule, grid) is interpolated along its row, one on a vertical line
    (margin, grid) along its column (crossings: the darker), plain paper along the row. Interpolation never starts from a
    pixel of a line running the other way — else a stroke ending on a grid line would smear its colour across the gap."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)                  # rows / columns that are all ink
        Lm = np.where(hole, np.nan, luminance(rgb))
        rowm, colm = np.nanmedian(Lm[:, ::2], axis=1), np.nanmedian(Lm[::2], axis=0)
        paper_l = float(np.nanmedian(Lm[::2, ::2]))
    srow, scol = _grow1(rowm < paper_l - 6), _grow1(colm < paper_l - 6)  # printed lines (+ their soft edges)
    ys, xs = np.nonzero(hole)
    est, ok = _interp_across(rgb, hole | scol[None, :], ys, xs, 1)
    est[~ok] = np.array(paper_rgb, np.float32)
    sel = np.flatnonzero(scol[xs])
    if len(sel):
        v, okv = _interp_across(rgb, hole | srow[:, None], ys[sel], xs[sel], 0)
        use = okv & (~srow[ys[sel]] | (_lum(v) < _lum(est[sel])))
        est[sel[use]] = v[use]
    return ys, xs, est


def _shift1(row: np.ndarray, k: int) -> np.ndarray:
    W = row.shape[0]
    r = np.ones_like(row)
    if -W < k < W:
        if k >= 0:
            r[k:] = row[:W - k]
        else:
            r[:W + k] = row[-k:]
    return r


def shift_rows(fac: np.ndarray, dx: np.ndarray) -> np.ndarray:
    """out[y, x] = fac[y, x - dx[y]] (linear interpolation); uncovered pixels become 1 = no ink."""
    out = fac.copy()
    inky = (fac < 0.999).reshape(fac.shape[0], -1).any(1)
    for y in np.flatnonzero(inky & (np.abs(dx) > 1e-3)):
        i = int(np.floor(dx[y]))
        f = float(dx[y] - i)
        out[y] = (1 - f) * _shift1(fac[y], i) + f * _shift1(fac[y], i + 1)
    return out


def dx_field(H: int, lines: list[dict], offs: list[float]) -> np.ndarray:
    """Horizontal shift per pixel row: constant over each line's band, linear in between, fading out within half a
    character height above the first line and below the last."""
    if not lines:
        return np.zeros(H)
    bands = sorted([[l["top"], l["bot"], d, l["s"]] for l, d in zip(lines, offs)], key=lambda b: b[0])
    for p, q in zip(bands, bands[1:]):                         # overlapping neighbours meet halfway
        if p[1] > q[0]:
            p[1] = q[0] = min(q[1], (p[1] + q[0]) / 2)
    xp, fp = [bands[0][0] - 0.5 * bands[0][3]], [0.0]
    for t, b, d, _s in bands:
        xp += [t, max(t, b)]
        fp += [d, d]
    xp.append(bands[-1][1] + 0.5 * bands[-1][3])
    fp.append(0.0)
    xp = np.maximum.accumulate(np.asarray(xp, np.float64) + np.arange(len(xp)) * 1e-6)
    return np.interp(np.arange(H, dtype=np.float64), xp, fp, left=0.0, right=0.0)


def pages_pdf(paths: list[Path], out: Path) -> None:
    """The pages as one PDF at paper size with the AIGC metadata (/AIGC + XMP) — pdf.py's writer."""
    import pdf as PDF
    pages, aigc = [], None
    for f in paths:
        img, meta = open_image(f)
        aigc = aigc or meta.get("aigc")
        pw, ph = PDF.page_size_pt(img, meta, "auto")
        k = min(pw / img.width, ph / img.height)
        w, h = img.width * k, img.height * k
        data, im = PDF.encode_image(img, 90, False)
        pages.append((data, im, (pw, ph), ((pw - w) / 2, (ph - h) / 2, w, h)))
    info = {"Title": out.stem, "Creator": SKILL_TAG, "Producer": PRODUCER, "CreationDate": time.strftime("D:%Y%m%d%H%M%S")}
    if aigc:
        info["AIGC"] = json.dumps(aigc, ensure_ascii=False)
        info["Keywords"] = "AIGC; AI生成 · Inko"
    PDF.write_pdf(pages, out, info, xmp_packet(aigc) if aigc else None)


def cmd_drift(a) -> None:
    files, plan, layout, params = job_inputs(a.pages, a.job)
    if a.plan:
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    if a.layout:
        layout = json.loads(Path(a.layout).read_text(encoding="utf-8"))
        layout = layout.get("layout", layout)
    plan = json.loads(json.dumps(plan)) if plan else None                # a copy: its x's get the shifts for the output
    paper_id = (layout or {}).get("paperId") or (((plan or {}).get("plan") or {}).get("paper") or {}).get("id")
    if paper_id in CELL_PAPERS:
        die(f"'{paper_id}' paper has one character per cell: shifting lines would push them out of their cells — leave it as it is")
    out = Path(a.out)
    single = len(files) == 1 and out.suffix.lower() == ".png"
    if out.suffix and not single:
        die("-o: give a folder (the pages keep their names there), or a .png file when there is one page")
    if a.drift_amount < 0:
        die("--drift-amount must be >= 0 (0.5 = half as much)")
    targets = [out] if single else [out / (f.stem + ".png") for f in files]
    if len({t.resolve() for t in targets}) < len(targets):
        die("two pages have the same file name, they would overwrite each other in -o: rename them or run them one at a time")
    for f, t in zip(files, targets):
        if t.resolve() == f.resolve() and not a.force:
            die(f"{t} is the original page: write the drifted pages somewhere else (-o drifted/), or add --force")
    d = (layout or {}).get("d") or {}
    lift = float(d.get("lift", LIFT))
    size = params.get("size")
    pages = []
    for i, f in enumerate(files):                               # 1) lines, label, margin of every page
        img, meta = open_image(f)
        if meta.get("drift") and not a.force:
            die(f"{f} was drifted already ({meta['drift'].get('by', 'compose.py')}, {meta['drift'].get('mode')}): start from the "
                "original page, or add --force to drift it once more")
        sep = INK.separate(img)
        if sep["is_layer"]:
            die(f"{f} is a transparent ink layer: drift the page itself, then extract the ink from the result")
        ppm = px_per_mm_of(img, meta)
        alpha = sep["alpha"]
        lab = sep["label_bbox"]
        if lab:
            x0, y0, x1, y1 = lab
            alpha[y0:y1, x0:x1] = 0                               # the label is never handwriting
        if plan:
            lines, boxes = plan_lines(plan, i, ppm)
            how = "plan.json"
        else:
            boxes = []
            if paper_id in INKO_RULES:
                rules, sp = page_rules({"ppm": ppm}, {"paperId": paper_id})
                rows, how = rule_rows(alpha, rules, sp, lift), "the page's ruled lines"
            else:                                                 # from the ink (grid squares are not rows)
                prior = PLAIN_CHAR_PX[size] * PLAIN_LINE_ADV * ppm / (300 / 25.4) if size in PLAIN_CHAR_PX else None
                rows, how = detect_rows(alpha, ppm, prior), "the ink (no plan.json)"
            lines = [{"base": r["base"], "top": r["base"] - 0.95 * r["s"], "bot": r["base"] + 0.3 * r["s"], "x0": r["x0"],
                      "x1": r["x1"], "s": r["s"], "i0": None} for r in rows]
        lines.sort(key=lambda l: l["base"])
        pages.append({"path": f, "meta": meta, "ppm": ppm, "size": img.size, "hole": alpha > 0.03, "label": lab, "boxes": boxes,
                      "paper_rgb": sep["paper_rgb"], "lines": lines, "how": how,
                      "margin": margin_line_x(sep["rgb"], paper_id, ppm)})
        del sep, alpha
    # 2) one offset per written line, creeping on across pages; re-anchored after blank lines and at new problems
    seq = [(pi, l) for pi, p in enumerate(pages) for l in p["lines"]]
    ppm0 = pages[0]["ppm"]
    pitch = gen_pitch([[{"base": l["base"]} for l in p["lines"]] for p in pages], layout, ppm0) / ppm0     # mm
    bases = [None if (k and seq[k - 1][0] != pi) else l["base"] / pages[pi]["ppm"] for k, (pi, l) in enumerate(seq)]
    resets = drift_resets(bases, pitch, [l.get("i0") for _, l in seq], (layout or {}).get("text") if plan else None)
    ser = drift_series(len(seq), a.drift, a.drift_amount, drift_seed(a.seed, page_key(pages[0]["meta"], files[0])), resets)
    s_mm = float(np.median([l["s"] / pages[pi]["ppm"] for pi, l in seq])) if seq else 5.0
    area = AREA_X_MM.get(paper_id or "", (None, None))                  # writing area (mm): the layout's margins, else the paper's
    mg = list(d.get("margins") or []) + [None] * 4
    for p in pages:
        k, W = p["ppm"], p["size"][0]
        p["sd"] = s_mm * k
        if not p["lines"]:
            continue
        left_a = mg[3] if mg[3] is not None else area[0]
        right_a = (W / k - mg[1]) if mg[1] is not None else area[1]
        # with a margin line: never left of where the line was (can't cross it); else at most a hair left of the writing
        # area; on the right at most a hair past it
        p["lo"] = None if p["margin"] is not None else \
            min(([left_a * k] if left_a is not None else []) + [q["x0"] for q in p["lines"]]) - 0.2 * p["sd"]
        p["hi"] = min(max(([right_a * k] if right_a is not None else []) + [q["x1"] for q in p["lines"]]) + 0.3 * p["sd"], W - 2 * k)
    for (pi, l), dv in zip(seq, ser):
        p = pages[pi]
        lo, hi = (l["x0"] if p["lo"] is None else p["lo"]), p["hi"]
        if p["lo"] is None:          # a margin line: the wobble folds back to the right — clipping it at 0 would leave
            dv = abs(dv)             # several lines at exactly the old, ruler-straight edge
        for bx0, by0, bx1, by1 in p["boxes"]:                   # a box beside the line (wrap: true): don't write into it
            if by0 < l["bot"] and by1 > l["top"]:
                if bx0 >= l["x1"] - 1:
                    hi = min(hi, bx0)
                elif bx1 <= l["x0"] + 1:
                    lo = max(lo, bx1)
                else:                                           # the line runs on both sides of the box: keep it
                    dv = 0.0
        l["dx"] = clamp_drift(dv * p["sd"], l["x0"], l["x1"], lo, hi)
    # 3) move the ink of each page, never the paper
    outs, written = [], []
    for p, t in zip(pages, targets):
        img, meta = open_image(p["path"])
        rgb = np.asarray(img.convert("RGB"), np.float32)
        H, W = rgb.shape[:2]
        res_full = rgb.copy()
        hole = np.asarray(Image.fromarray(p["hole"].astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5)), np.uint8) > 0
        if p["label"]:
            x0, y0, x1, y1 = p["label"]
            hole[y0:y1, x0:x1] = False
        offs = [l["dx"] for l in p["lines"]]
        dx = dx_field(H, p["lines"], offs)
        rows = np.flatnonzero(hole.any(1) & (np.abs(dx) > 1e-3))
        if len(rows):
            r0, r1 = int(rows[0]), int(rows[-1]) + 1
            sub, hs, dxs = rgb[r0:r1], hole[r0:r1], dx[r0:r1]
            ys, xs, est = paper_under_ink(sub, hs, p["paper_rgb"] or (255, 255, 255))
            fac = np.ones_like(sub)
            fac[ys, xs] = np.clip(sub[ys, xs] / np.maximum(est, 1.0), 0, 1)
            base = sub.copy()
            base[ys, xs] = est                                    # the paper with the handwriting lifted off
            bm = np.zeros(hs.shape, bool)
            for bx0, by0, bx1, by1 in p["boxes"]:                 # boxed answers stay exactly where they are
                bm[max(0, by0 - r0):max(0, by1 - r0), max(0, bx0):max(0, bx1)] = True
            kept = fac[bm].copy()
            fac[bm] = 1.0
            warped = shift_rows(fac, dxs)
            res = base * warped
            res[bm] *= kept
            same = (bm & (warped.min(2) >= 0.999)) | (np.abs(dxs) <= 1e-3)[:, None]
            res[same] = sub[same]                                 # untouched pixels stay bit-identical
            res_full[r0:r1] = res
        if p["label"]:                                            # the label goes back unchanged
            x0, y0, x1, y1 = p["label"]
            res_full[y0:y1, x0:x1] = rgb[y0:y1, x0:x1]
        mm = [round(float(v / p["ppm"]), 2) for v in offs]
        mark = {"by": "compose.py drift", "mode": a.drift, "amount": a.drift_amount, "seed": a.seed, "offsets_mm": mm}
        dpi = float(meta["dpi"][0]) if meta.get("dpi") else p["ppm"] * 25.4
        save_image(Image.fromarray(np.clip(res_full, 0, 255).round().astype(np.uint8), "RGB"), t,
                   {**meta, "drift": mark} if any(mm) else meta, dpi=dpi)       # nothing moved: not marked as drifted
        written.append(t)
        outs.append({"page": str(p["path"]), "out": str(t), "rows_from": p["how"], "lines": len(p["lines"]), "offsets_mm": mm,
                     "boxed_items_kept": len(p["boxes"]), "margin_line": p["margin"] is not None,
                     "visible_label": bool(p["label"]), "aigc_metadata": bool(meta.get("aigc"))})
        for l in p["lines"]:                                      # the plan follows the ink
            for it in l.get("items", []):
                it["x"] = round(it["x"] + l["dx"] / p["ppm"], 3)
    pdf_out = None
    if not single and a.job:
        jd = Path(a.job)
        for name in ("layout.json", "job.json", "text.txt"):
            if (jd / name).exists() and (jd / name).resolve() != (out / name).resolve():
                shutil.copy(jd / name, out / name)
        if plan:
            plan["drift"] = {"by": "compose.py drift", "mode": a.drift, "amount": a.drift_amount, "seed": a.seed}
            (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        if (jd / "inko.pdf").exists():
            pdf_out = out / "inko.pdf"
            pages_pdf(written, pdf_out)
    if not seq:
        note("found no lines of body text to move (everything in boxes, or no handwriting) — the pages are unchanged")
    emit({"outputs": outs, "pdf": str(pdf_out) if pdf_out else None, "mode": a.drift, "amount": a.drift_amount, "seed": a.seed,
          "reanchored_lines": [k + 1 for k in resets],
          "help": "offsets_mm: how far each written line (top to bottom) was moved right; only the handwriting moved — "
                  "ruled lines, margin line, boxed answers and the label are untouched",
          "next": "Look at the pages (zoom into the left edges and where strokes cross ruled lines) before handing them over. "
                  "Less: --drift-amount 0.5 · another pattern: --seed N · wobble without creeping: --drift random."})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def common(s):
        s.add_argument("-o", "--out", required=True, help=".jpg / .png / .webp")
        s.add_argument("--color", default="auto",
                       help=f"auto (default: the job's pen colour, else the writing already on the paper, else a real-pen darkness) | "
                            f"match | page (the generated ink as is) | {' | '.join(INK.INK_COLORS)} | #RRGGBB")
        s.add_argument("--texture", default="none", choices=INK.TEXTURES)
        s.add_argument("--weight", type=float, default=0.0, help="-1..1 thinner/bolder strokes")
        s.add_argument("--darkness", type=float, default=0.0, help="-1..1")
        s.add_argument("--soften", type=float, help="blur the ink by this many px to match the photo (default: auto)")
        s.add_argument("--grain", type=float, help="sensor noise on the ink, in grey levels (default: measured from the paper)")
        s.add_argument("--label-corner", default="auto", choices=["auto", "br", "bl", "tr", "tl"],
                       help="where the 「AI生成 · Inko」 label goes (auto: bottom-right unless the new handwriting is there)")
        s.add_argument("--seed", type=int, default=7)
        s.add_argument("-q", "--quality", type=int, default=92)
    s = sp.add_parser("lines", help="snap handwriting onto the lines of a ruled paper")
    s.add_argument("pages", nargs="*", help="generated page PNGs (or use --job)")
    s.add_argument("--job", help="folder from inko.py generate (page-*.png + plan.json)")
    s.add_argument("--plan", help="plan JSON from `inko.py layout --out` (if not in the job folder)")
    s.add_argument("--layout", help="the layout JSON used to generate (if not in the job folder)")
    s.add_argument("--paper", nargs="+", required=True, help="paper.json from paper.py (several = several sheets in order)")
    s.add_argument("--start-line", type=int, help="first line number to write on (see the overlay; default: first free line)")
    s.add_argument("--every", type=int, default=1, help="2 = write on every other line")
    s.add_argument("--over-writing", action="store_true", help="also use lines that already have writing")
    s.add_argument("--lift", type=float, help="baseline height above the line, in line spacings (default: like the writing "
                                            "already on the paper, else 0.14)")
    s.add_argument("--x-offset", type=float, default=0.0, help="shift right by this many line spacings")
    s.add_argument("--scale", type=float, default=1.0, help="extra size factor (0.9 = 10%% smaller)")
    s.add_argument("--drift", default="natural", choices=DRIFT_MODES,
                   help="left edges: natural (default: creep right line by line, re-anchored at a new problem) | random "
                        "(small wobble) | none (ruler-straight, as generated)")
    s.add_argument("--drift-amount", type=float, default=1.0, help="scale of the drift (0.5 = half, 0 = none)")
    common(s)
    s = sp.add_parser("page", help="lay whole pages over a same-size sheet")
    s.add_argument("pages", nargs="*")
    s.add_argument("--job")
    s.add_argument("--onto", help="scan / straightened photo of the sheet")
    s.add_argument("--sheet", help="blanks.json or paper.json from paper.py (writes back into the original photo)")
    s.add_argument("--paper-size", help="the sheet's size (A4 default)")
    s.add_argument("--offset-mm", help="dx,dy nudge in mm")
    common(s)
    s = sp.add_parser("place", help="put handwriting into a box of any image")
    s.add_argument("ink", help="a generated page or an ink layer from ink.py extract")
    s.add_argument("--onto", required=True)
    s.add_argument("--box", required=True, help="x,y,w,h in pixels of --onto")
    s.add_argument("--fit", default="contain", choices=["contain", "width", "height", "scale"])
    s.add_argument("--scale", type=float, default=1.0, help="with --fit scale")
    s.add_argument("--align", default="left", choices=["left", "center", "right"])
    s.add_argument("--valign", default="top", choices=["top", "middle", "bottom"])
    s.add_argument("--rotate", type=float, default=0.0, help="degrees, counter-clockwise")
    s.add_argument("--from-mm", help="x,y,w,h: take only this region of the page (mm) — e.g. the better version of one answer")
    common(s)
    s = sp.add_parser("drift", help="uneven, natural left edges on Inko pages handed over as they are (only the ink moves)")
    s.add_argument("pages", nargs="*", help="page PNGs (or use --job)")
    s.add_argument("--job", help="folder from inko.py generate (page-*.png + plan.json / layout.json, inko.pdf)")
    s.add_argument("--plan", help="plan JSON (if not in the job folder): exact lines, boxed answers stay put")
    s.add_argument("--layout", help="the layout JSON used to generate (if not in the job folder)")
    s.add_argument("-o", "--out", required=True, help="output folder (same file names; plus inko.pdf / plan.json for a job), "
                                                     "or a .png file for a single page")
    s.add_argument("--drift", default="natural", choices=["natural", "random"],
                   help="natural (default): creep right line by line, re-anchored at a new problem | random: small wobble")
    s.add_argument("--drift-amount", type=float, default=1.0, help="scale of the drift (0.5 = half)")
    s.add_argument("--seed", type=int, default=7, help="another number = another pattern (mixed with the job's id)")
    s.add_argument("--force", action="store_true", help="drift pages that were drifted before / overwrite the input")
    a = ap.parse_args()
    {"lines": cmd_lines, "page": cmd_page, "place": cmd_place, "drift": cmd_drift}[a.cmd](a)


if __name__ == "__main__":
    main()
