from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from saga import contest_demo


class ContestDemo054Tests(unittest.TestCase):
    def test_generated_demo_preserves_safe_unsafe_contrast(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = contest_demo.run_demo(tmp)
            root = Path(tmp)

            self.assertTrue(manifest["valid"])
            self.assertEqual(manifest["observed"]["safe"], "pass")
            self.assertEqual(manifest["observed"]["unsafe"], "fail")
            self.assertTrue(
                any(code.startswith("SAGA-C") for code in manifest["observed"]["unsafe_diagnostics"])
            )
            for artifact in manifest["artifacts"]:
                self.assertTrue((root / artifact).is_file(), artifact)
            self.assertTrue((root / "manifest.json").is_file())

            persisted = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["observed"], manifest["observed"])
            self.assertIn("source-analysis contrast", persisted["boundary"])

    def test_demo_sources_match_checked_in_contest_examples(self):
        root = Path(__file__).resolve().parents[1]
        safe = (root / "examples/contest/diff_safe_control.saga").read_text(encoding="utf-8")
        unsafe = (root / "examples/contest/diff_unsafe_control.saga").read_text(encoding="utf-8")

        self.assertEqual(contest_demo.SAFE_SOURCE, safe)
        self.assertEqual(contest_demo.UNSAFE_SOURCE, unsafe)

    def test_cli_reports_success_when_expected_contrast_is_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = contest_demo.main(["--output", tmp])

            self.assertEqual(rc, 0)
            text = output.getvalue()
            self.assertIn("safe:   PASS", text)
            self.assertIn("unsafe: FAIL", text)
            self.assertIn("index.html", text)


if __name__ == "__main__":
    unittest.main()
