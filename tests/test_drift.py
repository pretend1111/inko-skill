#!/usr/bin/env python3
"""Tests for natural left edges: `compose.py drift` (Inko pages as delivered).
Offline, CPU only, no API key.

    python tests/test_drift.py            # outputs in tests/_out/drift/

Synthetic "Inko pages" (text drawn in dark gray + Inko's gray label + AIGC metadata + a matching plan.json) on blank,
ruled (blue rules every 8 mm, red margin at 24 mm, like Inko's ruled8) and 5 mm grid paper.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import json
import os
import shutil
import subprocess
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
S = ROOT / "inko-handwriting" / "scripts"
OUT = HERE / "_out" / "drift"
sys.path.insert(0, str(S))

import numpy as np  # noqa: E402

from _common import (Image, ImageDraw, ImageFilter, apply_label, cjk_font, find_label_bbox, label_from_page, open_image,  # noqa: E402
                     save_image, text_height)

PPM = 300 / 25.4
INK = (40, 40, 44)
RESULTS: list[tuple[str, bool, str]] = []


def aigc(pid: str) -> dict:
    return {"Label": "1", "ContentProducer": "Inko (inkotype.com)", "ProduceID": pid, "ReservedCode1": "", "ContentPropagator": "inkotype.com",
            "PropagateID": pid, "ReservedCode2": ""}


def run(*args) -> tuple[int, dict, str]:
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        js = json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        js = {"_stdout": r.stdout}
    return r.returncode, js, r.stderr


def ok(*args) -> dict:
    code, js, err = run(*args)
    assert code == 0, f"{' '.join(map(str, args))} failed ({code}):\n{err[-1500:]}"
    return js


def test(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, False, f"{e}\n{traceback.format_exc(limit=3)}"))
        return fn
    return deco


def lum(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def rgb_of(path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(np.int16)


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * r + 1))) > 0


def build_page(folder: Path, lines: list[str], *, paper: str = "blank", s_mm: float = 7.0, pitch_mm: float = 11.9,
               left_mm: float = 20.0, first_base_mm: float = 40.0, boxes=(), pid: str = "drift-test", plan: bool = True) -> dict:
    """page-1.png (+ plan.json, layout.json): text in dark gray, the gray label, AIGC metadata. boxes: [(text, x, y, id)] in mm."""
    from paper import make_paper
    folder.mkdir(parents=True, exist_ok=True)
    if paper == "ruled8":                                    # rules 34 … 274 mm every 8 mm, red margin line at 24 mm
        im, _ = make_paper("ruled", (210, 297), 300, 8.0, "blue", "white", 24.0, 26.0, 23.0, 10.0)
    elif paper == "grid":
        im, _ = make_paper("grid", (210, 297), 300, 5.0, "blue", "white", None, 20.0, 25.0, 10.0)
    else:
        im = Image.new("RGB", (2480, 3508), (253, 253, 250))
    im.save(folder.parent / f"{folder.name}-paper.png")                 # the empty paper, to check what's left under the ink
    d = ImageDraw.Draw(im)
    font = cjk_font(int(s_mm * PPM), kai=True)
    items, i, bases = [], 0, []
    for row, t in enumerate(lines):
        base = first_base_mm + row * pitch_mm
        bases.append(base)
        d.text((left_mm * PPM, base * PPM), t, font=font, fill=INK, anchor="ls")
        x = left_mm
        for ch in t:
            w = font.getlength(ch) / PPM
            items.append({"i": i, "j": i + 1, "c": ch, "x": round(x, 3), "y": base, "s": s_mm, "w": round(w, 3), "r": 0, "h": 0,
                          "st": "", "row": row})
            x += w
            i += 1
        i += 1                                                # the "\n"
    box_rects = []
    f2 = cjk_font(int(4 * PPM), kai=True)
    for k, (t, bx, by, bid) in enumerate(boxes):
        d.text((bx * PPM, by * PPM), t, font=f2, fill=INK, anchor="ls")
        x = bx
        for n, ch in enumerate(t):
            w = f2.getlength(ch) / PPM
            items.append({"i": 10000000 + 100000 * k + n, "j": 10000001 + 100000 * k + n, "c": ch, "x": round(x, 3), "y": by, "s": 4.0,
                          "w": round(w, 3), "r": 0, "h": 0, "st": "", "row": len(lines) + k, "box": bid})
            x += w
        box_rects.append((bx - 1.2, by - 4.4, x + 1.2, by + 1.8))           # mm, the region items with box never leave
    im = apply_label(im, None, frac=0.053)
    save_image(im, folder / "page-1.png", {"aigc": aigc(pid)}, dpi=300)
    text = "\n".join(lines)
    if plan:
        pid_paper = paper if paper != "grid" else "blank"
        (folder / "plan.json").write_text(json.dumps({"pages": 1, "chars": len(items), "plan": {
            "paper": {"id": pid_paper, "w": 210, "h": 297}, "pages": [items]}}, ensure_ascii=False), encoding="utf-8")
        (folder / "layout.json").write_text(json.dumps({"text": text, "paperId": pid_paper, "d": {}}, ensure_ascii=False),
                                            encoding="utf-8")
    return {"bases": bases, "s": s_mm, "left": left_mm, "boxes": box_rects, "items": items}


def first_ink_mm(rgb: np.ndarray, base_mm: float, s_mm: float, ppm: float = PPM, thr: float = 110) -> float | None:
    """Left edge of the dark handwriting in a line's band (printed rules / margin line are lighter than thr)."""
    y0, y1 = int((base_mm - 0.95 * s_mm) * ppm), int((base_mm + 0.3 * s_mm) * ppm)
    cols = np.flatnonzero(((lum(rgb[y0:y1]) < thr).sum(0)) >= 2)
    return float(cols[0] / ppm) if len(cols) else None


def paper_left_clean(out_png: Path, paper_png: Path, tol: int = 12) -> None:
    """Away from the (moved) handwriting the result must be the empty paper: no ghost strokes, no smeared lines."""
    after, clean = rgb_of(out_png), rgb_of(paper_png)
    mask = ~dilate(lum(after) < 170, 3)
    H, W = mask.shape
    mask[int(0.88 * H):, int(0.4 * W):] = False                              # the label corner
    dev = np.abs(after - clean).max(2)[mask]
    bad = int((dev > tol).sum())
    assert bad <= 20, f"{bad} pixels differ from the empty paper by > {tol} (max {int(dev.max())})"


def label_ok(path: Path, original: Path | None = None) -> None:
    img, meta = open_image(path)
    rgb = np.asarray(img.convert("RGB"))
    bb = find_label_bbox(rgb)
    assert bb, f"visible label not found in {path.name}"
    th = text_height(label_from_page(rgb, bb))
    assert th >= 0.05 * min(img.size), (th, min(img.size))
    assert meta.get("aigc") and meta["aigc"].get("ProduceID"), "AIGC metadata lost"
    if original is not None:
        x0, y0, x1, y1 = bb
        assert np.array_equal(rgb[y0:y1, x0:x1], np.asarray(Image.open(original).convert("RGB"))[y0:y1, x0:x1]), "label pixels changed"


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    import compose as C

    # ── the drift model itself ──────────────────────────────────────────────
    @test("drift_series: natural creeps right, stays within 0.45 s, is deterministic, re-anchors")
    def _():
        n = 12
        runs = [C.drift_series(n, "natural", 1.0, C.drift_seed(s, "k"), []) for s in range(200)]
        assert all(np.abs(v).max() <= 0.45 + 1e-9 for v in runs)
        creep = np.mean([v[7] - v[0] for v in runs])
        assert creep > 0.12, creep                                           # ~7 x 0.0325 s
        a1 = C.drift_series(n, "natural", 1.0, C.drift_seed(3, "k"), [6])
        a2 = C.drift_series(n, "natural", 1.0, C.drift_seed(3, "k"), [6])
        assert np.array_equal(a1, a2)
        assert not np.array_equal(a1, C.drift_series(n, "natural", 1.0, C.drift_seed(4, "k"), [6]))
        with_reset = [C.drift_series(n, "natural", 1.0, C.drift_seed(s, "k"), [6]) for s in range(200)]
        drops = np.mean([v[6] - v[5] for v in with_reset])                  # line 7 keeps only 30 % of the creep
        assert drops < -0.05, drops
        r = np.concatenate([C.drift_series(n, "random", 1.0, C.drift_seed(s, "k"), []) for s in range(200)])
        assert 0.08 < r.mean() < 0.16 and 0.03 < r.std() < 0.09 and np.abs(r).max() <= 0.45, (r.mean(), r.std())
        assert not C.drift_series(n, "none", 1.0, 1, []).any()
        half = C.drift_series(n, "natural", 0.5, C.drift_seed(3, "k"), [6])
        assert np.allclose(half, 0.5 * a1)

    @test("problem numbers re-anchor the creep (1. / (2) / 3、 / ① / 第4题, not 3.14)")
    def _():
        for t, want in (("1. 解", True), ("(2) 求", True), ("3、x", True), ("①ab", True), ("第4题", True), ("十、b", True),
                        ("3.14 是 π", False), ("x = 1", False)):
            assert C.starts_problem(t, 0) is want, t
        assert C.starts_problem("abc\n2. x", 4) and not C.starts_problem("abc 2. x", 4)
        assert C.starts_problem("\U00020000\n2. x", 3)                        # UTF-16 offsets (astral char = 2 units)
        assert C.clamp_drift(5.0, 100, 200, 90, 203) == 3.0 and C.clamp_drift(-5.0, 100, 200, 98, 300) == -2.0
        assert C.clamp_drift(5.0, 100, 210, 90, 203) == 0.0                  # already past the right edge: not pushed further

    # ── (a) blank paper, plan.json, a boxed answer, inko.pdf ───────────────
    lines_a = ["1. Solve x + 3 = 7 解方程", "x = 7 - 3", "x = 4", "Check: 4 + 3 = 7 对", "so the answer is x = 4",
               "and that is all for 1", "2. Solve 2y = 10", "y = 10 / 2", "y = 5", "Check: 2 x 5 = 10", "so y = 5 is right",
               "both answers checked", "done with the homework", "see you tomorrow"]
    ja = OUT / "job_blank"
    fa = build_page(ja, lines_a, boxes=[("42", 150.0, 250.0, "ans")], pid="drift-blank")
    ok(S / "pdf.py", ja / "page-1.png", "-o", ja / "inko.pdf")
    ra = OUT / "blank_out"
    res_a: dict = {}

    @test("drift --job (blank): uneven left edges, |offset| <= 0.5 s, reported shifts = measured shifts")
    def _():
        res_a.update(ok(S / "compose.py", "drift", "--job", ja, "-o", ra))
        o = res_a["outputs"][0]
        assert o["rows_from"] == "plan.json" and o["lines"] == len(lines_a), o
        assert 7 in res_a["reanchored_lines"], res_a["reanchored_lines"]     # "2. Solve" starts a new problem
        offs = np.array(o["offsets_mm"])
        assert np.abs(offs).max() <= 0.5 * fa["s"], offs
        before, after = rgb_of(ja / "page-1.png"), rgb_of(ra / "page-1.png")
        xb = np.array([first_ink_mm(before, b, fa["s"]) for b in fa["bases"]])
        xa = np.array([first_ink_mm(after, b, fa["s"]) for b in fa["bases"]])
        assert xa.std() > 0.3, (xa.std(), xa)
        assert np.abs((xa - xb) - offs).max() < 0.15, np.round(xa - xb - offs, 3)
        paper_left_clean(ra / "page-1.png", OUT / "job_blank-paper.png")

    @test("drift --job (blank): boxed answer, label, AIGC metadata, dpi, marker, PDF, shifted plan")
    def _():
        before, after = rgb_of(ja / "page-1.png"), rgb_of(ra / "page-1.png")
        x0, y0, x1, y1 = (int(v * PPM) for v in fa["boxes"][0])
        assert np.array_equal(before[y0:y1, x0:x1], after[y0:y1, x0:x1]), "the boxed answer moved"
        label_ok(ra / "page-1.png", ja / "page-1.png")
        img, meta = open_image(ra / "page-1.png")
        assert abs(float(img.info["dpi"][0]) - 300) < 0.5 and meta["drift"]["by"] == "compose.py drift", (img.info.get("dpi"), meta["drift"])
        raw = (ra / "inko.pdf").read_bytes()
        assert raw.startswith(b"%PDF") and b"/AIGC" in raw and b"TC260:AIGC" in raw, "PDF without AIGC metadata"
        plan = json.loads((ra / "plan.json").read_text(encoding="utf-8"))
        items = plan["plan"]["pages"][0]
        offs = res_a["outputs"][0]["offsets_mm"]
        firsts = [next(it for it in items if it.get("row") == r and not it.get("box")) for r in range(len(lines_a))]
        orig = [next(it for it in fa["items"] if it.get("row") == r) for r in range(len(lines_a))]
        assert all(abs(f["x"] - (o["x"] + d)) < 0.01 for f, o, d in zip(firsts, orig, offs)), "plan.json not shifted with the ink"
        assert all(it["x"] == o["x"] for it, o in zip(items, fa["items"]) if it.get("box")), "boxed plan items moved"

    @test("drift: same seed = same pages, another seed = another pattern")
    def _():
        rb = OUT / "blank_again"
        again = ok(S / "compose.py", "drift", "--job", ja, "-o", rb)
        assert again["outputs"][0]["offsets_mm"] == res_a["outputs"][0]["offsets_mm"]
        assert np.array_equal(rgb_of(ra / "page-1.png"), rgb_of(rb / "page-1.png")), "not deterministic"
        other = ok(S / "compose.py", "drift", "--job", ja, "-o", OUT / "blank_seed9", "--seed", "9")
        assert other["outputs"][0]["offsets_mm"] != res_a["outputs"][0]["offsets_mm"]

    @test("drift refuses to drift twice (unless --force) and to overwrite the original")
    def _():
        code, _js, err = run(S / "compose.py", "drift", ra / "page-1.png", "-o", OUT / "twice")
        assert code != 0 and "drifted already" in err, (code, err)
        ok(S / "compose.py", "drift", ra / "page-1.png", "-o", OUT / "twice", "--force")
        code, _js, err = run(S / "compose.py", "drift", "--job", ja, "-o", ja)
        assert code != 0 and "original" in err, (code, err)

    # ── (b) ruled paper like ruled8: blue rules, red margin line ───────────
    lines_b = ["1. Solve: x + y = 9, y = 2", "x = 9 - 2 = 7 (gypsy jog)", "so x = 7 and y = 2", "2. Find the length gj",
               "gj = 3 + 4 = 7 (py)", "so the length is 7", "3. Simplify (a + b) - b", "(a + b) - b = a", "so it is a, qed",
               "4. Say yes or no: 2 > 1", "yes, because 2 - 1 > 0", "that's all, jolly good"]
    jb = OUT / "job_ruled"
    bases_b = [34 + 8 * k - 0.5 for k in range(len(lines_b))]           # low enough that g / y / j / ( ) cross the rules
    fb = build_page(jb, lines_b, paper="ruled8", s_mm=4.96, pitch_mm=8.0, left_mm=25.5, first_base_mm=bases_b[0],
                    boxes=[("9", 178.16, 23.9, "mon"), ("27", 186.52, 23.9, "day")], pid="drift-ruled")
    rb_out = OUT / "ruled_out"
    res_b: dict = {}

    @test("drift on ruled paper: only the ink moves — rules and margin line untouched, rules whole where ink left them")
    def _():
        res_b.update(ok(S / "compose.py", "drift", "--job", jb, "-o", rb_out))
        o = res_b["outputs"][0]
        assert o["lines"] == len(lines_b) and o["margin_line"] and o["boxed_items_kept"] == 3, o
        offs = np.array(o["offsets_mm"])
        assert (offs >= 0).all() and np.abs(offs).max() <= 0.5 * fb["s"], offs      # never across the margin line
        before, after = rgb_of(jb / "page-1.png"), rgb_of(rb_out / "page-1.png")
        xb = np.array([first_ink_mm(before, b, fb["s"]) for b in fb["bases"]])
        xa = np.array([first_ink_mm(after, b, fb["s"]) for b in fb["bases"]])
        assert xa.std() > 0.3 and (xa >= xb - 0.05).all(), (xa.std(), np.round(xa - xb, 2))
        Lb, La = lum(before), lum(after)
        near = dilate((Lb < 150) | (La < 150), 4)                           # handwriting before or after, with a margin
        rule_rows = np.flatnonzero(np.median(Lb[:, 300:2300], 1) < 230)
        margin_cols = np.flatnonzero(np.median(Lb[400:3000], 0) < 235)
        assert len(rule_rows) >= 30 and len(margin_cols) >= 2, (len(rule_rows), len(margin_cols))
        for sel in (np.s_[rule_rows, :], np.s_[:, margin_cols]):
            keep = ~near[sel]
            assert np.array_equal(before[sel][keep], after[sel][keep]), "printed paper changed"
        rows_core = [r for r in rule_rows if np.median(Lb[r, 300:2300]) < 185]                # the rules' core rows
        crossed = 0
        for r in rows_core:                                                 # where old ink crossed a rule and moved away
            m = (Lb[r, 300:2300] < 150) & ~dilate(La < 150, 2)[r, 300:2300]
            crossed += int(m.sum())
            if m.any():
                ref = np.median(before[r, 300:2300][~dilate(Lb < 150, 3)[r, 300:2300]], 0)
                assert np.abs(after[r, 300:2300][m] - ref).max() <= 14, (r, np.abs(after[r, 300:2300][m] - ref).max())
        assert crossed > 0, "the fixture should have ink crossing rules"
        x0, y0, x1, y1 = (int(v * PPM) for v in fb["boxes"][0])
        assert np.array_equal(before[y0:y1, x0:x1], after[y0:y1, x0:x1]), "the Date box moved"
        label_ok(rb_out / "page-1.png", jb / "page-1.png")
        paper_left_clean(rb_out / "page-1.png", OUT / "job_ruled-paper.png")

    @test("drift without plan.json: rows from the ink, red margin line found, nothing moves left of it")
    def _():
        r = ok(S / "compose.py", "drift", jb / "page-1.png", "-o", OUT / "ruled_noplan.png")
        o = r["outputs"][0]
        assert o["rows_from"].startswith("the ink") and o["margin_line"], o
        assert abs(o["lines"] - len(lines_b)) <= 1, o["lines"]
        assert all(v >= 0 for v in o["offsets_mm"]), o["offsets_mm"]
        label_ok(OUT / "ruled_noplan.png", jb / "page-1.png")
        paper_left_clean(OUT / "ruled_noplan.png", OUT / "job_ruled-paper.png")

    @test("drift beside a margin line: the wobble folds back, no run of lines left at exactly the old edge")
    def _():
        resets = [k - 1 for k in res_b["reanchored_lines"]]
        seed = next(sd for sd in range(500)                                  # a pattern that dips below 0 on 2+ lines
                    if (C.drift_series(len(lines_b), "natural", 1.0, C.drift_seed(sd, "drift-ruled"), resets) <= 0).sum() >= 2)
        r = ok(S / "compose.py", "drift", "--job", jb, "-o", OUT / "ruled_fold", "--seed", seed)
        offs = np.array(r["outputs"][0]["offsets_mm"])
        assert (offs > 0).all() and offs.std() > 0.03, (seed, offs)                    # clipping left 0.00 on 2+ lines

    @test("drift: a line with a box beside it (wrap) doesn't run into the box; formula bands cover raised fractions")
    def _():
        jw = OUT / "job_wrap"
        lw = ["1. first line of text", "second line of it", "third line of it", "fourth line of it", "fifth line of it",
              "x + 3 = 7, x =", "seventh line", "eighth line"]
        font = cjk_font(int(7.0 * PPM), kai=True)
        x1 = 20.0 + sum(font.getlength(ch) / PPM for ch in lw[5])            # right end of line 6 (mm)
        fw = build_page(jw, lw, boxes=[("4", x1 + 1.6, 40.0 + 5 * 11.9, "ans")], pid="drift-wrap")
        r = ok(S / "compose.py", "drift", "--job", jw, "-o", OUT / "wrap_out", "--drift-amount", "3")
        offs = r["outputs"][0]["offsets_mm"]
        room = fw["boxes"][0][0] - x1                                         # box rectangle (with its margin) - line end
        assert offs[5] <= room + 0.01 and max(offs) > room + 0.3, (room, offs)
        before, after = rgb_of(jw / "page-1.png"), rgb_of(OUT / "wrap_out" / "page-1.png")
        bx0, by0, bx1, by1 = (int(v * PPM) for v in fw["boxes"][0])
        assert np.array_equal(before[by0:by1, bx0:bx1], after[by0:by1, bx0:bx1]), "the boxed answer changed"
        code, _js, err = run(S / "compose.py", "drift", ja / "page-1.png", jw / "page-1.png", "-o", OUT / "dup")
        assert code != 0 and "same file name" in err, (code, err)
        items = [{"x": 20, "y": 50.0, "s": 5.0, "w": 5, "c": "a"},
                 {"x": 25, "y": 49.2, "s": 5.0, "w": 9, "c": "\\frac{1}{2}", "m": {"a": 1.0, "d": 0.44}}]
        (ln,), _ = C.plan_lines({"plan": {"pages": [items]}}, 0, 1.0)
        assert abs(ln["top"] - (49.2 - 5.0)) < 1e-6 and abs(ln["bot"] - (49.2 + 0.44 * 5.0)) < 1e-6, ln

    @test("drift on 5 mm grid paper: rows found from the ink, not from the grid; grid untouched")
    def _():
        jg = OUT / "job_grid"
        lines_g = ["grid paper, line one", "the second line here", "and a third one", "fourth line of text", "fifth and last"]
        build_page(jg, lines_g, paper="grid", s_mm=6.0, pitch_mm=10.0, left_mm=30.0, first_base_mm=39.0, pid="drift-grid", plan=False)
        r = ok(S / "compose.py", "drift", jg / "page-1.png", "-o", OUT / "grid_out")
        assert r["outputs"][0]["lines"] == len(lines_g), r["outputs"][0]
        before, after = rgb_of(jg / "page-1.png"), rgb_of(OUT / "grid_out" / "page-1.png")
        near = dilate((lum(before) < 150) | (lum(after) < 150), 4)
        H, W = near.shape
        grid = np.zeros((H, W), bool)
        grid[np.flatnonzero(np.median(lum(before)[:, 300:2300], 1) < 235), :] = True
        grid[:, np.flatnonzero(np.median(lum(before)[400:3000], 0) < 235)] = True
        keep = grid & ~near
        keep[int(0.88 * H):, int(0.4 * W):] = False                          # the label corner
        assert keep.sum() > 10000 and np.array_equal(before[keep], after[keep]), "grid changed"
        paper_left_clean(OUT / "grid_out" / "page-1.png", OUT / "job_grid-paper.png")

    width = max(len(n) for n, _, _ in RESULTS)
    fails = 0
    for n, good, msg in RESULTS:
        print(f"{'PASS' if good else 'FAIL'}  {n.ljust(width)}")
        if not good:
            fails += 1
            print("      " + msg.replace("\n", "\n      "))
    print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} passed · outputs in {OUT}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
