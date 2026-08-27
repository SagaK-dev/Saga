from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from saga.aot import AOTError
from saga.native_codegen import build_native_codegen, native_function_symbol


@unittest.skipUnless(shutil.which("clang") or shutil.which("cc") or shutil.which("gcc"), "C toolchain required")
class NativeCodegenABIRegressionTests(unittest.TestCase):
    def write(self, root: Path, name: str, source: str) -> Path:
        path = root / name
        path.write_text(source.strip() + "\n", encoding="utf-8")
        return path

    def run_binary(self, path: Path) -> str:
        proc = subprocess.run([str(path)], text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip()

    def project(self, root: Path) -> tuple[Path, Path]:
        models = self.write(root, "models.saga", '''
module models
public fn twice(value: int) -> int = value * 2
public fn even(value: int) -> bool = value % 2 == 0
''')
        main = self.write(root, "main.saga", '''
use "models.saga" as m
fn local(value: int) -> int = m.twice(value) + 1
print(local(20))
print(m.even(4))
''')
        return main, models

    def test_cross_module_calls_are_real_native_symbols_without_go_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, _ = self.project(root)
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), "41\ntrue")
            report = json.loads(result.report.read_text(encoding="utf-8"))
            self.assertFalse(report["go_runtime_linked"])
            self.assertEqual(report["profile"], "Native Codegen ABI 0.35")

            if shutil.which("nm") and os.name != "nt":
                main_obj = next(path for path in result.objects if "main.saga" in path.name)
                model_obj = next(path for path in result.objects if "models.saga" in path.name)
                symbol = native_function_symbol("models", "twice")
                main_nm = subprocess.check_output(["nm", str(main_obj)], text=True)
                model_nm = subprocess.check_output(["nm", str(model_obj)], text=True)
                self.assertIn(f"U {symbol}", main_nm)
                self.assertIn(f"T {symbol}", model_nm)
                final_nm = subprocess.check_output(["nm", str(result.output)], text=True)
                self.assertNotIn("runtime.main", final_nm)
                self.assertNotIn("crosscall", final_nm)

    def test_recursive_control_flow_continue_modulo_and_eval_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "math.saga", '''
module math
public fn fact(n: int) -> int {
  if n <= 1 { return 1 }
  return n * fact(n - 1)
}
public fn sumSkip(n: int) -> int {
  var total: int = 0
  for i in 1..n {
    if i == 2 { continue }
    total = total + i
  }
  return total
}
public fn modulo() -> int = -5 % 3
public fn first() -> int { print(1); return 1 }
public fn second() -> int { print(2); return 2 }
public fn combine(a: int, b: int) -> int = a + b
public fn noisy() -> bool { print(9); return true }
public fn shortCircuit() -> bool = false and noisy()
''')
            main = self.write(root, "main.saga", '''
use "math.saga" as m
print(m.fact(5))
print(m.sumSkip(4))
print(m.modulo())
print(m.combine(m.first(), m.second()))
print(m.shortCircuit())
''')
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), "120\n8\n1\n1\n2\n3\nfalse")

    def test_incremental_compile_uses_dependency_native_abi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, models = self.project(root)
            build_dir = root / "build"
            output = root / "app"
            first = build_native_codegen(main, output, build_dir=build_dir)
            self.assertEqual(set(first.compiled_objects), {"project/main.saga", "project/models.saga"})
            second = build_native_codegen(main, output, build_dir=build_dir)
            self.assertEqual(second.compiled_objects, ())
            self.assertFalse(second.linked)

            models.write_text('module models\npublic fn twice(value: int) -> int = value * 3\npublic fn even(value: int) -> bool = value % 2 == 0\n', encoding="utf-8")
            third = build_native_codegen(main, output, build_dir=build_dir)
            self.assertEqual(third.compiled_objects, ("project/models.saga",))
            self.assertEqual(third.reused_objects, ("project/main.saga",))
            self.assertTrue(third.linked)
            self.assertEqual(self.run_binary(output), "61\ntrue")

            models.write_text('module models\npublic fn twice(value: int) -> int = value * 3\npublic fn even(value: int) -> bool = value % 2 == 0\npublic fn spare() -> int = 7\n', encoding="utf-8")
            fourth = build_native_codegen(main, output, build_dir=build_dir)
            self.assertEqual(set(fourth.compiled_objects), {"project/main.saga", "project/models.saga"})
            self.assertTrue(fourth.linked)

    def test_native_abi_manifest_has_stable_public_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, _ = self.project(root)
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            manifests = list((result.build_dir / "abi").glob("*.nabi.json"))
            model = next(json.loads(p.read_text(encoding="utf-8")) for p in manifests if json.loads(p.read_text(encoding="utf-8"))["module"] == "models")
            exports = {item["name"]: item for item in model["exports"]}
            self.assertEqual(exports["twice"]["symbol"], native_function_symbol("models", "twice"))
            self.assertEqual(exports["twice"]["params"], ["int"])
            self.assertEqual(exports["twice"]["return"], "int")
            self.assertEqual(model["abi_version"], "0.35")

    def test_remaining_unstable_generic_inheritance_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = self.write(root, "main.saga", """
class Base[T](let value: T) {}
class Child(let other: int) extends Base[int] {}
print(1)
""")
            output = root / "app"
            with self.assertRaisesRegex(AOTError, "generic inheritance|generic base"):
                build_native_codegen(main, output, build_dir=root / "build")
            self.assertFalse(output.exists())

    def test_natural_first_binding_is_lowered_as_native_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = self.write(root, "main.saga", '''
fn plusOne(value: int) -> int {
  next = value + 1
  return next
}
print(plusOne(41))
''')
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), "42")

    def test_generated_c_header_can_call_module_object_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, _ = self.project(root)
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            model_obj = next(path for path in result.objects if "models.saga" in path.name)
            header = next(path for path in (result.build_dir / "abi").glob("*.nabi.h") if "models.saga" in path.name)
            support_obj = next((result.build_dir / "support").rglob("saga_native_abi035.o"))
            symbol = native_function_symbol("models", "twice")
            harness = root / "harness.c"
            harness.write_text(
                f'#include "{header.name}"\n#include <stdio.h>\nint main(void) {{ printf("%lld\\n", (long long){symbol}(21)); return 0; }}\n',
                encoding="utf-8",
            )
            cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
            external = root / "external-abi-client"
            proc = subprocess.run([cc, str(harness), str(model_obj), str(support_obj), "-I", str(header.parent), "-I", str(support_obj.parent), "-o", str(external)], text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(self.run_binary(external), "42")

    def test_dependency_module_initialization_is_rejected_instead_of_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "dep.saga", 'module dep\npublic let answer: int = 42\npublic fn value() -> int = answer')
            main = self.write(root, "main.saga", 'use "dep.saga" as d\nprint(d.value())')
            with self.assertRaisesRegex(AOTError, "module initialization is not yet in ABI 0.35"):
                build_native_codegen(main, root / "app", build_dir=root / "build")


    def test_q31_control_primitives_lower_to_native_integer_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = self.write(root, "main.saga", """
use machine

@control_tick(60000, 8)
fn currentTick(error: int, gain: int) -> int {
    return machine.q31_mul_sat(error, gain)
}

print(currentTick(machine.q31_from_ratio(1, 2), machine.q31_from_ratio(1, 2)))
print(machine.q31_add_sat(2147483647, 1))
print(machine.q31_mac_sat(536870912, 1073741824, 1073741824))
""")
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(
                self.run_binary(result.output),
                "536870912\n2147483647\n1073741824",
            )
            generated = "\n".join(
                path.read_text(encoding="utf-8")
                for path in result.build_dir.rglob("*.c")
            )
            self.assertIn("saga_abi035_machine_q31_mul_sat", generated)
            self.assertIn("saga_abi035_machine_q31_mac_sat", generated)


if __name__ == "__main__":
    unittest.main()
