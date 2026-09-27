#!/usr/bin/env python3
"""Offline tests for the skill's scripts (no network, no API key).

    python tests/run_tests.py            # everything offline, outputs in tests/_out/
    python tests/run_tests.py --online   # also doctor / quote / layout against the API in $INKO_API_BASE (free calls only)

A synthetic "Inko page" (typeset text + the gray label + AIGC metadata + a plan.json) stands in for real generations,
and synthetic paper photos with known geometry stand in for users' photos.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
S = ROOT / "inko-handwriting" / "scripts"
OUT = HERE / "_out"
sys.path.insert(0, str(S))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from _common import (Image, ImageDraw, apply_label, cjk_font, find_label_bbox, open_image, read_meta, save_image)  # noqa: E402

AIGC = {"Label": "1", "ContentProducer": "Inko (inkotype.com)", "ProduceID": "test-page-1", "ReservedCode1": "", "ContentPropagator": "inkotype.com",
        "PropagateID": "test-page-1", "ReservedCode2": ""}
RESULTS: list[tuple[str, bool, str]] = []


def run(*args, ok=True) -> dict:
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if ok and r.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, args))} failed ({r.returncode}):\n{r.stderr[-1500:]}")
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        return {"_stdout": r.stdout, "_stderr": r.stderr}


def test(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, False, f"{e}\n{traceback.format_exc(limit=3)}"))
        return fn
    return deco


def fake_inko_page(path: Path, lines: list[str], size_mm=4.96, pitch_mm=8.0, left_mm=20.0, top_mm=30.0) -> dict:
    """An A4 300-dpi page with typeset 'handwriting', Inko's gray label and AIGC metadata; returns the matching plan."""
    ppm = 300 / 25.4
    im = Image.new("RGB", (2480, 3508), (255, 255, 255))
    d = ImageDraw.Draw(im)
    font = cjk_font(int(size_mm * ppm), kai=True)
    items = []
    for row, text in enumerate(lines):
        base = top_mm + row * pitch_mm
        d.text((left_mm * ppm, base * ppm), text, font=font, fill=(40, 40, 44), anchor="ls")
        x = left_mm
        for ch in text:
            items.append({"c": ch, "x": x, "y": base, "s": size_mm, "w": size_mm, "r": 0, "h": 0, "st": "", "row": row})
            x += size_mm * 1.06
    im = apply_label(im, None, frac=0.053)
    save_image(im, path, {"aigc": AIGC}, dpi=300)
    return {"pages": 1, "chars": sum(len(t) for t in lines), "plan": {"paper": {"id": "blank", "w": 210, "h": 297}, "pages": [items]}}


def main() -> int:
    online = "--online" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    import make_fixtures
    make_fixtures.main(OUT / "fx")
    truth = json.loads((OUT / "fx" / "truth.json").read_text(encoding="utf-8"))
    job = OUT / "job"
    job.mkdir(exist_ok=True)
    lines = ["第三章 读书笔记", "这一章讲了光合作用的过程，植物利用光能", "把二氧化碳和水转化成有机物，释放氧气。", "Light reaction: thylakoid membrane."]
    plan = fake_inko_page(job / "page-1.png", lines)
    (job / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (job / "layout.json").write_text(json.dumps({"text": "\n".join(lines), "paperId": "blank",
                                                 "d": {"size": 4.96, "line": 8.0 / 4.96, "margins": [22, 45, 26, 20]}}), encoding="utf-8")

    @test("fake page has a detectable label and AIGC metadata")
    def _():
        img, meta = open_image(job / "page-1.png")
        assert find_label_bbox(np.asarray(img.convert("RGB"))), "label not found"
        assert meta["aigc"] and meta["aigc"]["ProduceID"] == "test-page-1"

    for name, size in (("notebook_photo", "B5"), ("notebook_closeup", None), ("ruled_scan", "A4"), ("grid_scan", "A5")):
        @test(f"paper.py analyze {name}")
        def _(name=name, size=size):
            t = truth[name]
            args = [S / "paper.py", "analyze", OUT / "fx" / (name + (".jpg" if "notebook" in name else ".png")), "-o", OUT / f"{name}.json",
                    "--overlay", OUT / f"{name}-check.png"]
            args += ["--paper-size", size] if size else ["--line-mm", "8"]
            r = run(*args)
            full = json.loads((OUT / f"{name}.json").read_text(encoding="utf-8"))
            if t.get("kind") == "grid":
                assert r["kind"] == "grid", r["kind"]
            else:
                assert r["kind"] == "ruled", r["kind"]
                assert abs(r["lines"] - t["lines"]) <= 1, (r["lines"], t["lines"])
            assert abs(r["pitch_mm"] - t["pitch_mm"]) / t["pitch_mm"] < 0.03, (r["pitch_mm"], t["pitch_mm"])
            if "written_lines" in t:
                assert r["written_lines"] == t["written_lines"], (r["written_lines"], t["written_lines"])
            assert full["lines"][0]["poly"] and len(full["lines"][0]["poly"]) == 3

    @test("paper.py make --json + compose.py lines (exact paper)")
    def _():
        run(S / "paper.py", "make", "-o", OUT / "b5.png", "--kind", "ruled", "--size", "B5", "--pitch", "8", "--margin-line", "20",
            "--paper", "cream", "--json", OUT / "b5.json", "--dpi", "200")
        r = run(S / "compose.py", "lines", "--job", job, "--paper", OUT / "b5.json", "-o", OUT / "b5-written.png", "--color", "blue")
        o = r["outputs"][0]
        assert o["lines_written"] == len(lines), o
        assert o["visible_label"] and o["aigc_metadata"]
        img, meta = open_image(OUT / "b5-written.png")
        assert meta["aigc"]["ProduceID"] == "test-page-1"
        # the writing of row k must sit just above line k: check ink rows
        pj = json.loads((OUT / "b5.json").read_text(encoding="utf-8"))
        a = np.asarray(img.convert("RGB"), np.float32)
        blue = (a[..., 2] - a[..., 0] > 40) & (a[..., 2] < 200)
        p = pj["pitch_px"]
        for k in range(len(lines)):                          # writing sits just above its line and doesn't cross it
            y = pj["lines"][k]["y"]
            core = blue[int(y - 0.75 * p):int(y - 0.2 * p)].sum()
            on_line = blue[int(y - 0.02 * p):int(y + 0.15 * p)].sum()
            assert core > 200 and core > 4 * on_line, (k, int(core), int(on_line))
        y_last = pj["lines"][len(lines)]["y"]
        assert blue[int(y_last - 0.75 * p):int(y_last)].sum() == 0, "writing on a line that should be empty"

    @test("compose.py lines onto a perspective photo writes back into the original")
    def _():
        r = run(S / "compose.py", "lines", "--job", job, "--paper", OUT / "notebook_photo.json", "-o", OUT / "photo-written.jpg")
        o = r["outputs"][0]
        assert o["lines_written"] == len(lines) and o["first_line"] == 4, o
        src = Image.open(OUT / "fx" / "notebook_photo.jpg")
        assert Image.open(OUT / "photo-written.jpg").size == src.size

    @test("compose.py lines without plan.json (rows from the page)")
    def _():
        run(S / "compose.py", "lines", job / "page-1.png", "--paper", OUT / "b5.json", "-o", OUT / "b5-noplan.png")

    @test("compose.py place into a box")
    def _():
        r = run(S / "compose.py", "place", job / "page-1.png", "--onto", OUT / "b5.png", "--box", "150,300,900,300", "-o", OUT / "placed.jpg")
        assert r["visible_label"] and r["placed_px"][2] <= 900

    @test("ink.py extract / restyle / info")
    def _():
        run(S / "ink.py", "extract", job / "page-1.png", "-o", OUT / "ink.png")
        img, meta = open_image(OUT / "ink.png")
        assert img.mode == "RGBA" and meta["label_png"] and meta["aigc"]
        run(S / "ink.py", "restyle", job / "page-1.png", "-o", OUT / "blue.png", "--color", "blue", "--texture", "ballpoint", "--weight", "0.5")
        a = np.asarray(Image.open(OUT / "blue.png").convert("RGB"), np.int16)
        lab = find_label_bbox(a.astype(np.uint8))
        assert lab, "label lost after restyle"
        info = run(S / "ink.py", "info", OUT / "blue.png")
        assert info["ink_rgb"][2] > info["ink_rgb"][0] + 30, info["ink_rgb"]

    for preset in ("desk", "flat", "scan", "copy", "notebook"):
        @test(f"photo.py --preset {preset}")
        def _(preset=preset):
            r = run(S / "photo.py", job / "page-1.png", "-o", OUT / f"photo-{preset}.jpg", "--preset", preset, "--size", "1200")
            assert r["visible_label"] and r["aigc_metadata"], r
            img, meta = open_image(OUT / f"photo-{preset}.jpg")
            assert meta["aigc"], "AIGC metadata lost in JPEG"

    @test("pdf.py keeps AIGC metadata and page size")
    def _():
        r = run(S / "pdf.py", job / "page-1.png", OUT / "photo-flat.jpg", "-o", OUT / "out.pdf")
        assert r["pages"] == 2 and r["aigc_metadata"] and r["page_size_mm"] == [210.0, 297.0], r
        raw = (OUT / "out.pdf").read_bytes()
        assert raw.startswith(b"%PDF-1.7") and b"/AIGC" in raw and b"TC260:AIGC" in raw and raw.rstrip().endswith(b"%%EOF")

    @test("label is re-applied at >= 5% of the shortest side")
    def _():
        img = Image.open(OUT / "photo-desk.jpg").convert("L")
        W, H = img.size
        from _common import label_box
        x0, y0, x1, y1 = label_box((W, H))
        assert (y1 - y0) >= 0.05 * min(W, H), (y1 - y0, min(W, H))

    @test("label on squared paper: measured without the grid, paper under it rebuilt")
    def _():
        from _common import erase_label, label_from_page, text_height
        from paper import make_paper
        plain = apply_label(Image.new("RGB", (2480, 3508), (255, 255, 255)), None, frac=0.053)
        grid, _ = make_paper("grid", (210, 297), 300, 5.0, "blue", "white", None, 10.0, 10.0, 8.0)
        grid = apply_label(grid, None, frac=0.053)
        heights = []
        for im in (plain, grid):
            rgb = np.asarray(im.convert("RGB"))
            bb = find_label_bbox(rgb)
            assert bb, "label not found"
            clean = erase_label(rgb, bb)
            heights.append(text_height(label_from_page(rgb, bb, clean)))
        assert abs(heights[1] - heights[0]) <= 3, heights
        g = np.asarray(grid.convert("L"), np.int16)
        bb = find_label_bbox(np.asarray(grid.convert("RGB")))
        clean = np.asarray(Image.fromarray(erase_label(np.asarray(grid.convert("RGB")), bb)).convert("L"), np.int16)
        x0, y0, x1, y1 = bb
        per = round(5.0 * 300 / 25.4)
        rows = [r for r in range(y0, y1) if (clean[r, x0:x1] < 235).mean() > 0.5]   # grid rows continue under the label
        assert rows and all(any(abs((r - q) % per) <= 2 or abs((r - q) % per - per) <= 2 for q in range(y0 - per, y0)
                                if (g[q, x0:x1] < 235).mean() > 0.5) for r in rows), "grid not continued under the label"

    @test("paper.py measures the writing already on the page")
    def _():
        full = json.loads((OUT / "notebook_photo.json").read_text(encoding="utf-8"))
        ew = full.get("existing_writing")
        assert ew and ew["lines"] == [1, 2, 3], ew
        ink = np.array(ew["ink_rgb"], float)
        assert np.abs(ink - np.array([35, 40, 70])).max() < 25, ink          # the fixture wrote in (35, 40, 70)
        assert 0.03 < ew["lift"] < 0.3 and 0.25 < ew["height"] < 0.9, ew

    @test("compose.py --color auto uses the job's pen colour")
    def _():
        jb = OUT / "job_blue"
        jb.mkdir(exist_ok=True)
        import shutil
        for f in ("page-1.png", "plan.json", "layout.json"):
            shutil.copy(job / f, jb / f)
        (jb / "job.json").write_text(json.dumps({"params": {"pen": {"type": "ballpoint", "color": "blue"}}}), encoding="utf-8")
        r = run(S / "compose.py", "lines", "--job", jb, "--paper", OUT / "b5.json", "-o", OUT / "b5-auto.png")
        assert r["ink_rgb"] == [30, 62, 168] and "pen" in r["ink_from"], (r["ink_rgb"], r["ink_from"])

    @test("inko.py layout shorthand (box text, line/match marks, UTF-16 positions)")
    def _():
        import inko
        e = inko.expand_layout({"paperId": "blank", "boxes": [{"id": "Q1", "text": "$x=3$", "x": 1, "y": 2, "w": 3, "h": 4},
                                                               {"id": "Q2", "text": "𠀀答", "x": 1, "y": 9, "w": 3, "h": 4}],
                                "marks": [{"line": -1, "f": {"scale": 2}}, {"match": "答", "f": {}}]})
        assert e["text"] == "$x=3$\n𠀀答" and e["d"]["fillRest"] is False
        assert e["blocks"] == [{"id": "B_Q1", "start": 0, "end": 5}, {"id": "B_Q2", "start": 6, "end": 9}], e["blocks"]
        assert e["boxes"][0]["kind"] == "rect" and e["boxes"][0]["block"] == "B_Q1"
        assert (e["marks"][0]["start"], e["marks"][0]["end"]) == (6, 9) and (e["marks"][1]["start"], e["marks"][1]["end"]) == (8, 9)

    @test("inko.py catches swallowed LaTeX backslashes")
    def _():
        bad = OUT / "bad.txt"
        bad.write_text("$" + chr(12) + "rac{1}{2}$", encoding="utf-8")
        r = run(S / "inko.py", "quote", "--file", bad, ok=False)
        assert r.get("error", {}).get("code") == "broken_backslash", r

    if online:
        @test("online: doctor / quote / layout (free)")
        def _():
            d = run(S / "inko.py", "doctor")
            assert d["api_reachable"] and str(d.get("key", "")).startswith("valid"), d
            q = run(S / "inko.py", "quote", "--text", "解：$x^2-5x+6=0$", "--model", "logic-1")
            assert q["ok"] and q["chars"] > 0, q
            lay = run(S / "inko.py", "layout", "--spec", ROOT / "inko-handwriting" / "assets" / "layouts" / "letter.json", "--model", "lyric-1",
                      "--preview", OUT / "letter-preview.png")
            assert lay["pages"] == 1 and lay["unplaced"] == 0, lay

    width = max(len(n) for n, _, _ in RESULTS)
    fails = 0
    for n, ok, msg in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}")
        if not ok:
            fails += 1
            print("      " + msg.replace("\n", "\n      "))
    print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} passed · outputs in {OUT}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
