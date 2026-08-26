from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from saga.native_codegen import AOTError, build_native_codegen


class NativeAggregateGC034Tests(unittest.TestCase):
    def write(self, root: Path, name: str, source: str) -> Path:
        path = root / name
        path.write_text(source, encoding="utf-8")
        return path

    def run_binary(self, path: Path) -> list[str]:
        proc = subprocess.run([str(path)], text=True, capture_output=True, check=True)
        return proc.stdout.splitlines()

    def test_enum_list_map_set_and_class_execute_direct_native(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = self.write(root, "main.saga", r'''
enum State { Ready, Running, Done }
fn choose() -> State = State.Running
fn listDemo() -> int {
    let xs: list[int] = [1, 2, 3]
    let ys: list[int] = append(xs, 4)
    return ys[3]
}
fn mapDemo() -> int {
    let m: map[text, int] = map_of("a", 1)
    let n: map[text, int] = map_put(m, "b", 7)
    return map_get(n, "b", 0)
}
fn setDemo() -> bool {
    let s: set[int] = set_of(1, 2)
    let t: set[int] = set_add(s, 3)
    return set_contains(t, 3)
}
class Box(var value: int) {
    fn add(delta: int) -> int {
        self.value = self.value + delta
        return self.value
    }
}
fn objectDemo() -> int {
    let b: Box = Box(5)
    return b.add(3)
}
print(listDemo())
print(mapDemo())
print(setDemo())
print(objectDemo())
print(choose() == State.Running)
''')
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), ["4", "7", "true", "8", "true"])
            reports = [json.loads(p.read_text()) for p in (root / "build" / "objects").glob("*.codegen.json") if p.name != "startup.codegen.json"]
            self.assertTrue(reports)
            self.assertTrue(all(x["abi_version"] == "0.35" for x in reports))

    def test_cross_module_public_class_constructor_method_and_ref_return(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write(root, "models.saga", r'''
module models
public class Box(var value: int) {
    fn add(delta: int) -> int {
        self.value = self.value + delta
        return self.value
    }
}
public fn make(value: int) -> Box = Box(value)
''')
            main = self.write(root, "main.saga", r'''
use "models.saga" as m
let a: m.Box = m.Box(10)
let b: m.Box = m.make(4)
print(a.add(5))
print(b.add(3))
print(a == a)
print(a == b)
''')
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), ["15", "7", "true", "false"])
            header = next((root / "build" / "abi").glob("models*.nabi.h")).read_text()
            self.assertIn("SagaRef", header)
            self.assertIn("_new(", header)
            self.assertIn("SagaRef saga_self", header)

    def test_nested_managed_references_survive_gc_and_are_reclaimed(self):
        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = self.write(root, "main.saga", "print(1)\n")
            build_native_codegen(main, root / "app", build_dir=root / "build")
            header = next((root / "build" / "support").glob("*/saga_native_abi035.h"))
            obj = next((root / "build" / "support").glob("*/saga_native_abi035.o"))
            harness = self.write(root, "gc.c", r'''
#include "saga_native_abi035.h"
#include <stdio.h>
int main(void) {
    uint64_t mark = saga_gc_root_mark();
    SagaRef child = saga_object_new(UINT64_C(11), 0);
    saga_gc_root_ref(&child);
    SagaRef list = saga_list_new(SAGA_HV_REF, 1);
    saga_gc_root_ref(&list);
    saga_list_push(list, (SagaHeapValue){SAGA_HV_REF, {.ref = child}});
    child = NULL;
    saga_gc_collect();
    printf("%llu\n", (unsigned long long)saga_gc_live_objects());
    list = NULL;
    saga_gc_collect();
    printf("%llu\n", (unsigned long long)saga_gc_live_objects());
    saga_gc_unwind_roots(mark);
    saga_gc_shutdown();
    return 0;
}
''')
            binary = root / "gc"
            subprocess.run([cc, "-std=c11", "-I", str(header.parent), str(harness), str(obj), "-o", str(binary)], check=True)
            self.assertEqual(self.run_binary(binary), ["2", "0"])

    def test_gc_handles_object_to_list_to_object_graph(self):
        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = self.write(root, "main.saga", "print(1)\n")
            build_native_codegen(main, root / "app", build_dir=root / "build")
            header = next((root / "build" / "support").glob("*/saga_native_abi035.h"))
            obj = next((root / "build" / "support").glob("*/saga_native_abi035.o"))
            harness = self.write(root, "graph.c", r'''
#include "saga_native_abi035.h"
#include <stdio.h>
int main(void) {
    uint64_t mark = saga_gc_root_mark();
    SagaRef root = saga_object_new(UINT64_C(1), 1);
    saga_gc_root_ref(&root);
    SagaRef list = saga_list_new(SAGA_HV_REF, 1);
    saga_gc_root_ref(&list);
    SagaRef leaf = saga_object_new(UINT64_C(2), 0);
    saga_gc_root_ref(&leaf);
    saga_list_push(list, (SagaHeapValue){SAGA_HV_REF, {.ref = leaf}});
    saga_object_set(root, 0, (SagaHeapValue){SAGA_HV_REF, {.ref = list}});
    list = NULL; leaf = NULL;
    saga_gc_collect();
    printf("%llu\n", (unsigned long long)saga_gc_live_objects());
    root = NULL;
    saga_gc_collect();
    printf("%llu\n", (unsigned long long)saga_gc_live_objects());
    saga_gc_unwind_roots(mark);
    saga_gc_shutdown();
    return 0;
}
''')
            binary = root / "graph"
            subprocess.run([cc, "-std=c11", "-I", str(header.parent), str(harness), str(obj), "-o", str(binary)], check=True)
            self.assertEqual(self.run_binary(binary), ["3", "0"])

    def test_private_layout_change_invalidates_importer_native_object(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            models = self.write(root, "models.saga", 'module models\npublic class Box(private let secret: int, let value: int) { fn get()->int=self.value }\n')
            main = self.write(root, "main.saga", 'use "models.saga" as m\nlet b:m.Box=m.Box(1,2)\nprint(b.get())\n')
            build = root / "build"
            first = build_native_codegen(main, root / "app", build_dir=build)
            self.assertEqual(set(first.compiled_objects), {"project/main.saga", "project/models.saga"})
            second = build_native_codegen(main, root / "app", build_dir=build)
            self.assertEqual(second.compiled_objects, ())
            models.write_text('module models\npublic class Box(private let secret: text, let value: int) { fn get()->int=self.value }\n', encoding="utf-8")
            main.write_text('use "models.saga" as m\nlet b:m.Box=m.Box("x",2)\nprint(b.get())\n', encoding="utf-8")
            third = build_native_codegen(main, root / "app", build_dir=build)
            self.assertIn("project/models.saga", third.compiled_objects)
            self.assertIn("project/main.saga", third.compiled_objects)

    def test_inheritance_is_now_native_and_collection_structural_equality_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inherited = self.write(root, "inherit.saga", 'class Base(let x:int) { fn get()->int=self.x }\nclass Child(let y:int) extends Base {}\nlet c=Child(4,5)\nprint(c.get())\n')
            result = build_native_codegen(inherited, root / "inherit", build_dir=root / "bi")
            self.assertEqual(self.run_binary(result.output), ["4"])
            structural = self.write(root, "eq.saga", 'let a:list[int]=[1]\nlet b:list[int]=[1]\nprint(a == b)\n')
            with self.assertRaisesRegex(AOTError, "collection structural"):
                build_native_codegen(structural, root / "eq", build_dir=root / "be")

    def test_option_of_managed_reference_uses_gc_descriptor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.write(root, "main.saga", 'fn maybe()->option[list[int]] { return some([1,2]) }\nlet x:option[list[int]]=maybe()\nprint(unwrap(x)[1])\n')
            result = build_native_codegen(source, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), ["2"])

    def test_saga_allocator_reuses_size_class_blocks_and_reports_stats(self):
        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = self.write(root, "main.saga", "print(1)\n")
            build_native_codegen(main, root / "app", build_dir=root / "build")
            header = next((root / "build" / "support").glob("*/saga_native_abi035.h"))
            obj = next((root / "build" / "support").glob("*/saga_native_abi035.o"))
            harness_source = '''#include "saga_native_abi035.h"
#include <stdio.h>
int main(void) {
    uint64_t mark = saga_gc_root_mark();
    SagaRef list = saga_list_new(SAGA_HV_I64, 4);
    saga_gc_root_ref(&list);
    uint64_t first_reserved = saga_allocator_reserved_bytes();
    printf("%d\\n", saga_allocator_live_bytes() > 0);
    list = NULL;
    saga_gc_collect();
    uint64_t after_collect = saga_allocator_reserved_bytes();
    SagaRef again = saga_list_new(SAGA_HV_I64, 4);
    saga_gc_root_ref(&again);
    printf("%d\\n", saga_allocator_reserved_bytes() == after_collect);
    printf("%d\\n", saga_allocator_peak_bytes() >= saga_allocator_live_bytes());
    printf("%d\\n", first_reserved > 0);
    saga_gc_unwind_roots(mark);
    saga_gc_shutdown();
    return 0;
}
'''
            harness = self.write(root, "alloc.c", harness_source)
            binary = root / "alloc"
            subprocess.run([cc, "-std=c11", "-I", str(header.parent), str(harness), str(obj), "-o", str(binary)], check=True)
            self.assertEqual(self.run_binary(binary), ["1", "1", "1", "1"])

    def test_generated_c_header_can_construct_and_call_public_class(self):
        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write(root, "models.saga", "module models\npublic class Box(var value: int) {\n    fn add(delta: int) -> int { self.value = self.value + delta; return self.value }\n}\n")
            main = self.write(root, "main.saga", 'use "models.saga" as m\nprint(m.Box(1).add(1))\n')
            result = build_native_codegen(main, root / "app", build_dir=root / "build")
            header = next((root / "build" / "abi").glob("models*.nabi.h"))
            model_obj = next(p for p in result.objects if "models" in p.name)
            support_obj = next((root / "build" / "support").glob("*/saga_native_abi035.o"))
            import re
            text = header.read_text()
            ctor = re.search(r"SagaRef\s+(saga_abi035_[A-Za-z0-9_]+_new)\(", text).group(1)
            method = re.search(r"int64_t\s+(saga_abi035_[A-Za-z0-9_]+_f[0-9a-f]+)\(SagaRef saga_self", text).group(1)
            harness_source = f'''#include "{header.name}"
#include <stdio.h>
int main(void) {{
    uint64_t mark = saga_gc_root_mark();
    SagaRef box = {ctor}(40);
    saga_gc_root_ref(&box);
    printf("%lld\\n", (long long){method}(box, 2));
    saga_gc_unwind_roots(mark);
    saga_gc_shutdown();
    return 0;
}}
'''
            harness = self.write(root, "client.c", harness_source)
            binary = root / "client"
            subprocess.run([cc, "-std=c11", "-I", str(header.parent), "-I", str(support_obj.parent), str(harness), str(model_obj), str(support_obj), "-o", str(binary)], check=True)
            self.assertEqual(self.run_binary(binary), ["42"])

    def test_block_roots_are_unwound_across_continue_under_gc_pressure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.write(root, "main.saga", r'''
fn stress() -> int {
    let keep: list[int] = [9]
    for i in 1..400 {
        if i % 2 == 0 {
            let temp: list[int] = [i]
            continue
        }
        let other: list[int] = [i, i]
    }
    return keep[0]
}
print(stress())
''')
            result = build_native_codegen(source, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), ["9"])
            generated = next((root / "build" / "generated").glob("main*.c")).read_text()
            self.assertIn("__saga_block_root_mark_", generated)
            self.assertIn("saga_gc_unwind_roots", generated)

    def test_payload_tagged_union_executes_and_traces_managed_refs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.write(root, "main.saga", r'''
class Box(var value: int) {
    fn add(delta: int) -> int { self.value = self.value + delta; return self.value }
}
enum Outcome { Ok(int), Err(text) }
enum MaybeBox { Some(Box), None }
fn make(okay: bool) -> Outcome {
    if okay { return Outcome.Ok(42) }
    return Outcome.Err("bad")
}
match make(true) {
    case Outcome.Ok(number) { print(number) }
    case Outcome.Err(message) { print(message) }
}
match make(false) {
    case Outcome.Ok(number) { print(number) }
    case Outcome.Err(message) { print(message) }
}
let item: MaybeBox = MaybeBox.Some(Box(5))
let values: list[MaybeBox] = [item]
match values[0] {
    case MaybeBox.Some(box) { print(box.add(2)) }
    case MaybeBox.None { print(0) }
}
''')
            result = build_native_codegen(source, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(result.output), ["42", "bad", "7"])

    def test_tagged_union_gc_root_keeps_managed_payload_alive(self):
        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = self.write(root, "main.saga", "print(1)\n")
            build_native_codegen(main, root / "app", build_dir=root / "build")
            header = next((root / "build" / "support").glob("*/saga_native_abi035.h"))
            obj = next((root / "build" / "support").glob("*/saga_native_abi035.o"))
            harness = self.write(root, "tagged_gc.c", r'''
#include "saga_native_abi035.h"
#include <stdio.h>
int main(void) {
    uint64_t mark = saga_gc_root_mark();
    SagaRef child = saga_object_new(UINT64_C(99), 0);
    SagaTagged tagged = {0};
    tagged.type_id = UINT64_C(7);
    tagged.tag = 0;
    tagged.arity = 1;
    tagged.kinds[0] = SAGA_HV_REF;
    tagged.payload[0].ref = child;
    saga_gc_root_tagged(&tagged);
    child = NULL;
    saga_gc_collect();
    printf("%llu\n", (unsigned long long)saga_gc_live_objects());
    tagged.payload[0].ref = NULL;
    saga_gc_collect();
    printf("%llu\n", (unsigned long long)saga_gc_live_objects());
    saga_gc_unwind_roots(mark);
    saga_gc_shutdown();
    return 0;
}
''')
            binary = root / "tagged_gc"
            subprocess.run([cc, "-std=c11", "-I", str(header.parent), str(harness), str(obj), "-o", str(binary)], check=True)
            self.assertEqual(self.run_binary(binary), ["1", "0"])

    def test_payload_tagged_union_native_abi_change_invalidates_importer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = self.write(root, "models.saga", r'''
module models
public enum Outcome { Ok(int), Err(text) }
public fn make(value: int) -> Outcome = Outcome.Ok(value)
''')
            main = self.write(root, "main.saga", r'''
use "models.saga" as m
match m.make(4) {
    case m.Outcome.Ok(value) { print(value) }
    case m.Outcome.Err(message) { print(0) }
}
''')
            first = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertEqual(self.run_binary(first.output), ["4"])
            model.write_text('module models\npublic enum Outcome { Ok(text), Err(text) }\npublic fn make(value: int) -> Outcome = Outcome.Ok("changed")\n', encoding='utf-8')
            second = build_native_codegen(main, root / "app", build_dir=root / "build")
            self.assertIn("project/models.saga", second.compiled_objects)
            self.assertIn("project/main.saga", second.compiled_objects)
            self.assertEqual(self.run_binary(second.output), ["changed"])


    def test_c_collection_abi_fails_closed_before_invalid_dereference(self):
        cc = shutil.which("clang") or shutil.which("cc")
        if not cc:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = self.write(root, "main.saga", "print(1)\n")
            build_native_codegen(main, root / "app", build_dir=root / "build")
            header = next((root / "build" / "support").glob("*/saga_native_abi035.h"))
            obj = next((root / "build" / "support").glob("*/saga_native_abi035.o"))

            cases = {
                "null_map": ('''#include "saga_native_abi035.h"
int main(void) {
    SagaHeapValue key = {SAGA_HV_I64, {.i64 = 1}};
    SagaHeapValue value = {SAGA_HV_I64, {.i64 = 2}};
    saga_map_put(NULL, key, value);
    return 0;
}
''', 81, "SAGA-R183: expected native map"),
                "wrong_map_key": ('''#include "saga_native_abi035.h"
int main(void) {
    SagaRef map = saga_map_new(SAGA_HV_I64, SAGA_HV_I64, 0);
    SagaHeapValue key = {SAGA_HV_BOOL, {.boolean = 1}};
    (void)saga_map_contains(map, key);
    return 0;
}
''', 84, "SAGA-R184: native map type mismatch"),
                "null_list": ('''#include "saga_native_abi035.h"
int main(void) {
    SagaHeapValue value = {SAGA_HV_I64, {.i64 = 2}};
    (void)saga_list_set_at(NULL, 0, value);
    return 0;
}
''', 81, "SAGA-R181: expected native list"),
                "mixed_set_intersection": ('''#include "saga_native_abi035.h"
int main(void) {
    SagaRef left = saga_set_new(SAGA_HV_I64, 0);
    SagaRef right = saga_set_new(SAGA_HV_BOOL, 0);
    (void)saga_set_intersection(left, right);
    return 0;
}
''', 86, "SAGA-R186: native set type mismatch"),
            }
            for name, (source, expected_rc, expected_stderr) in cases.items():
                with self.subTest(name=name):
                    harness = self.write(root, f"{name}.c", source)
                    binary = root / name
                    subprocess.run([cc, "-std=c11", "-I", str(header.parent), str(harness), str(obj), "-o", str(binary)], check=True)
                    proc = subprocess.run([str(binary)], text=True, capture_output=True)
                    self.assertEqual(proc.returncode, expected_rc)
                    self.assertEqual(proc.stderr.strip(), expected_stderr)



if __name__ == "__main__":
    unittest.main()