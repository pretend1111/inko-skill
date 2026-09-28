#!/usr/bin/env python3

"""Adjust line positions on standard flat Inko pages.

Only the ink moves; page geometry, AI labels and metadata stay intact.
Optional: python compose.py drift --job RUN -o edited
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import re
import shutil
import time
import warnings
import zlib
from pathlib import Path

import numpy as np

from _common import PRODUCER, SKILL_TAG, Image, ImageFilter, die, emit, gaussian, luminance, note, open_image, px_per_mm_of, save_image, xmp_packet
import ink as INK

LIFT = 0.14


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

INKO_RULES = {"ruled8": (34.0, 274.0, 8.0), "ruled7": (33.0, 276.0, 7.0)}

PLAIN_CHAR_PX = {"small": 71, "medium": 83, "large": 100}

PLAIN_LINE_ADV = 1.75

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

DRIFT_MODES = ("natural", "random", "none")

DRIFT_CAP = 0.45

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
    cmd_drift(a)


if __name__ == "__main__":
    main()
