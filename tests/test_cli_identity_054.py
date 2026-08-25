from __future__ import annotations

import contextlib
import io
import tomllib
import unittest
from pathlib import Path

import saga
from saga import cli


class CLIIdentity054Tests(unittest.TestCase):
    def test_active_version_sources_match(self):
        project = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(project["project"]["version"], saga.__version__)
        self.assertEqual(cli.VERSION, saga.__version__)

    def test_version_flag_reports_active_package_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"Saga {saga.__version__}")

    def test_help_leads_with_machine_control_identity(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        help_text = output.getvalue()
        self.assertIn("Machine-control, robotics, and drone", help_text)
        self.assertIn("explicit hardware authority", help_text)


if __name__ == "__main__":
    unittest.main()
