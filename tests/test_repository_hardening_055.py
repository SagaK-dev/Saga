from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from saga.errors import ParseError
from saga.source_units import load_program
from saga import native_codegen
from saga import _native_codegen_impl
from saga import plugin_host, sandbox


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

    def test_plugin_process_limit_is_applied_after_namespace_creation(self) -> None:
        class FakeResource:
            RLIMIT_CORE = 1
            RLIMIT_NOFILE = 2
            RLIMIT_NPROC = 3
            RLIMIT_FSIZE = 4

            def __init__(self) -> None:
                self.calls: list[tuple[int, tuple[int, int]]] = []

            def setrlimit(self, kind: int, pair: tuple[int, int]) -> None:
                self.calls.append((kind, pair))

        outer = FakeResource()
        with patch.object(sandbox, "_resource", outer):
            sandbox._resource_limits()
        self.assertNotIn(outer.RLIMIT_NPROC, [kind for kind, _ in outer.calls])
        self.assertIn((outer.RLIMIT_NOFILE, (64, 64)), outer.calls)

        child = FakeResource()
        with patch.object(plugin_host, "resource", child):
            plugin_host._limits()
        self.assertIn((child.RLIMIT_NPROC, (8, 8)), child.calls)
        self.assertIn((child.RLIMIT_NOFILE, (32, 32)), child.calls)


if __name__ == "__main__":
    unittest.main()
