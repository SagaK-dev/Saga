from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from saga import ResourceBudget
from saga import cli
from saga.errors import LexLimitError


class CLIResourceProfile053Tests(unittest.TestCase):
    def test_run_untrusted_profile_applies_deployment_budget(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "main.saga"
            source.write_text("var n = 0\nwhile true { n = n + 1 }\n", encoding="utf-8")
            tiny = ResourceBudget(max_steps=8)
            stderr = io.StringIO()
            with (
                mock.patch("saga.cli.UNTRUSTED_RESOURCE_BUDGET", tiny),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.main(["run", str(source), "--resource-profile", "untrusted"])
            self.assertNotEqual(code, 0)
            self.assertIn("実行", stderr.getvalue())

    def test_default_profile_does_not_apply_untrusted_preset(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "main.saga"
            source.write_text('print("0123456789")\n', encoding="utf-8")
            tiny = ResourceBudget(max_source_bytes=4)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("saga.cli.UNTRUSTED_RESOURCE_BUDGET", tiny),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.main(["run", str(source)])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertIn("0123456789", stdout.getvalue())

    def test_check_untrusted_profile_enforces_source_budget(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "main.saga"
            source.write_text('print("0123456789")\n', encoding="utf-8")
            tiny = ResourceBudget(max_source_bytes=4)
            stderr = io.StringIO()
            with (
                mock.patch("saga.cli.UNTRUSTED_RESOURCE_BUDGET", tiny),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.main(["check", str(source), "--resource-profile", "untrusted"])
            self.assertNotEqual(code, 0)
            self.assertIn("ソースサイズ", stderr.getvalue())

    def test_test_command_applies_untrusted_profile_to_each_source(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "case.saga"
            source.write_text('print("0123456789")\n', encoding="utf-8")
            tiny = ResourceBudget(max_source_bytes=4)
            stderr = io.StringIO()
            with (
                mock.patch("saga.cli.UNTRUSTED_RESOURCE_BUDGET", tiny),
                contextlib.redirect_stderr(stderr),
            ):
                code = cli.main(["test", td, "--resource-profile", "untrusted"])
            self.assertNotEqual(code, 0)
            self.assertIn("ソースサイズ", stderr.getvalue())

    def test_bounded_diagnostics_do_not_read_oversized_file_whole(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "large.saga"
            source.write_bytes(b"x" * 1024)
            error = LexLimitError("too large", 1, 1, str(source))
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")):
                text = cli._diagnostic_source(
                    error,
                    "fallback",
                    ResourceBudget(max_source_bytes=16),
                )
            self.assertEqual(text, "fallback")

    def test_resource_profile_is_exposed_on_execution_commands(self):
        for command in ("run", "check", "test"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main([command, "--help"])
            self.assertEqual(ctx.exception.code, 0)
            self.assertIn("--resource-profile", output.getvalue())


if __name__ == "__main__":
    unittest.main()
