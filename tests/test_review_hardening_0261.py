from __future__ import annotations
import base64, hashlib, json, os, stat, subprocess, sys, tempfile, threading, unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]

class ReviewHardening0261Tests(unittest.TestCase):
    def test_registry_plain_http_is_loopback_only(self):
        from saga.registry import _validate_registry_url
        self.assertEqual(_validate_registry_url('http://127.0.0.1:7331'),'http://127.0.0.1:7331')
        self.assertEqual(_validate_registry_url('http://localhost:7331'),'http://localhost:7331')
        with self.assertRaisesRegex(ValueError,'HTTPS'):
            _validate_registry_url('http://example.com')
        with self.assertRaises(ValueError):
            _validate_registry_url('https://user@example.com')
        with self.assertRaises(ValueError):
            _validate_registry_url('https://example.com/?token=secret')


    def test_registry_client_does_not_follow_redirects(self):
        from saga.registry import search
        hits={'redirected':0}
        class Final(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                hits['redirected']+=1; self.send_response(200); self.send_header('Content-Type','application/json'); body=b'{"packages":[]}' ; self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        final=ThreadingHTTPServer(('127.0.0.1',0),Final); threading.Thread(target=final.serve_forever,daemon=True).start()
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                self.send_response(302); self.send_header('Location',f'http://127.0.0.1:{final.server_address[1]}/v1/search'); self.end_headers()
        redir=ThreadingHTTPServer(('127.0.0.1',0),Redirect); threading.Thread(target=redir.serve_forever,daemon=True).start()
        try:
            with self.assertRaises(Exception): search(f'http://127.0.0.1:{redir.server_address[1]}','x')
            self.assertEqual(hits['redirected'],0)
        finally:
            redir.shutdown(); redir.server_close(); final.shutdown(); final.server_close()

    @unittest.skipIf(os.name=='nt','POSIX permission bits are not authoritative on Windows')
    def test_registry_private_key_is_mode_0600(self):
        from saga.registry import keygen
        with tempfile.TemporaryDirectory() as td:
            priv,pub=keygen(Path(td)/'publisher-private.pem',Path(td)/'publisher-public.pem')
            self.assertEqual(stat.S_IMODE(priv.stat().st_mode),0o600)
            self.assertTrue(pub.is_file())

    def test_ga_bound_pass_rejects_forged_pass_json(self):
        from tools.ga_readiness import bound_pass
        ctx={'manifest_sha256':'a'*64,'tree_sha256':'b'*64}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'fake.json'; p.write_text(json.dumps({'release':'0.50.0','pass':True,'source_manifest_sha256':'c'*64,'source_tree_sha256':'b'*64}))
            ok,reason=bound_pass(p,ctx)
            self.assertFalse(ok); self.assertIn('manifest',reason)

    def test_external_audit_verifier_rejects_current_tree_mismatch(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest('cryptography unavailable')
        manifest=ROOT/'release/source-manifest-0.26.2.json'
        if not manifest.is_file(): self.skipTest('release source manifest not generated yet')
        from tools.verify_external_security_attestation import canonical
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); report=td/'audit.md'; report.write_text('independent audit report\n')
            payload={'schema':1,'target_release':'0.26.2','source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'auditor':{'organization':'Independent Lab','reviewer':'Reviewer'},'completed_at_utc':'2026-08-11T00:00:00Z','scope':['compiler','runtime','package-manager','registry','capability-sandbox','crypto-tls','native-host-boundaries'],'methods':['source-review','dynamic-testing'],'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),'critical_open':0,'high_open':0,'medium_open':0,'low_open':0,'independent':True,'decision':'PASS'}
            key=Ed25519PrivateKey.generate(); pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()
            att=td/'att.json'; pk=td/'pub.hex'; out=td/'out.json'; pk.write_text(pub)
            sig=key.sign(canonical(payload)); att.write_text(json.dumps({'payload':payload,'signature_ed25519_base64':base64.b64encode(sig).decode()}))
            probe=ROOT/'review-tree-tamper-probe.tmp'
            try:
                probe.write_text('tamper')
                p=subprocess.run([sys.executable,str(ROOT/'tools/verify_external_security_attestation.py'),str(att),str(pk),'--report',str(report),'--source-manifest',str(manifest),'--output',str(out)],cwd=ROOT,text=True,capture_output=True,timeout=30)
                self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertIn('does not match',p.stdout+p.stderr)
            finally:
                probe.unlink(missing_ok=True)

    def test_generated_final_spec_does_not_break_frozen_source_manifest(self):
        from tools.review_evidence import build_manifest
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'source.txt').write_text('stable\n',encoding='utf-8')
            before=build_manifest(root)
            (root/'SAGA_LANGUAGE_SPECIFICATION_1.0.md').write_text('independently approved generated final\n',encoding='utf-8')
            after=build_manifest(root)
            self.assertEqual(before['tree_sha256'],after['tree_sha256'])
            self.assertEqual(before['files'],after['files'])

    def test_public_release_uses_review_candidate_not_draft_spec(self):
        text=(ROOT/'tools/package_public_release.py').read_text(encoding='utf-8')
        self.assertIn('SAGA_LANGUAGE_SPECIFICATION_1.0_FINAL_CANDIDATE.md',text)
        self.assertIn('SAGA_NATIVE_DISTRIBUTION_PROFILE_1.0_FINAL_CANDIDATE.md',text)
        self.assertIn('SAGA_SELF_HOSTING_PROFILE_1.0_FINAL_CANDIDATE.md',text)
        self.assertNotIn("OUT/'SAGA_LANGUAGE_SPECIFICATION_1.0_DRAFT.md'",text)

    def test_current_api_compatibility_manifests_exist(self):
        for name in ('api','app-action-api','native-game-api','security-api','web-host-api'):
            p=ROOT/'compatibility'/f'{name}-0.26.2.json'
            self.assertTrue(p.is_file(),p)
            doc=json.loads(p.read_text(encoding='utf-8'))
            self.assertIn('0.26.2',json.dumps(doc))

    def test_sh3_current_corpus_is_source_bound(self):
        from tools.review_evidence import build_manifest
        paths={r['path'] for r in build_manifest(ROOT)['files']}
        self.assertIn('conformance/sh3/standard-core-cases-1.0.json',paths)
        self.assertIn('conformance/sh3/edition-2027-cases.json',paths)
        tool=(ROOT/'tools/sh3_validate.py').read_text(encoding='utf-8')
        self.assertIn('conformance/sh3/standard-core-cases-1.0.json',tool)
        self.assertIn('conformance/sh3/edition-2027-cases.json',tool)

    def test_registry_live_qualification_names_are_rerunnable(self):
        from tools.registry_live_qualification import qualification_names
        a=qualification_names('saga-qualification','rOne')
        b=qualification_names('saga-qualification','rTwo')
        self.assertNotEqual(a[0],b[0]); self.assertNotEqual(a[1],b[1])
        self.assertTrue(a[0].startswith('saga-qualification-python-'))
        with self.assertRaises(ValueError): qualification_names('saga-qualification','2026-bad')

    def test_python_registry_does_not_persist_plaintext_bearer_token(self):
        from saga.registry import init_registry, serve_registry
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'registry'; init_registry(root,'super-secret-token')
            raw=(root/'registry.json').read_text(encoding='utf-8')
            self.assertNotIn('super-secret-token',raw)
            cfg=json.loads(raw); self.assertEqual(cfg['schema'],2); self.assertEqual(len(cfg['token_sha256']),64)
            if os.name!='nt': self.assertEqual(stat.S_IMODE((root/'registry.json').stat().st_mode),0o600)
            server=serve_registry(root,'127.0.0.1',0,token=None)
            server.server_close()

    def test_python_registry_migrates_legacy_plaintext_token_config(self):
        from saga.registry import serve_registry
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'registry'; (root/'packages').mkdir(parents=True)
            (root/'registry.json').write_text(json.dumps({'schema':1,'token':'legacy-secret','require_signatures':True}),encoding='utf-8')
            server=serve_registry(root,'127.0.0.1',0,token=None); server.server_close()
            raw=(root/'registry.json').read_text(encoding='utf-8')
            self.assertNotIn('legacy-secret',raw); self.assertNotIn('"token"',raw); self.assertIn('token_sha256',raw)

    def test_package_lock_rejects_duplicate_json_keys(self):
        from saga.package import build_lock, verify_lock
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'saga.toml').write_text('[project]\nname="dup-lock"\nversion="1.0.0"\nlanguage="1.0"\nentry="main.saga"\ntest_dir="tests"\n')
            (root/'main.saga').write_text('print(1)\n'); build_lock(root)
            p=root/'saga.lock'; text=p.read_text(encoding='utf-8')
            p.write_text(text.replace('{','{"schema": 1,',1),encoding='utf-8')
            ok,errors=verify_lock(root)
            self.assertFalse(ok); self.assertTrue(any('duplicate JSON key' in e for e in errors),errors)

    def test_registry_keygen_refuses_existing_paths(self):
        from saga.registry import keygen
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); priv=td/'private.pem'; pub=td/'public.pem'; priv.write_text('keep')
            with self.assertRaises(FileExistsError): keygen(priv,pub)
            self.assertEqual(priv.read_text(),'keep'); self.assertFalse(pub.exists())

if __name__=='__main__': unittest.main()
