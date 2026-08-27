from __future__ import annotations
import os, platform, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

from saga.plugin_runtime import PluginSandboxError, call_plugin, load_plugin
from saga.sandbox import support
from saga.api import run_source

ROOT=Path(__file__).resolve().parents[1]

class SagaSecurity010Tests(unittest.TestCase):
    def test_plugin_is_separate_and_blocks_file_and_import_builtins(self):
        if platform.system().lower() != 'linux' or not shutil.which('unshare'):
            self.skipTest('strict namespace sandbox requires Linux unshare')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'plugin.py'
            p.write_text("""
def add(a,b): return a+b
saga_exports={"add":add}
""",encoding='utf-8')
            h=load_plugin(p)
            self.assertEqual(call_plugin(h,'add',[2,5]),7)
            self.assertEqual(h.sandbox_mode,'linux-namespaces')
            bad=Path(td)/'bad.py'
            bad.write_text("""
def read_secret(): return open("/etc/passwd").read()
def import_os(): return __import__("os").listdir("/")
saga_exports={"read_secret":read_secret,"import_os":import_os}
""",encoding='utf-8')
            with self.assertRaises(PluginSandboxError): load_plugin(bad)

    def test_safe_annotation_processor_cannot_write_directly(self):
        if platform.system().lower() != 'linux' or not shutil.which('unshare'):
            self.skipTest('strict namespace sandbox requires Linux unshare')
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); source=td/'main.saga'; source.write_text('@entity\nclass User(let id: int) {}',encoding='utf-8')
            proc=td/'processor.py'; proc.write_text('''\ndef process(metadata):\n    return {"generated.txt": "ok"}\n''',encoding='utf-8')
            out=td/'out'
            cp=subprocess.run([sys.executable,str(ROOT/'saga.py'),'process',str(source),'--processor',str(proc),'--output',str(out)],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(cp.returncode,0,cp.stderr); self.assertEqual((out/'generated.txt').read_text(), 'ok')

    def test_diagnostic_identity_does_not_depend_on_japanese_message(self):
        from saga.errors import TypeCheckError
        a=TypeCheckError('日本語の文言A',1,1,'x.saga',detail_code='SAGA-T103')
        b=TypeCheckError('completely different wording',1,1,'x.saga',detail_code='SAGA-T103')
        self.assertEqual(a.diagnostic_id,'SAGA-T103'); self.assertEqual(b.diagnostic_id,'SAGA-T103')
        c=TypeCheckError('let なので変更できません',1,1,'x.saga')
        self.assertEqual(c.diagnostic_id,'SAGA-T001')

    def test_plugin_safe_facades_do_not_expose_sys_or_os_modules(self):
        if platform.system().lower() != 'linux' or not shutil.which('unshare'):
            self.skipTest('strict namespace sandbox requires Linux unshare')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'facade.py'
            p.write_text("""
def probe(): return statistics.sys.modules["os"].listdir("/")
saga_exports={"probe":probe}
""",encoding='utf-8')
            h=load_plugin(p)
            with self.assertRaises(PluginSandboxError): call_plugin(h,'probe',[])

    def test_plugin_blocks_dunder_introspection_escape(self):
        if platform.system().lower() != 'linux' or not shutil.which('unshare'):
            self.skipTest('strict namespace sandbox requires Linux unshare')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'escape.py'
            p.write_text("""
def escape(): return ().__class__.__base__.__subclasses__()
saga_exports={"escape":escape}
""",encoding='utf-8')
            with self.assertRaises(PluginSandboxError): load_plugin(p)

    def test_strict_os_sandbox_blocks_host_network_even_when_capability_is_granted(self):
        if platform.system().lower() != 'linux' or not shutil.which('unshare'):
            self.skipTest('strict namespace sandbox requires Linux unshare')
        import http.server, socketserver, threading
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
            def log_message(self, *args): pass
        server=socketserver.TCPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            port=server.server_address[1]
            with tempfile.TemporaryDirectory() as td:
                src=Path(td)/'net.saga'
                src.write_text(f'use http\nlet r = http.get("http://127.0.0.1:{port}/")\nprint(http.status(r))\n',encoding='utf-8')
                base=[sys.executable,str(ROOT/'saga.py'),'run',str(src),'--allow-net',f'127.0.0.1:{port}']
                normal=subprocess.run(base,cwd=ROOT,text=True,capture_output=True)
                strict=subprocess.run(base+['--os-sandbox','strict'],cwd=ROOT,text=True,capture_output=True)
                self.assertEqual(normal.returncode,0,normal.stderr); self.assertIn('200',normal.stdout)
                self.assertNotEqual(strict.returncode,0); self.assertNotIn('200',strict.stdout)
        finally:
            server.shutdown(); server.server_close()

    def test_verify_cli_has_single_optional_path(self):
        cp=subprocess.run([sys.executable,str(ROOT/'saga.py'),'verify','--help'],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(cp.returncode,0,cp.stderr)
        self.assertEqual(cp.stdout.count('[path]'),1)

    def test_strict_sandbox_fails_closed_when_strong_os_boundary_is_unavailable(self):
        from unittest import mock
        from saga.sandbox import command_for_python
        with mock.patch('saga.sandbox.platform.system',return_value='Windows'):
            with self.assertRaises(RuntimeError): command_for_python(Path('worker.py'),strict=True)

    def test_strict_cli_sandbox_uses_mount_namespace_and_hardening_preexec(self):
        from unittest import mock
        from saga import sandbox
        completed = subprocess.CompletedProcess([], 0)
        with (
            mock.patch('saga.sandbox.platform.system', return_value='Linux'),
            mock.patch('saga.sandbox.shutil.which', return_value='/usr/bin/unshare'),
            mock.patch.dict(os.environ, {'SAGA_OS_SANDBOX_ACTIVE': '0'}),
            mock.patch('saga.sandbox.subprocess.run', return_value=completed) as run,
        ):
            self.assertEqual(sandbox.run_cli_in_strict_sandbox(['run', 'main.saga']), 0)
        cmd = run.call_args.args[0]
        self.assertIn('--mount', cmd)
        self.assertIs(run.call_args.kwargs['preexec_fn'], sandbox._strict_cli_preexec)
        self.assertTrue(run.call_args.kwargs['close_fds'])

    def test_untrusted_process_budget_applies_cpu_and_address_space_limits(self):
        from types import SimpleNamespace
        from unittest import mock
        from saga import ProcessBudget
        from saga import sandbox
        fake_resource = SimpleNamespace(
            RLIMIT_CORE=1, RLIMIT_NOFILE=2, RLIMIT_CPU=3, RLIMIT_AS=4,
            setrlimit=mock.Mock(),
        )
        with mock.patch("saga.sandbox._resource", fake_resource):
            sandbox._resource_limits(ProcessBudget(
                max_cpu_seconds=7,
                max_address_space_bytes=256 * 1024 * 1024,
            ))
        fake_resource.setrlimit.assert_any_call(3, (7, 7))
        fake_resource.setrlimit.assert_any_call(4, (256 * 1024 * 1024, 256 * 1024 * 1024))

    def test_untrusted_process_budget_fails_closed_when_required_limit_is_missing(self):
        from types import SimpleNamespace
        from unittest import mock
        from saga import ProcessBudget
        from saga import sandbox
        fake_resource = SimpleNamespace(
            RLIMIT_CORE=1, RLIMIT_NOFILE=2, RLIMIT_CPU=3,
            setrlimit=mock.Mock(),
        )
        with mock.patch("saga.sandbox._resource", fake_resource):
            with self.assertRaisesRegex(RuntimeError, "RLIMIT_AS"):
                sandbox._resource_limits(ProcessBudget(
                    max_cpu_seconds=7,
                    max_address_space_bytes=128 * 1024 * 1024,
                ))

    def test_strict_untrusted_profile_runs_inside_process_budget(self):
        if platform.system().lower() != "linux" or not shutil.which("unshare"):
            self.skipTest("strict namespace sandbox requires Linux unshare")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "simple.saga"
            src.write_text('print("budget-ok")\n', encoding="utf-8")
            cp = subprocess.run([
                sys.executable, str(ROOT / "saga.py"), "run", str(src),
                "--resource-profile", "untrusted", "--os-sandbox", "strict",
            ], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn("budget-ok", cp.stdout)

    def test_strict_cli_preexec_requires_no_new_privs(self):
        from unittest import mock
        from saga import sandbox
        with (
            mock.patch('saga.sandbox._set_no_new_privs') as no_new_privs,
            mock.patch('saga.sandbox._resource_limits') as resource_limits,
        ):
            sandbox._strict_cli_preexec()
        no_new_privs.assert_called_once_with()
        resource_limits.assert_called_once_with()

    def test_no_new_privs_failure_is_not_silently_ignored(self):
        from unittest import mock
        from saga import sandbox
        libc = mock.Mock()
        libc.prctl.return_value = -1
        with (
            mock.patch('saga.sandbox.platform.system', return_value='Linux'),
            mock.patch('saga.sandbox.ctypes.CDLL', return_value=libc),
            mock.patch('saga.sandbox.ctypes.get_errno', return_value=1),
        ):
            with self.assertRaisesRegex(RuntimeError, 'PR_SET_NO_NEW_PRIVS'):
                sandbox._set_no_new_privs()

    def test_regex_hosted_module(self):
        out=[]
        run_source('''use regex\nprint(regex.is_match("[A-Z]+", "abcXYZ"))\nprint(regex.replace("[0-9]+", "a12b", "#"))''',output=out.append)
        self.assertEqual(out,['true','a#b'])

if __name__=='__main__':unittest.main()
