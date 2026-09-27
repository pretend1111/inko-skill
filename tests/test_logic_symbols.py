"""Offline client contract tests for Logic's 2026-09-28 symbol update.

Run: python tests/test_logic_symbols.py (mock API only; no key, network or generation).
"""
import copy
import unittest

from test_math_style import QUOTE, run_cli


class LogicSymbolContract(unittest.TestCase):
    def test_models_passes_empty_beta_and_explicit_eta_rewrite(self):
        symbols = {"stable": [r"\eta", r"\wedge", r"\vee"], "beta": [],
                   "unsupported": [r"\oplus", r"\odot"], "rewrites": {r"\eta": "n"}}
        out, sent = run_cli(["models", "--symbols"], {"/models": {"data": [{"id": "logic-1", "symbols": symbols}]}})
        self.assertEqual(out["data"][0]["symbols"], symbols)
        self.assertEqual([(m, p) for m, p, _ in sent], [("GET", "/models")])

    def test_quote_preserves_eta_command_for_server_rewrite(self):
        text = r"取 $\eta_i^2$"
        out, sent = run_cli(["quote", "--text", text, "--model", "logic-1"], {"/quote": QUOTE})
        self.assertTrue(out["ok"])
        self.assertEqual(sent[0][2]["text"], text)

    def test_newly_blocked_symbols_never_submit_even_with_yes(self):
        for symbol in (r"\#", r"\%", r"\uparrow", r"\downarrow", r"\leftarrow", r"\oplus", r"\odot"):
            with self.subTest(symbol=symbol):
                quote = copy.deepcopy(QUOTE)
                quote.update(ok=False, warnings=[], errors=[{"kind": "unsupported_symbol", "symbol": symbol,
                             "paragraph": 1, "snippet": symbol, "message": "暂不支持"}])
                out, sent = run_cli(["generate", "--text", f"$a {symbol} b$", "--model", "logic-1", "--yes"],
                                    {"/quote": quote})
                self.assertFalse(out["submitted"])
                self.assertEqual(out["reason"], "text_not_writable")
                self.assertEqual(out["quote"]["errors"][0]["symbol"], symbol)
                self.assertEqual([(m, p) for m, p, _ in sent], [("POST", "/quote")])


if __name__ == "__main__":
    unittest.main()
