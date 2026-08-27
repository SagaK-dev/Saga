from __future__ import annotations
import io, json, shutil, subprocess, sys, tempfile, threading, unittest, zipfile
from pathlib import Path
from unittest.mock import patch

from saga.api import run_source, run_file
from saga.aot import build, build_standard_bundle
from saga.registry import init_registry, serve_registry, publish, search, install, keygen
from saga.mobile import generate_ios, generate_android
from saga.capability_audit import audit

ROOT=Path(__file__).resolve().parents[1]

class SagaEcosystem011Tests(unittest.TestCase):
    def test_lexical_closure_captures_mutable_cell(self):
        src='''fn make(start: int) -> fn[int] { var n=start fn next() -> int { n=n+1 return n } return next }\nlet c=make(3)\nprint(c())\nprint(c())'''
        out=[]; run_source(src, output=out.append)
        self.assertEqual(out,['4','5'])

    def test_registry_publish_install_and_pkg_import(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); reg=td/'reg'; pkg=td/'pkg'; consumer=td/'consumer'; (pkg/'tests').mkdir(parents=True); consumer.mkdir()
            (pkg/'saga.toml').write_text('[project]\nname="math-tools"\nversion="1.0.0"\nlanguage="0.9"\nentry="lib.saga"\ntest_dir="tests"\n')
            (pkg/'lib.saga').write_text('fn twice(x:int)->int=x*2\n')
            init_registry(reg,'secret',require_signatures=True); server=serve_registry(reg,'127.0.0.1',0,'secret',require_signatures=True); port=server.server_address[1]
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); url=f'http://127.0.0.1:{port}'
            try:
                priv,pub=keygen(td/'math-private.pem',td/'math-public.pem')
                meta=publish(pkg,url,'secret',priv); self.assertEqual(meta['name'],'math-tools'); self.assertEqual(search(url,'math')[0]['version'],'1.0.0')
                install(url,'math-tools@1.0.0',consumer,trust_once=meta['publisher_fingerprint'])
                (consumer/'saga.toml').write_text('[project]\nname="consumer"\nversion="0.1.0"\nlanguage="0.9"\nentry="main.saga"\ntest_dir="tests"\n')
                (consumer/'main.saga').write_text('use "pkg:math-tools/lib.saga"\nprint(twice(21))\n')
                out=[]; run_file(str(consumer/'main.saga'),output=out.append); self.assertEqual(out,['42'])
            finally: server.shutdown(); server.server_close()

    def test_registry_install_rejects_excessive_archive_shape(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as z:
            for i in range(10001): z.writestr(f"files/{i:05d}.txt", b"")
        data=buf.getvalue()
        class Response:
            headers={"Content-Length":str(len(data))}
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self,n=-1): return data if n < 0 else data[:n]
        with tempfile.TemporaryDirectory() as td, patch("saga.registry.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ValueError, "too many files"):
                install("https://registry.invalid","safe-pkg@1.0.0",td,allow_unsigned=True)
            self.assertFalse((Path(td)/"vendor"/"safe-pkg"/"1.0.0").exists())
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError): install("https://registry.invalid","../evil@1.0.0",td)


    def test_registry_install_rejects_identity_mismatch_and_preserves_existing_target(self):
        from saga.package import build_lock, pack_project
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); pkg=td/'wrong'; pkg.mkdir()
            (pkg/'saga.toml').write_text('[project]\nname="wrong-name"\nversion="1.0.0"\nlanguage="0.9"\nentry="main.saga"\ntest_dir="tests"\n')
            (pkg/'main.saga').write_text('print(1)\n'); build_lock(pkg); data=pack_project(pkg).read_bytes()
            class Response:
                headers={"Content-Length":str(len(data)),"X-Saga-Sha256":__import__('hashlib').sha256(data).hexdigest()}
                def __enter__(self): return self
                def __exit__(self,*_): return False
                def read(self,n=-1): return data if n < 0 else data[:n]
            consumer=td/'consumer'; existing=consumer/'vendor'/'safe-pkg'/'1.0.0'; existing.mkdir(parents=True); (existing/'keep.txt').write_text('keep')
            with patch('saga.registry.urlopen', return_value=Response()):
                with self.assertRaisesRegex(ValueError, 'identity'):
                    install('https://registry.invalid','safe-pkg@1.0.0',consumer,allow_unsigned=True)
            self.assertEqual((existing/'keep.txt').read_text(),'keep')

    def test_registry_idempotent_retry_detects_corrupt_stored_package(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography unavailable")
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); reg=td/'reg'; pkg=td/'pkg'; pkg.mkdir()
            (pkg/'saga.toml').write_text('[project]\nname="retry-check"\nversion="1.0.0"\nlanguage="1.0"\nentry="main.saga"\ntest_dir="tests"\n')
            (pkg/'main.saga').write_text('print(1)\n')
            init_registry(reg,'secret',require_signatures=True)
            server=serve_registry(reg,'127.0.0.1',0,'secret',require_signatures=True); port=server.server_address[1]
            threading.Thread(target=server.serve_forever,daemon=True).start(); url=f'http://127.0.0.1:{port}'
            try:
                priv,_=keygen(td/'priv.pem',td/'pub.pem')
                publish(pkg,url,'secret',priv)
                stored=reg/'packages'/'retry-check'/'1.0.0'/'package.sagapkg'
                stored.write_bytes(b'corrupt')
                with self.assertRaises(HTTPError) as cm:
                    publish(pkg,url,'secret',priv)
                self.assertEqual(cm.exception.code,500)
            finally:
                server.shutdown(); server.server_close()

    def test_scalar_native_and_wasm_build(self):
        if not shutil.which('clang'): self.skipTest('clang unavailable')
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); src=td/'main.saga'; src.write_text('fn sq(x:int)->int=x*x\nprint(sq(7))\n')
            native=build(src,'native',td/'app').output
            self.assertEqual(subprocess.check_output([native],text=True).strip(),'49')
            wasm=build(src,'wasm',td/'app.wasm').output; self.assertTrue(wasm.read_bytes().startswith(b'\x00asm'))

    def test_standard_native_bundle_supports_closure(self):
        if not shutil.which('go'): self.skipTest('go unavailable')
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); src=td/'main.saga'; src.write_text('fn make(x:int)->fn[int]{ var n=x fn next()->int {n=n+1 return n} return next }\nlet c=make(8)\nprint(c())\n')
            out=build_standard_bundle(src,'native',td/'app').output
            self.assertEqual(subprocess.check_output([out],text=True).strip(),'9')

    def test_standard_native_feature_detector_respects_lexical_capture(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); src=td/'main.saga'; src.write_text('fn make(x:int)->fn[int]{ var n=x fn next()->int {n=n+1 return n} return next }\n')
            from saga.api import compile_file
            from saga.aot import _natural_029_features
            loaded = compile_file(str(src))
            self.assertNotIn('first-assignment binding', _natural_029_features(loaded.program))

    def test_registry_ed25519_signature_and_capability_metadata(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography unavailable")
        import threading
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); reg=td/'registry'; init_registry(reg,'secret')
            server=serve_registry(reg,'127.0.0.1',0,'secret'); port=server.server_address[1]
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            project=td/'pkg'; project.mkdir(); (project/'saga.toml').write_text('[project]\nname="network-pkg"\nversion="1.0.0"\nlanguage="0.9"\nentry="main.saga"\ntest_dir="tests"\n')
            (project/'main.saga').write_text('use http\nprint(1)\n')
            priv,pub=keygen(td/'private.pem',td/'public.pem')
            meta=publish(project,f'http://127.0.0.1:{port}','secret',priv)
            self.assertTrue(meta.get('publisher_fingerprint')); self.assertEqual(meta.get('capabilities'),['network'])
            consumer=td/'consumer'; consumer.mkdir(); install(f'http://127.0.0.1:{port}','network-pkg@1.0.0',consumer,trust_once=meta['publisher_fingerprint'])
            lock=json.loads((consumer/'saga.dependencies.json').read_text()); self.assertTrue(lock['packages']['network-pkg'].get('publisher_fingerprint'))
            server.shutdown(); server.server_close()

    def test_allowlisted_python_ecosystem_bridge(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy unavailable")
        from saga.plugin_runtime import load_plugin, call_plugin
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); plugin=td/'plugin.py'
            plugin.write_text('def average(xs):\n    return numpy.mean(xs)\nsaga_exports={"average":average}\n')
            plugin.with_suffix('.saga-plugin.json').write_text('{"imports":{"numpy":["mean"]}}\n')
            handle=load_plugin(plugin, trusted_imports={"numpy": ("mean",)})
            self.assertEqual(str(call_plugin(handle,'average',[(1,2,3,4)])),'2.5')

    def test_mobile_generators_have_native_runtime_sources(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); src=td/'main.saga'; src.write_text('print(7)\n')
            ios=generate_ios(src,td/'ios'); android=generate_android(src,td/'android','dev.saga.test')
            self.assertTrue((ios/'Package.swift').is_file()); self.assertTrue((ios/'Sources/SagaRuntime/saga_program.c').is_file())
            self.assertTrue((ios/'StandardCoreRuntime/mobile.go').is_file()); self.assertTrue((ios/'build-standard-runtime.sh').is_file())
            self.assertTrue((android/'app/src/main/cpp/saga_program.c').is_file()); self.assertTrue((android/'app/src/main/AndroidManifest.xml').is_file())
            self.assertTrue((android/'StandardCoreRuntime/mobile.go').is_file()); self.assertTrue((android/'build-standard-runtime.sh').is_file())
            root_gradle=(android/'build.gradle.kts').read_text()
            app_gradle=(android/'app/build.gradle.kts').read_text()
            self.assertIn('version "9.3.0"', root_gradle)
            self.assertIn('compileSdk = 37', app_gradle); self.assertIn('targetSdk = 36', app_gradle)
            self.assertIn('ndkVersion = "28.2.13676358"', app_gradle)
            self.assertEqual(app_gradle.count('{'), app_gradle.count('}'))

    def test_standard_core_mobile_runtime_builds_and_executes(self):
        if not shutil.which('go'): self.skipTest('go unavailable')
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); src=ROOT/'examples/learning/01_foundation.saga'
            ios=generate_ios(src,td/'ios'); android=generate_android(src,td/'android','dev.saga.test')
            for runtime in (ios/'StandardCoreRuntime', android/'StandardCoreRuntime'):
                (runtime/'mobile_runtime_test.go').write_text('package sagaruntime\nimport "testing"\nfunc TestEmbeddedProgram(t *testing.T){got,err:=Run();if err!=nil{t.Fatal(err)};if got!="Saga score: 60"{t.Fatalf("got %q",got)}}\n')
                subprocess.run(['go','test','./...','-count=1'],cwd=runtime,check=True,capture_output=True,text=True)
                subprocess.run(['go','vet','./...'],cwd=runtime,check=True,capture_output=True,text=True)

    def test_python_module_entrypoint_matches_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "saga", "--version"],
            cwd=ROOT, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        from saga import __version__
        self.assertIn(__version__, proc.stdout)

    def test_python_debug_command_trace_and_breakpoint(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/"debug.saga"
            src.write_text("var x = 1\nx = x + 1\nprint(x)\n", encoding="utf-8")
            trace=subprocess.run([sys.executable,"-m","saga","debug",str(src),"--trace"],cwd=ROOT,text=True,capture_output=True,timeout=30)
            self.assertEqual(trace.returncode,0,trace.stdout+trace.stderr)
            self.assertEqual(trace.stdout.strip(),"2")
            self.assertIn("[trace]",trace.stderr); self.assertIn("locals=",trace.stderr)
            brk=subprocess.run([sys.executable,"-m","saga","debug",str(src),"--break","2"],cwd=ROOT,text=True,capture_output=True,timeout=30)
            self.assertEqual(brk.returncode,0,brk.stdout+brk.stderr)
            self.assertIn("[break]",brk.stderr); self.assertIn("x=1",brk.stderr)

    def test_capability_audit_is_deny_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'a.saga'; p.write_text('use http\nuse db\nprint(1)\n')
            r=audit(p); self.assertEqual(r['policy'],'deny-by-default'); self.assertEqual(r['capabilities'],['database','network'])

if __name__=='__main__': unittest.main()
