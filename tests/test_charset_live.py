"""Offline client contract tests for the live character list (skill 1.5.0).

Run: python tests/test_charset_live.py (mock API only; no key, network or generation).
"""
import unittest

from test_math_style import run_cli

CHARSET = {"version": "abc", "base": "两个模型都会写简体中文常用字、ASCII 字母与数字、常用中英文标点。",
           "models": {"lyric-1": {"writes": {"希腊字母": "αβ"}, "cannot": {"希腊字母": "ι"}, "dollar_is_text": True},
                      "logic-1": {"writes": {}, "cannot": {"希腊字母": "βι"}}},
           "custom_styles_cannot": "αβ", "rewrites": [], "skill_latest": "1.5.0"}


class CharsetLive(unittest.TestCase):
    def test_charset_is_fetched_live_without_key(self):
        out, sent = run_cli(["charset"], {"/charset": CHARSET})
        self.assertEqual(out["models"]["lyric-1"]["writes"]["希腊字母"], "αβ")
        self.assertEqual([(m, p) for m, p, _ in sent], [("GET", "/charset")])
        self.assertNotIn("skill_update", out)                  # same version: no nag

    def test_newer_skill_is_announced(self):
        out, _ = run_cli(["charset"], {"/charset": {**CHARSET, "skill_latest": "9.0.0"}})
        self.assertIn("9.0.0", out["skill_update"])
        out, _ = run_cli(["models"], {"/models": {"data": [], "skill_latest": "9.0.0"}})
        self.assertIn("9.0.0", out["skill_update"])

    def test_docs_do_not_hardcode_the_lists(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "inko-handwriting"
        text = (root / "references/writing-text.md").read_text(encoding="utf-8")
        math = (root / "references/writing-math.md").read_text(encoding="utf-8")
        self.assertIn("<!-- inko:live:plain-charset -->", text)
        self.assertIn("<!-- inko:live:logic-symbols -->", math)
        self.assertNotIn(r"Greek: `\alpha \beta", math)          # the old copied symbol lists are gone


if __name__ == "__main__":
    unittest.main()
