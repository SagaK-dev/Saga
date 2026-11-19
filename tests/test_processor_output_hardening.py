from __future__ import annotations

import tempfile
import unittest
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

    def test_valid_files_are_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_outputs(root, {"first.txt": "one", "nested/second.txt": "two"})
            self.assertEqual((root / "first.txt").read_text(encoding="utf-8"), "one")
            self.assertEqual((root / "nested" / "second.txt").read_text(encoding="utf-8"), "two")


if __name__ == "__main__":
    unittest.main()
