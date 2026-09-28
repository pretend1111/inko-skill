#!/usr/bin/env python3
"""Offline tests for the skill's scripts (no network, no API key).

    python tests/run_tests.py            # everything offline, outputs in tests/_out/
    python tests/run_tests.py --online   # also doctor / quote / layout against the API in $INKO_API_BASE (free calls only)

A synthetic "Inko page" (typeset text + the gray label + AIGC metadata + a plan.json) stands in for real generations,
to exercise local processing on standard flat backgrounds.
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

    @test("pdf.py keeps AIGC metadata and page size")
    def _():
        r = run(S / "pdf.py", job / "page-1.png", OUT / "blue.png", "-o", OUT / "out.pdf")
        assert r["pages"] == 2 and r["aigc_metadata"] and r["page_size_mm"] == [210.0, 297.0], r
        raw = (OUT / "out.pdf").read_bytes()
        assert raw.startswith(b"%PDF-1.7") and b"/AIGC" in raw and b"TC260:AIGC" in raw and raw.rstrip().endswith(b"%%EOF")

    @test("label is re-applied at >= 5% of the shortest side")
    def _():
        img = Image.open(job / "page-1.png").convert("L")
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

    @test("inko.py says which handwriting a job uses (常用字迹 first, fallback explained)")
    def _():
        import inko
        base = {"ok": True, "chars": 12, "price": {"list_cents": 20}}
        q = inko._summarize_quote({**base, "style": {"ref": "37", "label": "No.037", "source": "default"}})
        assert q["style"]["source"] == "default" and "常用字迹 No.037" in inko.style_hint(q)
        q = inko._summarize_quote({**base, "style": {"ref": "1", "label": "No.001", "source": "system"},
                                   "style_note": "常用字迹「我的字」是专属字迹,Inko Logic 1 暂不支持专属字迹,这次用了系统默认的 No.001"})
        h = inko.style_hint(q)
        assert q["style_note"] in h and "--favorites" in h and "casual" in h, h
        assert "hasn't set one" in inko.style_hint(inko._summarize_quote({**base, "style": {"ref": "5", "label": "No.005", "source": "system"}}))
        assert "on purpose" in inko.style_hint(inko._summarize_quote(base))           # an older API without `style` in the quote
        h = run(S / "inko.py", "default-style", "--help")["_stdout"]
        assert "--clear" in h and "常用字迹" in h, h

    @test("inko.py prices ¥0.002 / char (min 100 chars), pays from the balance only (older servers' quota still handled)")
    def _():
        import re
        import inko
        assert [inko.price_cents(n) for n in (0, 12, 100, 101, 812, 1000)] == [20, 20, 20, 21, 163, 200]
        # current servers: pure pay-as-you-go; quota_cents / quota_pages are 0 and membership is null (legacy keys)
        acc = {"balance_cents": 500, "quota_cents": 0, "quota_pages": 0, "membership": None}
        p = inko.payment(812, 0, acc)                                                         # no list price given: computed here
        assert p == "¥1.63 from the balance (¥5.00 available, ¥3.37 left after this job)", p
        p = inko.payment(812, 163, {**acc, "balance_cents": 100})
        assert p == "¥1.63 from the balance, but only ¥1.00 is available: the user must top up at least ¥0.63 on inkotype.com first", p
        assert inko.payment(12, 20, None) == "¥0.20 from the balance"                        # no account in the quote
        q = inko._summarize_quote({"ok": True, "chars": 12, "account": {"balance_cents": 1000, "quota_cents": 0, "quota_pages": 0},
                                   "price": {"units": "chars", "amount": 12, "billed_chars": 100, "min_chars": 100, "list_cents": 20}})
        assert q["price_cny"] == 0.2 and q["list_cents"] == 20, q
        assert q["payment"] == "¥0.20 from the balance (¥10.00 available, ¥9.80 left after this job)", q
        assert not re.search(r"quota|member|page|页|额度|会员", q["payment"]), q["payment"]
        assert inko._money(acc) == {"balance_cents": 500}
        assert inko._money({**acc, "can_remove_label": True}) == {"balance_cents": 500, "can_remove_label": True}
        # older servers still had a quota (membership / new-user gift), used before the balance at the same price
        assert inko.quota_cents({"quota_cents": 280, "quota_pages": 2.8}) == 280
        assert inko.quota_cents({"quota_pages": 2.8}) == 280 and inko.quota_cents({}) == 0 and inko.quota_cents(None) == 0
        p = inko.payment(12, 20, {"balance_cents": 0, "quota_cents": 300})
        assert p == "¥0.20 of quota (¥2.80 quota left after this job); balance untouched", p
        p = inko.payment(1500, 300, {"balance_cents": 500, "quota_pages": 2.8})              # even older: quota_pages only
        assert p == "all ¥2.80 remaining quota + ¥0.20 from the balance (¥5.00 available, ¥4.80 left after this job)", p
        assert inko._money({"balance_cents": 0, "quota_pages": 2.8}) == {"balance_cents": 0, "quota_cents": 280}
        # hints and help: no membership / subscription any more; label:none = a custom-handwriting seat + the agreement
        assert not any(re.search(r"member|subscri|会员|额度", h, re.I) for k, h in inko.HINTS.items() if k != "insufficient_balance")
        assert "top up" in inko.HINTS["insufficient_balance"] and "no membership" in inko.HINTS["insufficient_balance"]
        assert "seat" in inko.HINTS["label_required"] and "agreement" in inko.HINTS["label_required"]
        assert "pricing#topup" in inko.HINTS["insufficient_balance"] and "seat" in inko.HINTS["no_slot"]
        assert inko.payment(12, 20, {"quota_cents": 5}) == "all ¥0.05 remaining quota + ¥0.15 from the balance"     # older server, no balance field
        h = run(S / "inko.py", "generate", "--help")["_stdout"]
        assert "custom-handwriting seat" in re.sub(r"-\s+", "-", re.sub(r"\s+", " ", h)), h     # argparse may wrap at a hyphen

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

        @test("online: 常用字迹 and favourites (puts the account's setting back)")
        def _():
            before = run(S / "inko.py", "default-style")
            assert "default_style" in before and isinstance(before.get("favorites"), list), before
            prev = (before.get("default_style") or {}).get("ref")
            code = next(s["style"] for s in run(S / "inko.py", "styles", "--model", "lyric-1", "--kind", "preset", "--limit", "50")["styles"]
                        if s["style"] != prev)
            try:
                r = run(S / "inko.py", "default-style", code)
                assert r["default_style"]["ref"] == code and "lyric-1" in r["default_style"]["models"], r
                q = run(S / "inko.py", "quote", "--text", "春眠不觉晓，处处闻啼鸟。", "--model", "lyric-1")
                assert q["style"] == {"ref": code, "label": f"No.{int(code):03d}", "source": "default"} and "常用字迹" in q["style_hint"], q
                q = run(S / "inko.py", "quote", "--text", "春眠不觉晓，处处闻啼鸟。", "--model", "lyric-1", "--style", "2")
                assert q["style"]["source"] == "request" and "style_hint" not in q, q
                st = run(S / "inko.py", "styles", "--model", "lyric-1", "--limit", "3")
                assert st["styles"][0]["style"] == code and st["styles"][0].get("default") is True, st["styles"][0]
                fav = run(S / "inko.py", "styles", "--favorites")
                assert all(s.get("favorite") for s in fav["styles"]) and fav["count"] == len(before["favorites"]), fav
            finally:
                run(S / "inko.py", "default-style", *([prev] if prev else ["--clear"]))
            assert (run(S / "inko.py", "default-style").get("default_style") or {}).get("ref") == prev

    for name in ("test_math_style.py", "test_drift.py", "test_scene.py", "test_flat_scope.py"):      # separate suites (own fixtures), same pass/fail
        if not (HERE / name).exists():
            continue

        @test(f"suite {name}" + (" (--online)" if online else ""))
        def _(name=name):
            r = subprocess.run([sys.executable, str(HERE / name), *(["--online"] if online else [])], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            tail = "\n".join((r.stdout or "").strip().splitlines()[-12:])
            assert r.returncode == 0, f"{name} failed:\n{tail}\n{(r.stderr or '')[-800:]}"

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
