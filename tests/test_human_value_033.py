from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from saga.api import compile_file, compile_source, run_source
from saga.errors import SourceError
from saga.module_interface import build_module_interface, load_module_interface
from saga.native_codegen import build_native_codegen, native_function_symbol


class HumanCentered033Tests(unittest.TestCase):
    def test_enum_match_executes(self):
        src = '''
enum State { Ready, Running, Done }
let state: State = State.Running
match state {
  case State.Ready { print("ready") }
  case State.Running { print("running") }
  case State.Done { print("done") }
}
'''
        out: list[str] = []
        run_source(src, '<enum-match>', output=out.append)
        self.assertEqual(out, ['running'])

    def test_enum_match_must_be_exhaustive(self):
        src = '''
enum State { Ready, Running, Done }
match State.Ready {
  case State.Ready { print(1) }
  case State.Done { print(3) }
}
'''
        with self.assertRaises(SourceError) as ctx:
            compile_source(src, '<enum-exhaustive>')
        self.assertEqual(ctx.exception.diagnostic_id, 'SAGA-T112')

    def test_unless_is_natural_surface_sugar(self):
        out: list[str] = []
        run_source('let ready=false\nunless ready { print "not ready" }', '<unless>', output=out.append)
        self.assertEqual(out, ['not ready'])

    def test_public_enum_keeps_qualified_nominal_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'models.saga').write_text('''
module models
public enum Status { Ready, Done }
public fn status() -> Status = Status.Ready
'''.strip()+'\n', encoding='utf-8')
            main = root/'main.saga'
            main.write_text('''
use "models.saga" as m
let state: m.Status = m.status()
match state {
  case m.Status.Ready { print("ready") }
  case m.Status.Done { print("done") }
}
'''.strip()+'\n', encoding='utf-8')
            compile_file(main)
            proc = subprocess.run(['python3','-m','saga','run',str(main)], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout+proc.stderr)
            self.assertEqual(proc.stdout.strip(), 'ready')

    def test_smi_exports_enum_and_is_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); src=root/'models.saga'
            src.write_text('module models\npublic enum Status { Ready, Done }\npublic fn status()->Status=Status.Ready\n', encoding='utf-8')
            iface=build_module_interface(src, root=root)
            self.assertEqual(iface['language_version'], '0.35')
            enum=next(x for x in iface['exports'] if x['kind']=='enum')
            self.assertEqual(enum, {'kind':'enum','name':'Status','type_params':[],'variants':[{'name':'Ready','payload':[]},{'name':'Done','payload':[]}]})
            loaded=load_module_interface(src.with_suffix('.smi.json'), source=src)
            self.assertEqual(loaded['abi_sha256'], iface['abi_sha256'])


@unittest.skipUnless(shutil.which('clang') or shutil.which('cc') or shutil.which('gcc'), 'C toolchain required')
class NativeValueABI033Tests(unittest.TestCase):
    def _run(self, exe: Path) -> str:
        p=subprocess.run([str(exe)], text=True, capture_output=True, timeout=20)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        return p.stdout.strip()

    def test_native_text_option_result_and_propagation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); main=root/'main.saga'
            main.write_text('''
fn greet(name:text)->text=name
fn maybe(okay:bool)->option[int]{ if okay { return some(12) } return none() }
fn source(okay:bool)->result[int,text]{ if okay { return ok(4) } return err("bad") }
fn consume(okay:bool)->result[int,text]{ value=source(okay)? return ok(value+1) }
print(greet("こんにちは"))
print(unwrap_or(maybe(true),0))
print(unwrap_or(maybe(false),9))
print(unwrap_result_or(consume(true),0))
print(unwrap_result_or(consume(false),9))
'''.strip()+'\n', encoding='utf-8')
            result=build_native_codegen(main, root/'app', build_dir=root/'build')
            self.assertEqual(self._run(result.output), 'こんにちは\n12\n9\n5\n9')
            reports=[json.loads(p.read_text()) for p in (result.build_dir/'abi').glob('*.nabi.json')]
            self.assertTrue(all(x['abi_version']=='0.35' for x in reports))

    def test_cross_module_native_text_and_option(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'models.saga').write_text('''
module models
public fn greet(name:text)->text=name
public fn maybe(okay:bool)->option[int]{ if okay { return some(12) } return none() }
'''.strip()+'\n',encoding='utf-8')
            main=root/'main.saga'; main.write_text('''
use "models.saga" as m
print(m.greet("Saga"))
print(unwrap_or(m.maybe(true),0))
'''.strip()+'\n',encoding='utf-8')
            result=build_native_codegen(main, root/'app', build_dir=root/'build')
            self.assertEqual(self._run(result.output),'Saga\n12')
            if shutil.which('nm'):
                model=next(p for p in result.objects if 'models.saga' in p.name)
                mainobj=next(p for p in result.objects if 'main.saga' in p.name)
                sym=native_function_symbol('models','greet')
                self.assertIn('T '+sym, subprocess.check_output(['nm',str(model)],text=True))
                self.assertIn('U '+sym, subprocess.check_output(['nm',str(mainobj)],text=True))

    def test_generated_c_header_can_call_text_and_option_abi(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'models.saga').write_text('''
module models
public fn echo(value:text)->text=value
public fn maybe(okay:bool)->option[int]{ if okay { return some(21) } return none() }
'''.strip()+'\n',encoding='utf-8')
            main=root/'main.saga'; main.write_text('use "models.saga" as m\nprint(m.echo("ok"))\n',encoding='utf-8')
            result=build_native_codegen(main,root/'app',build_dir=root/'build')
            model_obj=next(p for p in result.objects if 'models.saga' in p.name)
            header=next(p for p in (result.build_dir/'abi').glob('*.nabi.h') if 'models.saga' in p.name)
            support_dir=result.build_dir/'support'
            support_obj=next(support_dir.rglob('saga_native_abi035.o'))
            echo=native_function_symbol('models','echo'); maybe=native_function_symbol('models','maybe')
            h=root/'client.c'; h.write_text(f'''#include "{header.name}"
#include <stdio.h>
#include <string.h>
int main(void) {{
  const char *raw="hello";
  SagaText in={{(const uint8_t*)raw,5}};
  SagaText out={echo}(in);
  SagaOption opt={maybe}(1);
  fwrite(out.data,1,(size_t)out.len,stdout);
  printf(" %lld\\n",(long long)opt.value.i64);
  return 0;
}}
''',encoding='utf-8')
            cc=shutil.which('clang') or shutil.which('cc') or shutil.which('gcc')
            exe=root/'client'
            p=subprocess.run([cc,str(h),str(model_obj),str(support_obj),'-I',str(header.parent),'-I',str(support_obj.parent),'-o',str(exe)],text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            self.assertEqual(self._run(exe),'hello 21')

    def test_native_enum_preserves_human_readable_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); main=root/'main.saga'
            main.write_text('enum State { Ready, Done }\nfn state()->State=State.Ready\nprint(state())\n',encoding='utf-8')
            result=build_native_codegen(main,root/'app',build_dir=root/'build')
            self.assertEqual(self._run(result.output),'State.Ready')


if __name__=='__main__':
    unittest.main()
