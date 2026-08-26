from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from saga.errors import ParseError
from saga.source_units import load_program
from saga import native_codegen
from saga import _native_codegen_impl


class RepositoryHardeningTests(unittest.TestCase):
    def test_external_path_named_like_package_cache_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            external = root / ".saga" / "packages" / "fake"
            project.mkdir()
            external.mkdir(parents=True)
            (project / "main.saga").write_text(
                'use "../.saga/packages/fake/escape.saga"\nprint(1)\n',
                encoding="utf-8",
            )
            (external / "escape.saga").write_text("fn escaped() -> int = 1\n", encoding="utf-8")

            with self.assertRaises(ParseError) as caught:
                load_program(project / "main.saga", root=project)

            self.assertIn("プロジェクト外", str(caught.exception))

    def test_native_codegen_cache_identity_tracks_implementation_bytes(self) -> None:
        fingerprint = sha256(Path(_native_codegen_impl.__file__).read_bytes()).hexdigest()
        expected = f"0.53.0+{fingerprint[:16]}"

        self.assertEqual(native_codegen.IMPLEMENTATION_FINGERPRINT, fingerprint)
        self.assertEqual(native_codegen.IMPLEMENTATION_VERSION, expected)
        self.assertEqual(_native_codegen_impl.IMPLEMENTATION_VERSION, expected)


if __name__ == "__main__":
    unittest.main()
