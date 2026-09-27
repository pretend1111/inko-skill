#!/usr/bin/env python3
"""Put Inko handwriting onto real paper — the user's own ruled sheet, a worksheet, or any photo.

    python compose.py lines --job inko-output/<run>/ --paper paper.json -o final.jpg [--start-line 4]
        Snap every generated line onto the ruled lines found by `paper.py analyze` (or drawn by `paper.py make --json`).
        Follows tilt, page curl and perspective; photos straightened by paper.py are written back into the ORIGINAL photo.
        Text that runs past the last line continues on the next --paper (or another copy of the last one): -2, -3 …

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
from pathlib import Path

import numpy as np

from _common import (Image, LABEL_MIN_FRAC, apply_label, die, emit, find_label_bbox, gaussian, label_box, label_from_b64, label_from_page,
                     luminance, merge_meta, note, open_image, parse_size_mm, px_per_mm_of, save_image)
import ink as INK

LIFT = 0.14                  # Inko's own ruled papers: the writing's baseline sits 0.14 line-spacings above the line


# ── generated pages ─────────────────────────────────────────────────────────

def _natural(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def load_pages(paths: list[str], job: str | None) -> tuple[list[dict], dict | None, dict | None]:
    """-> ([{path, alpha, ink_rgb, label, meta, ppm, size, params}], plan, layout)"""
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
                    "a": a * ppm, "d": d * ppm, "s": s * ppm})
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


def finish(result: Image.Image, a, pages: list[dict], out: str, extra: dict, ink: np.ndarray | None = None) -> dict:
    """Re-apply the visible label (if the pages had one; clear of the new handwriting) and save with the AIGC metadata."""
    has_label = any(p["label"] is not None for p in pages)
    corner = None
    if has_label:
        lab = next(p["label"] for p in pages if p["label"] is not None)
        corner = pick_label_corner(a, result.size, ink, lab)
        result = apply_label(result, lab, frac=LABEL_MIN_FRAC * 1.06, where=corner)
    meta = merge_meta(*[p["meta"] for p in pages])
    meta = {**meta, "ink": None, "label_png": None}
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
        placed = 0
        rows_done: list = []
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
            scaled = np.asarray(Image.fromarray(crop, "F").resize((nw, nh), Image.LANCZOS), np.float32).clip(0, 1)
            B0 = _poly(line)
            lift = lift_f * p_loc
            B = (lambda x, B0=B0, lift=lift: B0(x) - lift)
            wx0 = line.get("write_x0") if line.get("write_x0") is not None else pj["write_x0_px"]
            wx1 = line.get("write_x1") if line.get("write_x1") is not None else pj["write_x1_px"]
            X = wx0 + (xa - left_g) * sc + a.x_offset * p_loc
            if X + nw > wx1 + 0.6 * p_loc:
                note(f"line {line['i']}: generated line is {X + nw - wx1:.0f}px longer than the paper's writing area — "
                     "use the suggested layout's margins (or --scale 0.95)")
            base_c = (r["base"] - ya) * sc
            img_row, ox, oy = warp_row(scaled, base_c, X, B, (base_c + 2, nh - base_c + 2))
            y0, x0 = max(0, oy), max(0, ox)
            y1, x1 = min(H, oy + img_row.shape[0]), min(W, ox + img_row.shape[1])
            if y1 > y0 and x1 > x0:
                np.maximum(canvas[y0:y1, x0:x1], img_row[y0 - oy:y1 - oy, x0 - ox:x1 - ox], out=canvas[y0:y1, x0:x1])
                placed += 1
                rows_done.append((line, X, X + nw, p_loc))
        slot_base += len(slots)
        check = []                                             # measured on the result: how each new line sits
        for line, xa_, xb_, p_ in rows_done:
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
            check.append({"line": line["i"], "sits": round(float(lo / p_), 3), "height": round(float((hi - lo) / p_), 3)})
        ppm_p = pj.get("px_per_mm") or ppm_g
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
                            "first_line": slots[0]["i"] if slots else None, "check": check}, ink_mask))
        placed_total += placed
    result = {"outputs": outs, "rows": len(seq), "rows_placed": placed_total, "generated_pitch_px": round(pitch_g, 2),
              "ink_rgb": color, "ink_from": color_why, "lift": round(lift_f, 3), "lift_from": lift_why,
              "check_help": "check: per new line, where the ink bottom sits above the line and how tall the writing is, in line "
                            "spacings (same measure as paper.json existing_writing)"}
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
    a = ap.parse_args()
    {"lines": cmd_lines, "page": cmd_page, "place": cmd_place}[a.cmd](a)


if __name__ == "__main__":
    main()
