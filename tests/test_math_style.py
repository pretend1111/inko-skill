#!/usr/bin/env python3
"""Tests for inko.py's logic-1 math formatting: long-formula splitting, the width estimator and the math style lint.

    python tests/test_math_style.py            # offline (the API is replaced by a fake that records what is sent)
    python tests/test_math_style.py --online   # also a free /v1/layout call against $INKO_API_BASE (needs INKO_API_KEY)

Exit code 0 = all passed.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import contextlib
import io
import json
import os
import re
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE.parent / "inko-handwriting" / "scripts"
sys.path.insert(0, str(S))

import inko  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def test(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, False, f"{e}\n{traceback.format_exc(limit=4)}"))
        return fn
    return deco


def outside(text: str) -> str:
    """The text with every formula removed (what must never change)."""
    return "\n".join(re.sub(r"\s+", " ", "".join(ln[last:a] for (last, a) in _gaps(ln))) for ln in text.split("\n"))


def _gaps(line: str):
    spans = inko.formula_spans(line)
    starts = [0] + [b for _, b, _ in spans]
    ends = [a for a, _, _ in spans] + [len(line)]
    return list(zip(starts, ends))


LONG = r"I(a)=\int_0^\pi f dx=\int_0^\pi g dt=I(-a)"
LONG_ENGINE = r"I(a)=\int_0^\pi \ln(1-2a\cos x+a^2)dx=\int_0^\pi \ln(1+2a\cos t+a^2)dt=I(-a)"


def run_cli(argv: list[str], responses: dict) -> tuple[dict, list[tuple[str, str, dict]]]:
    """Run inko.main() with a fake API; returns (JSON printed, [(method, path, body) sent])."""
    sent: list[tuple[str, str, dict]] = []

    def fake_call(method, path, body=None, **kw):
        sent.append((method, path, json.loads(json.dumps(body)) if body is not None else None))
        for k, v in responses.items():
            if path.startswith(k):
                return json.loads(json.dumps(v))
        raise AssertionError(f"unexpected API call {method} {path}")

    old_call, old_argv, buf = inko.call, sys.argv, io.StringIO()
    inko.call = fake_call
    sys.argv = ["inko.py", *argv]
    try:
        with contextlib.redirect_stdout(buf):
            try:
                inko.main()
            except SystemExit:
                pass
    finally:
        inko.call, sys.argv = old_call, old_argv
    return json.loads(buf.getvalue()), sent


QUOTE = {"ok": True, "chars": 60, "formulas": 3, "pages_est": 1, "price": {"units": "chars", "amount": 60, "billed_chars": 100, "min_chars": 100,
                                                                         "list_cents": 0}, "errors": [], "warnings": [],
         "account": {"balance_cents": 1000, "quota_cents": 0, "quota_pages": 0}, "style": {"ref": "3", "label": "No.003", "source": "default"}}
LAYOUT = {"pages": 1, "chars": 60, "formulas": 3, "unplaced": 0, "warnings": [], "plan": {"paper": {"id": "ruled8"}, "pages": [[]], "rows": []}}
JOB = {"id": "job-math-style-1", "status": "queued", "chars": 60, "pages_est": 1}


def main() -> int:
    online = "--online" in sys.argv

    # ── splitting ─────────────────────────────────────────────────────────────
    @test("split: the example from the spec (few cuts: left side after 所以, the rest continues on the next line)")
    def _():
        t, sp = inko.split_long_math(f"所以 ${LONG}$，")
        assert t == r"所以 $I(a)$ $=\int_0^\pi f dx=\int_0^\pi g dt=I(-a)$，", t
        assert sp == [{"paragraph": 1, "pieces": 2}], sp
        # a piece longer than ~0.85 of a line is cut again, as late as possible
        assert inko.split_formula(LONG_ENGINE, line_w=24) == [r"I(a)", r"=\int_0^\pi \ln(1-2a\cos x+a^2)dx",
                                                              r"=\int_0^\pi \ln(1+2a\cos t+a^2)dt=I(-a)"]
        assert inko.split_formula(LONG_ENGINE, line_w=34) == [r"I(a)", r"=\int_0^\pi \ln(1-2a\cos x+a^2)dx=\int_0^\pi \ln(1+2a\cos t+a^2)dt=I(-a)"]

    @test("split: only top-level relations (fractions, scripts, roots, brackets, \\left…\\right, environments untouched)")
    def _():
        for f in (r"\frac{a=b=c=d=e=f=g}{h}", r"\left(a=b=c=d=e=f=g\right)=1", r"(a=b=c=d=e=f=g=h=i)",
                  r"\{x=1=2=3=4=5=6=7\}", r"(a+b+c+d+e+f+g+h+i+j+k)=1"):
            assert inko.split_formula(f) == [f], (f, inko.split_formula(f))
        f = r"f_{a=b=c=d=e=f=g=h}+\sum_{i=1=2=3=4=5}^{n}x_i^{k=2}+\sqrt{a=b=c}"       # a long sum: only a top-level + is cut
        assert inko.split_formula(f) == [r"f_{a=b=c=d=e=f=g=h}+\sum_{i=1=2=3=4=5}^{n}x_i^{k=2}", r"+\sqrt{a=b=c}"], inko.split_formula(f)
        # a matrix / cases is measured by its widest cells, not as one long row, so a short one is never cut
        for f in (r"f(x)=\begin{cases}x^2, & x\geq 0\\-x, & x<0\end{cases}", r"A=\begin{pmatrix}1&2&3\\4&5&6\end{pmatrix}"):
            assert inko.split_formula(f) == [f] and inko.formula_width(f) < 8, (f, inko.formula_width(f))
        f = r"\begin{cases}a=b=c=d=e\\x=y=z\end{cases}=1+2+3+4=10"          # the environment stays whole
        assert inko.split_formula(f) == [r"\begin{cases}a=b=c=d=e\\x=y=z\end{cases}", r"=1+2+3+4=10"], inko.split_formula(f)
        f = r"(x+1)^2-(x-1)^2=x^2+2x+1-(x^2-2x+1)=4x"
        assert inko.split_formula(f) == [r"(x+1)^2-(x-1)^2", r"=x^2+2x+1-(x^2-2x+1)=4x"], inko.split_formula(f)
        f = r"x_n=\alpha^n+\frac{b(1-\alpha^n)}{1-\alpha}+\sum_{k=1}^{n}\alpha^{n-k}e_k"            # the left side may be short
        assert inko.split_formula(f) == [r"x_n", r"=\alpha^n+\frac{b(1-\alpha^n)}{1-\alpha}+\sum_{k=1}^{n}\alpha^{n-k}e_k"]
        f = r"x=\frac{a=b}{c}+\frac{1}{2}+\frac{1}{3}+\frac{1}{4}=\left(\frac{1}{2}=d\right)+\sqrt{x=y}+\int_{a=1}^{b=2}t dt"
        assert inko.split_formula(f) == [r"x", r"=\frac{a=b}{c}+\frac{1}{2}+\frac{1}{3}+\frac{1}{4}=\left(\frac{1}{2}=d\right)+\sqrt{x=y}+\int_{a=1}^{b=2}t dt"]
        # a very long sum on a narrow line: extra cuts before top-level + / − (never inside brackets)
        f = "=" + "+".join("abcdefghijklmnopqrstuvwxyzabcdef")
        pieces = inko.split_formula(f, line_w=24)
        assert len(pieces) == 3 and all(p[0] in "=+" for p in pieces) and max(inko.formula_width(p) for p in pieces) <= 0.85 * 24, pieces

    @test("split: relation commands, a chain that starts with =, statements, '<=' typed as two symbols")
    def _():
        f = r"a+b+c+d \leq e+f+g+h \neq i+j+k+l \Rightarrow m+n"
        assert inko.split_formula(f) == [r"a+b+c+d", r"\leq e+f+g+h \neq i+j+k+l \Rightarrow m+n"], inko.split_formula(f)
        f = r"=\int_0^1 \frac{x^2+2x+1}{x+1}dx=\int_0^1 (x+1)dx=\frac{3}{2}+\frac{3}{2}"          # a continuation fits a line
        assert inko.split_formula(f) == [f], inko.split_formula(f)
        assert inko.split_formula(f, line_w=12) == [r"=\int_0^1 \frac{x^2+2x+1}{x+1}dx", r"=\int_0^1 (x+1)dx=\frac{3}{2}+\frac{3}{2}"]
        f = r"x_1=\frac{-b+\sqrt{b^2-4ac}}{2a}, x_2=\frac{-b-\sqrt{b^2-4ac}}{2a}"
        assert inko.split_formula(f, line_w=12) == [r"x_1", r"=\frac{-b+\sqrt{b^2-4ac}}{2a},", r"x_2=\frac{-b-\sqrt{b^2-4ac}}{2a}"]
        f = r"a+b+c+d+e<=f+g+h+i+j=>k+l+m+n+p"
        assert inko.split_formula(f) == [r"a+b+c+d+e", r"<=f+g+h+i+j=>k+l+m+n+p"], inko.split_formula(f)
        assert inko.split_formula(f, line_w=12) == [r"a+b+c+d+e", r"<=f+g+h+i+j", r"=>k+l+m+n+p"]

    @test("split: short formulas, a single short relation, $$ display and escaped \\$ stay as they are")
    def _():
        for t in ("$x=1=y$", "$a+b=c$", r"$\int_0^\pi \ln(1-2a\cos x+a^2)dx=0$",
                  f"$${LONG}$$", f"价格 \\$ {LONG} \\$ 元", f"又\n$${LONG}$$\n完"):
            assert inko.split_long_math(t) == (t, []), (t, inko.split_long_math(t))
        t, sp = inko.split_long_math(f"价格 \\$5，所以 ${LONG}$，又 $${LONG}$$ 和 \\$6")
        assert t == rf"价格 \$5，所以 $I(a)$ $=\int_0^\pi f dx=\int_0^\pi g dt=I(-a)$，又 $${LONG}$$ 和 \$6", t
        assert sp == [{"paragraph": 1, "pieces": 2}]

    @test("split: text outside formulas unchanged, paragraphs kept, numbered from 1, idempotent")
    def _():
        src = f"1. 解：令 $x=\\pi-t$，所以 ${LONG}$，\n\n故 $I(a)$ 是偶函数\n又 ${LONG_ENGINE}$ 且 ${LONG}$"
        for lw in (20, 24, 34):
            t, sp = inko.split_long_math(src, lw)
            assert t.count("\n") == src.count("\n")
            assert outside(t) == outside(src), (outside(t), outside(src))
            assert sp[0] == {"paragraph": 1, "pieces": 2} and {x["paragraph"] for x in sp} == {1, 4}, sp
            assert inko.split_long_math(t, lw) == (t, []), "second pass changed the text"
            joined = re.sub(r"\$ \$", "", t)                  # gluing the pieces back gives the original formulas
            assert joined.replace(" ", "") == src.replace(" ", ""), joined

    @test("split: the line width follows the paper / size")
    def _():
        assert inko.PLAIN_LINE_W == {"small": 28.0, "medium": 24.0, "large": 20.0}
        assert 30 < inko.layout_line_w({"paperId": "ruled8"}) < 40 and inko.layout_line_w({"paperId": "blank"}) < 26
        assert inko.layout_line_w({"paperId": "blank", "d": {"size": 5}}) > 30
        assert abs(inko.layout_line_w({"paperId": "blank"}, {"w": 70, "form": {"size": 7}}) - 10) < 1e-9

    # ── width estimator ───────────────────────────────────────────────────────
    @test("width estimator: close to the layout engine's own widths, sane ordering")
    def _():
        engine = {"x": 0.54, "abcdef": 3.24, "a+b": 1.92, "a=b": 1.92, r"a\leq b": 1.92, "(a)": 1.26, r"\frac{1}{2}": 0.66,
                  r"\frac{abc}{d}": 1.54, r"\frac{a+b}{c+d}": 1.78, "x^2": 0.92, "x_1^2": 0.92, "x_{i+1}": 1.81,
                  r"\int_0^\pi": 1.35, r"\sqrt{x+1}": 2.44, r"\log": 1.60, r"\sin x": 2.14, r"\cos^2 x": 2.52,
                  r"\lim_{x\to 0}": 1.72, r"\alpha\beta": 1.20, r"\left(\frac{1}{2}\right)": 1.38, r"\text{cm}": 1.08,
                  "f'(x)": 2.02, "e^{-x^2}": 1.72, LONG_ENGINE: 28.15}          # measured with POST /v1/layout, 2026-09
        for f, w in engine.items():
            est = inko.formula_width(f)
            assert abs(est - w) <= 0.2 * w, (f, est, w)
        W = inko.formula_width
        assert W("a+b") < W("a+b+c") < W("a+b+c+d")
        assert W(r"\frac{a+b}{c}") < W("(a+b)/c") and W("x^{2n}") < W("x2n")
        assert W(LONG) > inko.LONG_FORMULA and W("x=1=y") < inko.LONG_FORMULA

    # ── layouts ───────────────────────────────────────────────────────────────
    @test("layout: split before the shorthand (line / match marks, box text and own text), numbering like line marks")
    def _():
        spec = {"text": f"1. 解：所以 ${LONG}$\n答：略", "paperId": "ruled8",
                "boxes": [{"id": "Q", "text": f"第一行\n又 ${LONG}$", "x": 20, "y": 200, "w": 150, "h": 30},
                          {"id": "N", "own": f"${LONG}$", "x": 20, "y": 240, "w": 150, "h": 10}],
                "marks": [{"line": 1, "f": {"indent": 0}}, {"match": f"${LONG}$", "nth": 2, "f": {"scale": 1.1}}, {"match": "答：略"}]}
        new, fmt = inko.split_layout_math(spec)
        assert "$ $=" in new["text"] and "$ $=" in new["boxes"][0]["text"] and "$ $=" in new["boxes"][1]["own"], new
        assert fmt["formula_splits"] == [{"paragraph": 1, "pieces": 2}, {"paragraph": 4, "pieces": 2, "box": "Q"},
                                         {"paragraph": 1, "pieces": 2, "box": "N"}], fmt["formula_splits"]
        assert new["marks"][1]["match"] == inko.split_long_math(f"${LONG}$")[0], new["marks"][1]
        e = inko.expand_layout(new)                       # the shorthand still resolves on the split text
        m = e["marks"][1]
        assert e["text"][m["start"]:m["end"]] == new["marks"][1]["match"]
        assert e["text"].split("\n")[1] == "答：略" and spec["text"].endswith("答：略")
        assert spec["text"] == f"1. 解：所以 ${LONG}$\n答：略", "input spec was modified"

    @test("layout: raw start/end/range positions -> nothing in the text is split, a note says why (own text still split)")
    def _():
        base = {"text": f"所以 ${LONG}$", "paperId": "ruled8"}
        for extra in ({"marks": [{"id": "M1", "start": 0, "end": 2, "f": {"scale": 1.2}}]},
                      {"blocks": [{"id": "B1", "start": 0, "end": 5}], "boxes": [{"id": "A", "block": "B1", "x": 1, "y": 1, "w": 50, "h": 9}]},
                      {"boxes": [{"id": "R", "range": [0, 4], "x": 1, "y": 1, "w": 50, "h": 9}]}):
            new, fmt = inko.split_layout_math({**base, **extra})
            assert new["text"] == base["text"] and "formula_splits" not in fmt, (extra, fmt)
            assert "NOT split" in fmt["formula_note"] and "raw numbers" in fmt["formula_note"], fmt
        new, fmt = inko.split_layout_math({**base, "marks": [{"start": 0, "end": 2, "f": {}}],
                                           "boxes": [{"id": "N", "own": f"${LONG}$", "x": 1, "y": 1, "w": 50, "h": 9}]})
        assert new["text"] == base["text"] and "$ $=" in new["boxes"][0]["own"]
        assert fmt["formula_splits"] == [{"paragraph": 1, "pieces": 3, "box": "N"}] and "NOT split" in fmt["formula_note"], fmt   # narrow box: more cuts
        new, fmt = inko.split_layout_math({**base, "marks": [{"match": "I(a)=", "f": {}}]})    # match across a split point
        assert new["text"] == base["text"] and "match mark" in fmt["formula_note"], fmt
        two = {"text": base["text"] + "，又 $I(a)=1$", "marks": [{"match": "I(a)=", "f": {}}]}  # … while a later one survives:
        new, fmt = inko.split_layout_math(two)                 # the mark must not silently move to that one
        assert new["text"] == two["text"] and "match mark" in fmt["formula_note"], fmt
        new, fmt = inko.split_layout_math({"text": "所以 $x=1$", "marks": [{"start": 0, "end": 2, "f": {}}]})
        assert fmt == {}, fmt                                  # nothing to split: no note

    @test("layout: _load_layout splits for logic-1 only, --keep-formulas keeps them, lint rides along")
    def _():
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.json"
            p.write_text(json.dumps({"layout": {"text": f"1. 解：所以\n${LONG}$。", "paperId": "ruled8",
                                                "marks": [{"line": 1, "f": {"indent": 3}}]}}), encoding="utf-8")
            a = NS(model=None, keep_formulas=False)
            spec, auto, fmt = inko._load_layout(str(p), a)
            assert a.model == "logic-1" and auto and "$ $=" in spec["text"] and fmt["formula_splits"], (a.model, fmt)
            assert spec["marks"][0]["start"] == spec["text"].index("$") and spec["marks"][0]["end"] == len(spec["text"])
            assert {i["kind"] for i in fmt["math_style"]} == {"orphan_lead", "full_stop"}, fmt["math_style"]
            spec, _, fmt = inko._load_layout(str(p), NS(model=None, keep_formulas=True))
            assert f"${LONG}$" in spec["text"] and "formula_splits" not in fmt and fmt["math_style"]
            spec, _, fmt = inko._load_layout(str(p), NS(model="lyric-1", keep_formulas=False))
            assert f"${LONG}$" in spec["text"] and fmt == {}

    # ── lint ──────────────────────────────────────────────────────────────────
    @test("lint: display_formula")
    def _():
        items = inko.lint_math(f"解：\n$${LONG}$$\n所以 $x=1$")
        d = [i for i in items if i["kind"] == "display_formula"]
        assert len(d) == 1 and d[0]["paragraph"] == 2 and d[0]["snippet"].startswith("$$I(a)"), items
        assert set(d[0]) == {"kind", "paragraph", "snippet", "message", "fix"} and "centred" in d[0]["message"]
        assert not any(i["kind"] == "orphan_lead" for i in items)      # 解：+ $$ is reported once, as display
        assert inko.lint_math(r"费用 \$$5$ 元") == []                   # escaped \$ + an inline $5$ is not $$…$$

    @test("lint: full_stop (。 ． and a final '.', not decimals / numbering / formulas / English)")
    def _():
        k = lambda t: [(i["paragraph"], i["message"].split()[0]) for i in inko.lint_math(t) if i["kind"] == "full_stop"]
        assert k("所以 $x=2$。") == [(1, "1")]
        assert k("移项得 $x=2$．两边同除以 2。") == [(1, "2")]
        assert k("所以 $x=2$.") == [(1, "1")] and k("答：共 5 本.") == [(1, "1")]
        assert k("长 3．5 米，宽 $2.5$ 米") == [] and k("1．解：$x=1.5$，$y=0.5$") == [] and k("(2)．解：$x=1$") == []
        assert k(r"所以 $x=2.$") == [] and k("The answer is x.") == [] and k("见图 1.") == [] and k("所以……") == []
        assert k("第一行。\n第二行\n第三行。") == [(1, "1"), (3, "1")]

    @test("lint: orphan_lead (所以 / 得 / 故 / ： / ， at the end of a paragraph, formula on the next)")
    def _():
        k = lambda t: [(i["paragraph"], i["message"]) for i in inko.lint_math(t) if i["kind"] == "orphan_lead"]
        for lead in ("所以", "得", "故", "即", "则", "于是", "可得", "解得", "：", "，"):
            r = k(f"1. 解：由题意{lead}\n$x^2-4x+3=0$")
            assert len(r) == 1 and r[0][0] == 1 and f"「{lead}」" in r[0][1], (lead, r)
        assert k("所以\n$$x=1$$") == [] and k("所以\n故 $x=1$") == [] and k("所以\n\n$x=1$") == [] and k("所以 $x=1$\n$=2$") == []
        assert k("$x^2-4x+3=0$，\n$(x-1)(x-3)=0$") == [] and len(k("设宽为 $x$ 厘米，\n$x+2=5$")) == 1     # step lines are fine
        it = [i for i in inko.lint_math("解：\n$x=1$", first=5, box="Q") if i["kind"] == "orphan_lead"][0]
        assert it["paragraph"] == 5 and it["box"] == "Q" and "Join paragraphs 5 and 6" in it["fix"], it
        note = inko.math_style_note(inko.lint_math("所以\n$x=1$。\n$$y=2$$"))
        assert note.startswith("Math style:") and "1× $$" in note and "full stop" in note, note

    # ── commands (fake API: records what would be sent) ───────────────────────
    @test("quote: the split text is what gets quoted; formula_splits + math_style in the output")
    def _():
        out, sent = run_cli(["quote", "--text", f"所以 ${LONG}$。"], {"/quote": QUOTE})
        assert sent[0][1] == "/quote" and "$ $=" in sent[0][2]["text"] and sent[0][2]["model"] == "logic-1", sent
        assert out["formula_splits"] == [{"paragraph": 1, "pieces": 2}] and out["formula_note"]
        assert [i["kind"] for i in out["math_style"]] == ["full_stop"], out
        assert "$ $" not in out["math_style"][0]["snippet"], out["math_style"]     # snippets quote the text as written
        out, sent = run_cli(["quote", "--text", f"所以 ${LONG}$", "--keep-formulas"], {"/quote": QUOTE})
        assert f"${LONG}$" in sent[0][2]["text"] and "formula_splits" not in out and "math_style" not in out
        out, sent = run_cli(["quote", "--text", f"所以 ${LONG}$。", "--model", "lyric-1"], {"/quote": QUOTE})
        assert f"${LONG}$" in sent[0][2]["text"] and "formula_splits" not in out and "math_style" not in out

    @test("generate: split text sent to /quote and /generations and saved as text.txt; lint in next")
    def _():
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                out, sent = run_cli(["generate", "--text", f"所以\n${LONG}$"], {"/quote": QUOTE})
                assert out["reason"] == "confirmation_required" and out["formula_splits"] and out["math_style"], out
                assert "Math style:" in out["next"] and "$ $=" in sent[0][2]["text"], out["next"]
                out, sent = run_cli(["generate", "--text", f"所以\n${LONG}$", "--yes", "--no-wait"], {"/quote": QUOTE, "/generations": JOB})
                gen = [b for m, p, b in sent if p == "/generations"][0]
                assert "$ $=" in gen["text"] and gen["text"] == [b for m, p, b in sent if p == "/quote"][0]["text"], gen
                saved = json.loads((Path(d) / ".inko" / "plans" / f"{JOB['id']}.json").read_text(encoding="utf-8"))
                assert saved["text.txt"] == gen["text"], saved
                assert out["submitted"] and out["formula_splits"] and out["math_style"], out
            finally:
                os.chdir(cwd)

    @test("generate --layout / layout: split layout sent to /layout, /quote, /generations and saved as layout.json")
    def _():
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                p = Path(d) / "hw.json"
                p.write_text(json.dumps({"text": f"2. 解：令 $x=\\pi-t$，所以 ${LONG}$\n$${LONG}$$", "paperId": "ruled8",
                                         "d": {"indent": 0}, "marks": [{"match": "2. 解：", "f": {}}]}), encoding="utf-8")
                out, sent = run_cli(["layout", "--spec", str(p)], {"/layout": LAYOUT, "/quote": QUOTE})
                lay = [b for m, pth, b in sent if pth == "/layout"][0]
                assert "$ $=" in lay["layout"]["text"] and lay["model"] == "logic-1", lay
                assert "$ $=" in [b for m, pth, b in sent if pth == "/quote"][0]["text"]
                assert out["formula_splits"] == [{"paragraph": 1, "pieces": 2}], out
                assert [i["kind"] for i in out["math_style"]] == ["display_formula"] and "Math style:" in out["attention"], out
                out, sent = run_cli(["generate", "--layout", str(p), "--yes", "--no-wait"],
                                    {"/layout": LAYOUT, "/quote": QUOTE, "/generations": JOB})
                gen = [b for m, pth, b in sent if pth == "/generations"][0]
                assert gen["layout"]["text"].count("$ $=") == 1 and f"$${LONG}$$" in gen["layout"]["text"], gen["layout"]["text"]
                saved = json.loads((Path(d) / ".inko" / "plans" / f"{JOB['id']}.json").read_text(encoding="utf-8"))
                assert saved["layout.json"] == gen["layout"], saved
                assert out["formula_splits"] and out["math_style"]
            finally:
                os.chdir(cwd)

    # ── against the real engine (free /v1/layout) ─────────────────────────────
    if online:
        @test("online: the first piece is written right after 所以, the chain continues on the next line")
        def _():
            import subprocess
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "hw.json"
                p.write_text(json.dumps({"text": f"2. 解：令 $x=\\pi-t$，所以 ${LONG_ENGINE}$，\n故 $I(a)$ 是偶函数",
                                         "paperId": "ruled8", "d": {"indent": 0}}), encoding="utf-8")
                rows = {}
                for mode, extra in (("before", ["--keep-formulas"]), ("after", [])):
                    r = subprocess.run([sys.executable, str(S / "inko.py"), "layout", "--spec", str(p), "--model", "logic-1",
                                        "--out", str(Path(d) / f"{mode}.json"), *extra], capture_output=True, text=True,
                                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                    assert r.returncode == 0, r.stdout + r.stderr
                    items = json.loads((Path(d) / f"{mode}.json").read_text(encoding="utf-8"))["plan"]["pages"][0]
                    k = next(n for n, it in enumerate(items) if it["c"] == "以")
                    s = items[k]["s"]                                 # same visual line: baselines within 0.3 s
                    rows[mode] = [round((it["y"] - items[k]["y"]) / s, 2) for it in items[k:k + 4]]
                assert rows["before"][1] > 0.3, rows                        # whole formula jumped to the next line
                assert abs(rows["after"][1]) < 0.3, rows                    # first piece stays next to 所以
                assert max(rows["after"][1:]) > 0.3, rows                   # … and the chain continues below

    width = max(len(n) for n, _, _ in RESULTS)
    fails = 0
    for n, ok, msg in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}")
        if not ok:
            fails += 1
            print("      " + msg.replace("\n", "\n      "))
    print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
