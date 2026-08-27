from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from saga import contest_demo


class ContestDemo054Tests(unittest.TestCase):
    def test_generated_demo_preserves_exact_safe_unsafe_contrast(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = contest_demo.run_demo(tmp)
            root = Path(tmp)

            self.assertTrue(manifest["valid"])
            self.assertEqual(manifest["schema"], 2)
            self.assertTrue(manifest["single_change"]["verified"])
            self.assertEqual(manifest["single_change"]["kind"], "added-line")
            self.assertEqual(manifest["single_change"]["line"], contest_demo.RISKY_LINE.strip())
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
            self.assertEqual(persisted["single_change"], manifest["single_change"])
            self.assertIn("exact one-line", persisted["boundary"])
            self.assertEqual(len(persisted["source_sha256"]["safe"]), 64)
            self.assertEqual(len(persisted["source_sha256"]["unsafe"]), 64)
            self.assertNotEqual(
                persisted["source_sha256"]["safe"],
                persisted["source_sha256"]["unsafe"],
            )
            self.assertEqual(
                persisted["judge_summary"]["category"],
                "programming-middle-school-problem-solving",
            )

            safe_report = json.loads((root / "safe-report.json").read_text(encoding="utf-8"))
            self.assertEqual(safe_report["analysis_scope"], "loaded-program")

            index = (root / "index.html").read_text(encoding="utf-8")
            self.assertIn('<html lang="ja">', index)
            self.assertIn("機械制御の「危ない1行」を", index)
            self.assertIn("同じ解析器で比較", index)
            self.assertIn("再現方法", index)
            self.assertIn("判定の境界", index)
            self.assertIn(contest_demo.RISKY_LINE.strip(), index)
            self.assertIn("50 µs", index)

    def test_unsafe_source_is_safe_source_plus_exactly_one_line(self):
        self.assertEqual(contest_demo.UNSAFE_SOURCE.count(contest_demo.RISKY_LINE), 1)
        self.assertEqual(
            contest_demo.UNSAFE_SOURCE.replace(contest_demo.RISKY_LINE, "", 1),
            contest_demo.SAFE_SOURCE,
        )

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
            self.assertIn("single change: VERIFIED", text)
            self.assertIn("safe:   PASS", text)
            self.assertIn("unsafe: FAIL", text)
            self.assertIn("index.html", text)


if __name__ == "__main__":
    unittest.main()
