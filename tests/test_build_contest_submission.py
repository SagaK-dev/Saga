from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "build_contest_submission.py"
SPEC = importlib.util.spec_from_file_location("build_contest_submission", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _minimal_submission_tree(root: Path) -> None:
    _write(root, "README.md", "# Saga\n")
    _write(root, "LICENSE", "MIT\n")
    _write(root, "pyproject.toml", "[project]\nname='saga-lang'\n")
    _write(root, "CONTEST_DEMO.md", "# Demo\n")
    _write(root, "SUBMISSION_README_JA.md", "# Submission\n")
    _write(root, "saga/__init__.py", "VERSION = 'test'\n")
    _write(root, "saga/checker.py", "def check(): return True\n")
    _write(root, "docs/CONTEST_SUBMISSION_DEVELOPMENT_GUIDE_JA.md", "# Guide\n")
    _write(root, "docs/PROGRAMMING_FLOW_JA.md", "# Flow\n")
    _write(root, "docs/SUBMISSION_CHECKLIST_JA.md", "# Checklist\n")
    _write(root, "tests/test_sample.py", "def test_sample(): assert True\n")


class ContestSubmissionBuilderTests(unittest.TestCase):
    def test_preflight_reports_missing_required_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            errors = builder.validate_repository(Path(tmp))
        self.assertTrue(errors)
        self.assertTrue(any("README.md" in error for error in errors))
        self.assertTrue(any("saga" in error for error in errors))

    def test_archive_is_deterministic_and_excludes_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_submission_tree(root)
            _write(root, ".venv/secret.txt", "must not ship\n")
            _write(root, "build/generated.txt", "must not ship\n")
            _write(root, "saga/__pycache__/cached.pyc", "must not ship\n")
            _write(root, "demo.mp4", "video is uploaded separately\n")

            first = root / "first.zip"
            second = root / "second.zip"
            builder.build_archive(root, first)
            builder.build_archive(root, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())

            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                prefix = f"{builder.ARCHIVE_ROOT}/"
                self.assertIn(f"{prefix}README.md", names)
                self.assertIn(f"{prefix}saga/checker.py", names)
                self.assertIn(f"{prefix}docs/PROGRAMMING_FLOW_JA.md", names)
                self.assertIn(f"{prefix}SUBMISSION_SHA256SUMS.txt", names)

                self.assertFalse(any(".venv/" in name for name in names))
                self.assertFalse(any("build/" in name for name in names))
                self.assertFalse(any("__pycache__/" in name for name in names))
                self.assertFalse(any(name.endswith(".mp4") for name in names))
                self.assertFalse(any(name.endswith(".zip") for name in names))

    def test_checksum_manifest_matches_archived_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_submission_tree(root)
            output = root / "submission.zip"
            builder.build_archive(root, output)

            with zipfile.ZipFile(output) as archive:
                prefix = f"{builder.ARCHIVE_ROOT}/"
                manifest_text = archive.read(
                    f"{prefix}SUBMISSION_SHA256SUMS.txt"
                ).decode("utf-8")
                entries: dict[str, str] = {}
                for line in manifest_text.splitlines():
                    digest, relative = line.split("  ", 1)
                    entries[relative] = digest

                self.assertTrue(entries)
                for relative, expected in entries.items():
                    data = archive.read(f"{prefix}{relative}")
                    self.assertEqual(hashlib.sha256(data).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
