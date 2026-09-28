#!/usr/bin/env python3
"""Offline regressions for the flat-page-only skill and upgrade cleanup."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "inko-handwriting"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from PIL import Image


class FlatScopeTests(unittest.TestCase):
    def cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def test_retired_files_absent(self):
        for path in ("scripts/photo.py", "references/paper-matching.md"):
            self.assertFalse((SKILL / path).exists(), path)

    def test_retired_commands_rejected(self):
        for script, commands in (("paper.py", ("analyze", "blanks", "rectify")),
                                 ("compose.py", ("lines", "page", "place"))):
            for command in commands:
                with self.subTest(script=script, command=command):
                    result = self.cli(script, command)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("invalid choice", result.stderr)

    def test_flat_backgrounds(self):
        with tempfile.TemporaryDirectory(prefix="inko-flat-") as tmp:
            for kind in ("blank", "ruled", "grid", "dots", "tian", "compo"):
                with self.subTest(kind=kind):
                    out = Path(tmp) / f"{kind}.png"
                    result = self.cli("paper.py", "make", "--kind", kind,
                                      "--dpi", 72, "-o", out)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    with Image.open(out) as image:
                        self.assertEqual(image.size, (595, 842))
                        self.assertEqual(image.format, "PNG")

    def test_upgrade_removes_stale_scripts_without_touching_user_key(self):
        spec = importlib.util.spec_from_file_location("inko_installer", ROOT / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory(prefix="inko-upgrade-") as tmp:
            base = Path(tmp).resolve()
            dst = base / ".agents" / "skills" / "inko-handwriting"
            self.assertTrue(dst.resolve().is_relative_to(base))
            (dst / "scripts").mkdir(parents=True)
            (dst / "scripts" / "photo.py").write_text("retired", encoding="utf-8")
            key = base / ".inko" / "key"
            key.parent.mkdir()
            key.write_text("test-only-key", encoding="utf-8")
            installer.copy_skill(dst, False)
            self.assertFalse((dst / "scripts" / "photo.py").exists())
            self.assertTrue((dst / "scripts" / "compose.py").exists())
            self.assertEqual(key.read_text(encoding="utf-8"), "test-only-key")

    def test_flat_evals_and_deliverable_discovery(self):
        spec = importlib.util.spec_from_file_location("inko_grader", ROOT / "evals" / "grade.py")
        grader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grader)
        evals = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))["evals"]
        self.assertEqual({e["name"] for e in evals}, {"math-homework-flat", "notes-to-flat-pdf"})
        for entry in evals:
            for path in entry["files"]:
                self.assertTrue((ROOT / path).is_file(), path)
        with tempfile.TemporaryDirectory(prefix="inko-eval-") as tmp:
            out = Path(tmp)
            page = out / "final-flat.png"
            Image.new("RGB", (100, 100), "white").save(page)
            self.assertEqual(grader.images(out, (".png",)), [page])
            self.assertIsInstance(grader.grade_math(out), list)
            self.assertIsInstance(grader.grade_notes(out), list)


if __name__ == "__main__":
    # This suite is always offline, including when invoked by run_tests.py --online.
    unittest.main(argv=[sys.argv[0]])
