from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from saga import ast_nodes as ast
from saga.aot import build_standard_bundle
from saga.mobile import generate_ios
from saga.api import compile_file, run_file
from saga.errors import ParseError, TypeCheckError
from saga.module_interface import build_module_interface, load_module_interface


class NaturalModuleCore030Tests(unittest.TestCase):
    def write(self, root: Path, name: str, source: str) -> Path:
        path = root / name
        path.write_text(source.strip() + "\n", encoding="utf-8")
        return path

    def test_namespaced_public_runtime_and_internal_is_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "models.saga", '''
module models
public class User(let name: text) { fn greet() -> text = "Hello " + self.name }
public fn twice(x: int) -> int = x * 2
internal fn hidden() -> int = 99
''')
            main = self.write(root, "main.saga", '''
use "models.saga" as m
let u: m.User = m.User("Aki")
print(u.greet())
print(m.twice(21))
''')
            output: list[str] = []
            run_file(str(main), output=output.append)
            self.assertEqual(output, ["Hello Aki", "42"])
            bad = self.write(root, "bad.saga", 'use "models.saga" as m\nprint(m.hidden())')
            with self.assertRaises(TypeCheckError) as caught:
                compile_file(str(bad))
            self.assertEqual(caught.exception.diagnostic_id, "SAGA-T106")

    def test_imported_public_class_can_be_base_at_typecheck_and_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "models.saga", '''
module models
public class User(let name: text) { fn greet() -> text = "Hello " + self.name }
''')
            main = self.write(root, "main.saga", '''
use "models.saga" as m
class Local(let id: int) extends m.User {
    fn label() -> text = self.name + ":" + text(self.id)
}
let x = Local("Aki", 7)
print(x.greet())
print(x.label())
''')
            output: list[str] = []
            run_file(str(main), output=output.append)
            self.assertEqual(output, ["Hello Aki", "Aki:7"])

    def test_same_spelling_from_two_modules_has_distinct_nominal_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "a.saga", 'module a\npublic class User(let name: text) {}')
            self.write(root, "b.saga", 'module b\npublic class User(let name: text) {}')
            bad = self.write(root, "main.saga", '''
use "a.saga" as a
use "b.saga" as b
let value: a.User = b.User("B")
''')
            with self.assertRaises(TypeCheckError):
                compile_file(str(bad))

    def test_public_api_cannot_leak_internal_or_dependency_nominal_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            own = self.write(root, "own.saga", '''
module own
class Secret(let value: int) {}
public fn reveal(x: Secret) -> int = x.value
''')
            with self.assertRaises(TypeCheckError) as caught:
                compile_file(str(own))
            self.assertEqual(caught.exception.diagnostic_id, "SAGA-T118")

            self.write(root, "dep.saga", 'module dep\npublic class User(let name: text) {}')
            facade = self.write(root, "facade.saga", '''
module facade
use "dep.saga" as d
public fn make() -> d.User = d.User("x")
''')
            with self.assertRaises(TypeCheckError) as caught:
                compile_file(str(facade))
            self.assertEqual(caught.exception.diagnostic_id, "SAGA-T118")

    def test_module_directive_is_unique_and_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            late = self.write(root, "late.saga", 'let x = 1\nmodule late')
            with self.assertRaises(ParseError):
                compile_file(str(late))
            duplicate = self.write(root, "dup.saga", 'module one\nmodule two\nprint(1)')
            with self.assertRaises(ParseError):
                compile_file(str(duplicate))

    def test_legacy_source_units_remain_flattened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "helper.saga", 'fn add(a: int, b: int) -> int = a + b')
            main = self.write(root, "main.saga", 'use "helper.saga"\nprint(add(2, 3))')
            output: list[str] = []
            run_file(str(main), output=output.append)
            self.assertEqual(output, ["5"])

    def test_same_module_cannot_use_multiple_aliases_including_default_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "models.saga", 'module models\npublic fn value() -> int = 1')
            bad = self.write(root, "main.saga", '''
use "models.saga" as m
use "models.saga"
print(m.value())
''')
            with self.assertRaises(ParseError) as caught:
                compile_file(str(bad))
            self.assertEqual(caught.exception.diagnostic_id, "SAGA-P109")

    def test_interface_generation_verification_and_cache_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "models.saga", '''
module models
public class User(let name: text) { fn greet() -> text = self.name }
public fn twice(x: int) -> int = x * 2
public let answer: int = 42
''')
            interface = build_module_interface(module)
            self.assertEqual(interface["schema"], "saga.module-interface.v1")
            self.assertEqual(load_module_interface(root / "models.smi.json", source=module)["abi_sha256"], interface["abi_sha256"])
            main = self.write(root, "main.saga", 'use "models.saga" as m\nprint(m.twice(m.answer))')
            default_loaded = compile_file(str(main))
            default_modules = [s for s in default_loaded.program.statements if isinstance(s, ast.SourceModuleStmt)]
            self.assertEqual(len(default_modules), 1)
            self.assertIsNone(default_modules[0].interface)

            trusted_loaded = compile_file(str(main), trust_module_interfaces=True)
            trusted_modules = [s for s in trusted_loaded.program.statements if isinstance(s, ast.SourceModuleStmt)]
            self.assertEqual(len(trusted_modules), 1)
            self.assertIsNotNone(trusted_modules[0].interface)

    def test_fresh_looking_forged_interface_cannot_bypass_default_control_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "controller.saga", '''
module controller
public @control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    return error
}
''')
            build_module_interface(module)
            iface_path = root / "controller.smi.json"
            data = json.loads(iface_path.read_text(encoding="utf-8"))

            module.write_text('''
module controller
use machine
public @control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    let sampled_at = machine.monotonic_ns()
    return error
}
'''.strip() + "\n", encoding="utf-8")

            data["source_sha256"] = hashlib.sha256(module.read_bytes()).hexdigest()
            build_payload = {
                "source_sha256": data["source_sha256"],
                "abi_sha256": data["abi_sha256"],
                "dependencies": data.get("dependencies", []),
            }
            data["build_sha256"] = hashlib.sha256(
                json.dumps(
                    build_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            iface_path.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            main = self.write(root, "main.saga", 'use "controller.saga" as ctrl\nprint(1)')
            with self.assertRaises(TypeCheckError) as caught:
                compile_file(str(main))

            self.assertEqual(caught.exception.diagnostic_id, "SAGA-C492")

    def test_stale_interface_falls_back_to_source_checking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "models.saga", 'module models\npublic fn twice(x: int) -> int = x * 2')
            build_module_interface(module)
            main = self.write(root, "main.saga", 'use "models.saga" as m\nprint(m.twice(2))')
            compile_file(str(main))
            module.write_text('module models\npublic fn twice(x: int) -> int = "bad"\n', encoding="utf-8")
            with self.assertRaises(TypeCheckError):
                compile_file(str(main))

    def test_dependency_abi_controls_parent_interface_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dep = self.write(root, "dep.saga", 'module dep\npublic fn value() -> int = 1')
            parent = self.write(root, "parent.saga", '''
module parent
use "dep.saga" as d
public fn doubled() -> int = d.value() * 2
''')
            build_module_interface(parent)
            parent_iface = root / "parent.smi.json"
            first = load_module_interface(parent_iface, source=parent)

            # Implementation-only dependency changes preserve ABI after the
            # dependency interface is refreshed, so importer SMI remains valid.
            dep.write_text('module dep\npublic fn value() -> int = 2\n', encoding="utf-8")
            dep_iface = build_module_interface(dep)
            self.assertEqual(dep_iface["abi_sha256"], first["dependencies"][0]["abi_sha256"])
            self.assertEqual(load_module_interface(parent_iface, source=parent)["abi_sha256"], first["abi_sha256"])

            # A public signature change invalidates the already-compiled parent.
            dep.write_text('module dep\npublic fn value() -> text = "2"\n', encoding="utf-8")
            build_module_interface(dep)
            with self.assertRaisesRegex(ValueError, "dependency ABI"):
                load_module_interface(parent_iface, source=parent)

    def test_method_reordering_does_not_change_public_abi_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "models.saga", '''
module models
public class User(let name: text) {
  fn a() -> int = 1
  fn b() -> int = 2
}
''')
            first = build_module_interface(module)["abi_sha256"]
            module.write_text('''module models
public class User(let name: text) {
  fn b() -> int = 2
  fn a() -> int = 1
}
''', encoding="utf-8")
            second = build_module_interface(module)["abi_sha256"]
            self.assertEqual(first, second)

    @unittest.skipUnless(shutil.which("go"), "Go toolchain required")
    def test_python_and_go_emit_same_common_module_abi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "models.saga", '''
module models
public class User(let name: text) { fn greet() -> text = self.name }
public fn twice(x: int) -> int = x * 2
public let answer: int = 42
''')
            py = build_module_interface(module, output=root / "python.smi.json")
            go_dir = Path(__file__).resolve().parents[1] / "implementations" / "go" / "cmd" / "saga-go"
            result = subprocess.run(
                ["go", "run", ".", "module", "compile", str(module), str(root / "go.smi.json")],
                cwd=go_dir, text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            go_data = json.loads((root / "go.smi.json").read_text(encoding="utf-8"))
            self.assertEqual(py["exports"], go_data["exports"])
            self.assertEqual(py["abi_sha256"], go_data["abi_sha256"])
            self.assertEqual(py["build_sha256"], go_data["build_sha256"])

    def test_interface_rejects_build_hash_tampering_and_symlink_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "models.saga", 'module models\npublic fn value() -> int = 1')
            build_module_interface(module)
            iface_path = root / "models.smi.json"
            data = json.loads(iface_path.read_text(encoding="utf-8"))
            data["build_sha256"] = "0" * 64
            iface_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "build hash"):
                load_module_interface(iface_path, source=module)

            external = root / "external.smi.json"
            external.write_text("keep", encoding="utf-8")
            linked = root / "linked.smi.json"
            try:
                linked.symlink_to(external)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                build_module_interface(module, output=linked)
            self.assertEqual(external.read_text(encoding="utf-8"), "keep")

            source_target = root / "source-target.saga"
            source_target.write_text('module linked\npublic fn value() -> int = 1\n', encoding="utf-8")
            source_link = root / "source-link.saga"
            try:
                source_link.symlink_to(source_target)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(ValueError, "source.*symbolic link"):
                build_module_interface(source_link)

    def test_public_base_cannot_leak_dependency_nominal_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "dep.saga", 'module dep\npublic class Base(let name: text) {}')
            facade = self.write(root, "facade.saga", '''
module facade
use "dep.saga" as d
public class Child(let id: int) extends d.Base {}
''')
            with self.assertRaises(TypeCheckError) as caught:
                compile_file(str(facade))
            self.assertEqual(caught.exception.diagnostic_id, "SAGA-T118")

    def test_smi_reconstructs_inherited_constructor_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = self.write(root, "models.saga", '''
module models
public class Base(let name: text) { fn greet() -> text = "Hello " + self.name }
public class Child(let id: int) extends Base { fn label() -> text = self.name + ":" + text(self.id) }
''')
            build_module_interface(module)
            main = self.write(root, "main.saga", '''
use "models.saga" as m
let value: m.Child = m.Child("Aki", 7)
print(value.greet())
print(value.label())
''')
            loaded = compile_file(str(main))
            source_modules = [st for st in loaded.program.statements if isinstance(st, ast.SourceModuleStmt)]
            self.assertTrue(source_modules and source_modules[0].interface is not None)
            output: list[str] = []
            run_file(str(main), output=output.append)
            self.assertEqual(output, ["Hello Aki", "Aki:7"])

    @unittest.skipUnless(shutil.which("go"), "Go toolchain required")
    def test_python_and_go_share_project_root_for_cross_directory_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "shared").mkdir()
            (root / "saga.toml").write_text('''
[project]
name = "module-parity"
version = "0.1.0"
language = "1.0"
entry = "src/main.saga"
'''.strip() + "\n", encoding="utf-8")
            self.write(root / "shared", "models.saga", 'module models\npublic fn value() -> int = 42')
            main = self.write(root / "src", "main.saga", 'use "../shared/models.saga" as m\nprint(m.value())')
            py_output: list[str] = []
            run_file(str(main), output=py_output.append)
            self.assertEqual(py_output, ["42"])

            go_dir = Path(__file__).resolve().parents[1] / "implementations" / "go" / "cmd" / "saga-go"
            result = subprocess.run(
                ["go", "run", ".", "run", str(main)],
                cwd=go_dir, text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip().splitlines(), ["42"])

            app = self.write(
                root / "src",
                "app.saga",
                'module app\nuse "../shared/models.saga" as m\npublic fn answer() -> int = m.value()',
            )
            py_iface = build_module_interface(app, output=root / "python-app.smi.json")
            go_compile = subprocess.run(
                ["go", "run", ".", "module", "compile", str(app), str(root / "go-app.smi.json")],
                cwd=go_dir, text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(go_compile.returncode, 0, go_compile.stdout + go_compile.stderr)
            go_iface = json.loads((root / "go-app.smi.json").read_text(encoding="utf-8"))
            self.assertEqual(py_iface["abi_sha256"], go_iface["abi_sha256"])
            self.assertEqual(py_iface["build_sha256"], go_iface["build_sha256"])

    @unittest.skipUnless(shutil.which("go"), "Go toolchain required")
    def test_namespaced_module_survives_standard_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(
                root,
                "models.saga",
                'module models\npublic class User(let name: text) { fn greet() -> text = "Hello " + self.name }\npublic fn twice(value: int) -> int = value * 2',
            )
            main = self.write(
                root,
                "main.saga",
                'use "models.saga" as m\nuser = m.User("Aki")\nprint user.greet()\nprint m.twice(21)',
            )
            output = root / "app"
            build_standard_bundle(main, "native", output)
            result = subprocess.run([str(output)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip().splitlines(), ["Hello Aki", "42"])

    @unittest.skipUnless(shutil.which("go"), "Go toolchain required")
    def test_namespaced_module_survives_mobile_standard_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "models.saga", 'module models\npublic fn value() -> int = 42')
            main = self.write(root, "main.saga", 'use "models.saga" as m\nprint(m.value())')
            runtime = generate_ios(main, root / "ios") / "StandardCoreRuntime"
            (runtime / "module_runtime_test.go").write_text(
                'package sagaruntime\nimport "testing"\nfunc TestModuleProgram(t *testing.T){got,err:=Run();if err!=nil{t.Fatal(err)};if got!="42"{t.Fatalf("got %q",got)}}\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                ["go", "test", "./...", "-count=1"],
                cwd=runtime, text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
