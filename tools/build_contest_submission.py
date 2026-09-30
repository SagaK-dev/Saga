#!/usr/bin/env python3
"""Build a deterministic source ZIP for the DIFF Shizuoka 2026 submission.

This script intentionally uses only the Python standard library so that the
submission package can be reproduced without installing Saga first.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

ARCHIVE_ROOT = "Saga-DIFF-Shizuoka-2026"
DEFAULT_OUTPUT = Path("dist") / f"{ARCHIVE_ROOT}.zip"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

REQUIRED_PATHS = (
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "CONTEST_DEMO.md",
    "SUBMISSION_README_JA.md",
    "saga",
    "docs/CONTEST_SUBMISSION_DEVELOPMENT_GUIDE_JA.md",
    "docs/PROGRAMMING_FLOW_JA.md",
    "docs/SUBMISSION_CHECKLIST_JA.md",
)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}

# Submission requires source. Large/generated media and compiled artifacts do
# not belong in the source ZIP; the execution video is uploaded separately.
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".o",
    ".obj",
    ".a",
    ".lib",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_repository(root: Path) -> list[str]:
    """Return human-readable preflight errors; an empty list means ready."""
    errors: list[str] = []
    for required in REQUIRED_PATHS:
        if not (root / required).exists():
            errors.append(f"missing required submission path: {required}")

    saga_dir = root / "saga"
    if saga_dir.is_dir() and not any(saga_dir.rglob("*.py")):
        errors.append("saga/ contains no Python source files")

    return errors


def should_include(path: Path, root: Path, output: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False

    try:
        relative = path.relative_to(root)
    except ValueError:
        return False

    if path.resolve() == output.resolve():
        return False
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
        return False
    if relative.name in EXCLUDED_FILE_NAMES:
        return False
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def collect_source_files(root: Path, output: Path) -> list[Path]:
    files = [
        path
        for path in root.rglob("*")
        if should_include(path, root=root, output=output)
    ]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def make_zip_info(archive_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_archive(root: Path, output: Path) -> tuple[Path, str, int]:
    root = root.resolve()
    output = output.resolve()

    errors = validate_repository(root)
    if errors:
        raise ValueError("submission preflight failed:\n- " + "\n- ".join(errors))

    source_files = collect_source_files(root=root, output=output)
    if not source_files:
        raise ValueError("submission preflight failed: no source files found")

    output.parent.mkdir(parents=True, exist_ok=True)
    checksums: list[str] = []

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            archive_name = f"{ARCHIVE_ROOT}/{relative}"
            archive.writestr(make_zip_info(archive_name), data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            checksums.append(f"{sha256_bytes(data)}  {relative}")

        manifest = ("\n".join(checksums) + "\n").encode("utf-8")
        archive.writestr(
            make_zip_info(f"{ARCHIVE_ROOT}/SUBMISSION_SHA256SUMS.txt"),
            manifest,
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )

    return output, sha256_file(output), len(source_files)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of tools/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"output ZIP path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="run submission preflight without creating the ZIP",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    output = (args.output or (root / DEFAULT_OUTPUT)).resolve()

    errors = validate_repository(root)
    if errors:
        print("[NG] Saga contest submission preflight", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if args.check_only:
        print("[OK] Saga contest submission preflight")
        return 0

    try:
        archive_path, digest, count = build_archive(root=root, output=output)
    except ValueError as exc:
        print(f"[NG] {exc}", file=sys.stderr)
        return 1

    print("[OK] Saga contest source package created")
    print(f"files: {count}")
    print(f"archive: {archive_path}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
