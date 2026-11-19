from __future__ import annotations

import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path

from saga.processor_runtime import ProcessorSandboxError, write_outputs


class ProcessorOutputTests(unittest.TestCase):
    def test_invalid_later_path_does_not_touch_earlier_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / "result.txt"
            existing.write_text("original", encoding="utf-8")

            with self.assertRaises(ProcessorSandboxError):
                write_outputs(root, {"result.txt": "changed", "../outside.txt": "bad"})

            self.assertEqual(existing.read_text(encoding="utf-8"), "original")

    def test_parent_child_targets_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ProcessorSandboxError):
                write_outputs(root, {"a": "file", "a/b.txt": "nested"})
            self.assertFalse((root / "a").exists())

    def test_failed_replace_restores_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "first.txt"
            old.write_text("original", encoding="utf-8")
            actual_replace = os.replace
            triggered = False

            def failing_replace(source, destination):
                nonlocal triggered
                if Path(destination).name == "second.txt" and not triggered:
                    triggered = True
                    raise OSError("simulated disk failure")
                return actual_replace(source, destination)

            with patch("saga.processor_runtime.os.replace", side_effect=failing_replace):
                with self.assertRaisesRegex(OSError, "simulated"):
                    write_outputs(root, {"first.txt": "changed", "second.txt": "new"})

            self.assertEqual(old.read_text(encoding="utf-8"), "original")
            self.assertFalse((root / "second.txt").exists())

    def test_valid_files_are_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_outputs(root, {"first.txt": "one", "nested/second.txt": "two"})
            self.assertEqual((root / "first.txt").read_text(encoding="utf-8"), "one")
            self.assertEqual((root / "nested" / "second.txt").read_text(encoding="utf-8"), "two")


if __name__ == "__main__":
    unittest.main()
