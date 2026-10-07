"""Offline regression tests: zero-balance inference, preview/submission, unexpected prices."""
import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_math_style import inko, run_cli, QUOTE, LAYOUT, JOB


class FreeInference(unittest.TestCase):
    def setUp(self):
        self.quote = {**QUOTE, "price": {"list_cents": 0}, "account": {"balance_cents": 0}}
        self.responses = {"/quote": self.quote, "/layout": LAYOUT, "/generations": JOB}
        for name in ("log_job", "remember_files"):
            p = patch.object(inko, name)
            p.start()
            self.addCleanup(p.stop)

    def test_zero_is_free_regardless_of_balance_or_legacy_quota(self):
        for account in (None, {}, {"balance_cents": 0}, {"balance_cents": 500, "quota_cents": 300}):
            for count in (1, 99, 100, 20000):
                q = inko._summarize_quote({"chars": count, "price": {"list_cents": 0}, "account": account})
                self.assertEqual(q["price_cny"], 0)
                self.assertIn("Free handwriting inference", q["payment"])
                self.assertIn("no balance deduction", q["payment"])

    def test_missing_price_does_not_reconstruct_old_tariff(self):
        for price in (None, {}, {"list_cents": None}):
            self.assertEqual(inko._summarize_quote({"chars": 20000, "price": price})["price_cny"], 0)

    def test_preview_never_submits(self):
        out, sent = run_cli(["generate", "--text", "hello"], self.responses)
        self.assertFalse(out["submitted"])
        self.assertEqual(out["quote"]["price_cny"], 0)
        self.assertFalse(any(p == "/generations" for _, p, _ in sent))
        self.assertIn("no payment confirmation", out["next"])

    def test_zero_balance_can_submit_both_models(self):
        for model in ("lyric-1", "logic-1"):
            out, sent = run_cli(["generate", "--text", "hello", "--model", model, "--yes", "--no-wait"], self.responses)
            self.assertTrue(out["submitted"])
            self.assertEqual(sum(p == "/generations" for _, p, _ in sent), 1)

    def test_nonzero_quote_is_not_hidden_and_blocks_submission(self):
        responses = {**self.responses, "/quote": {**self.quote, "price": {"list_cents": 20}}}
        out, sent = run_cli(["generate", "--text", "hello", "--yes", "--no-wait"], responses)
        self.assertFalse(out["submitted"])
        self.assertEqual(out["reason"], "over_budget")
        self.assertEqual(out["quote"]["price_cny"], 0.2)
        self.assertEqual(out["max_cents"], 0)
        self.assertFalse(any(p == "/generations" for _, p, _ in sent))

    def test_invalid_content_still_blocks_free_generation(self):
        responses = {**self.responses, "/quote": {**self.quote, "ok": False, "errors": [{"kind": "unsupported"}]}}
        out, sent = run_cli(["generate", "--text", "hello", "--yes"], responses)
        self.assertFalse(out["submitted"])
        self.assertEqual(out["reason"], "text_not_writable")
        self.assertFalse(any(p == "/generations" for _, p, _ in sent))

    def test_layout_zero_missing_and_positive_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "layout.json"
            spec.write_text(json.dumps({"v": 2, "text": "hello", "paperId": "blank", "d": {"size": 7}}), encoding="utf-8")
            for price, submits in (({}, True), ({"list_cents": 0}, True), ({"list_cents": 20}, False)):
                responses = {**self.responses, "/layout": {**LAYOUT, "price": price}}
                out, sent = run_cli(["generate", "--layout", str(spec), "--yes", "--no-wait"], responses)
                self.assertEqual(out["submitted"], submits, out)
                self.assertEqual(any(p == "/generations" for _, p, _ in sent), submits)

    def test_installer_copies_updated_skill_and_client(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("inko_installer", root / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            for dst in installer.targets("both", "project", Path(tmp)):
                installer.copy_skill(dst, False)
                for rel in ("SKILL.md", "scripts/inko.py", "references/api.md", "references/styles.md"):
                    self.assertEqual((dst / rel).read_bytes(), (root / "inko-handwriting" / rel).read_bytes())


if __name__ == "__main__":
    unittest.main()
