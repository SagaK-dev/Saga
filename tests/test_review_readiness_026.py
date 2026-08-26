from __future__ import annotations
import base64, hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]

class ReviewReadiness026Tests(unittest.TestCase):
    def test_spec_candidate_lint(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'spec.json'
            p=subprocess.run([sys.executable,str(ROOT/'tools/spec_review_lint.py'),'--output',str(out)],cwd=ROOT,text=True,capture_output=True,timeout=30)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr); self.assertTrue(json.loads(out.read_text())['pass'])

    def test_registry_deployment_uses_canonical_go_protocol(self):
        docker=(ROOT/'deploy/registry/Dockerfile').read_text()
        self.assertIn('go build',docker); self.assertIn('"registry","serve"',docker); self.assertIn('"--addr","0.0.0.0:7331"',docker)
        self.assertNotIn('COPY saga /usr/local/bin/saga',docker)
        src=(ROOT/'implementations/go/cmd/saga-go/registry_native.go').read_text()
        self.assertIn('legacy registry endpoint removed',src); self.assertIn('/v1/packages/',src); self.assertIn('/v1/search',src)

    def test_live_registry_qualification_refuses_localhost(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'live.json'; key=Path(td)/'dummy.pem'; key.write_text('not used')
            env=os.environ.copy(); env.update({'SAGA_REGISTRY_LIVE':'1','SAGA_REGISTRY_URL':'https://localhost:7443','SAGA_REGISTRY_TOKEN':'x','SAGA_REGISTRY_SIGNING_KEY':str(key)})
            p=subprocess.run([sys.executable,str(ROOT/'tools/registry_live_qualification.py'),'--output',str(out)],cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
            self.assertNotEqual(p.returncode,0); self.assertFalse(json.loads(out.read_text())['pass'])

    def test_spec_review_attestation_exact_bytes_contract(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from tools.verify_spec_review_attestation import RELEASE, canonical, proposed_final_bytes
        candidate=ROOT/'SAGA_LANGUAGE_SPECIFICATION_1.0_FINAL_CANDIDATE.md'; grammar=ROOT/'spec/saga-1.0.ebnf'
        payload={'schema':1,'target_release':RELEASE,'reviewer':{'name':'Reviewer','organization':'Independent Lab'},'completed_at_utc':'2026-08-10T10:00:00Z','candidate_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),'proposed_final_sha256':hashlib.sha256(proposed_final_bytes()).hexdigest(),'grammar_sha256':hashlib.sha256(grammar.read_bytes()).hexdigest(),'decision':'APPROVE','independent':True,'unresolved_normative_issues':0}
        key=Ed25519PrivateKey.generate(); pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); att=td/'att.json'; pk=td/'pub.hex'; out=td/'out.json'; pk.write_text(pub)
            sig=key.sign(canonical(payload)); att.write_text(json.dumps({'payload':payload,'signature_ed25519_base64':base64.b64encode(sig).decode()}))
            cmd=[sys.executable,str(ROOT/'tools/verify_spec_review_attestation.py'),str(att),str(pk),'--output',str(out)]
            p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=30); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            payload['unresolved_normative_issues']=1; sig=key.sign(canonical(payload)); att.write_text(json.dumps({'payload':payload,'signature_ed25519_base64':base64.b64encode(sig).decode()}))
            p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=30); self.assertNotEqual(p.returncode,0)


    def test_live_registry_project_fixture_allows_precreated_directory(self):
        from tools.registry_live_qualification import write_project
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"consumer"; p.mkdir()
            write_project(p,"review-fixture")
            self.assertTrue((p/"saga.lock").is_file())

    def test_native_host_qualifier_refuses_wrong_host(self):
        wrong='windows' if sys.platform!='win32' else 'macos'
        p=subprocess.run([sys.executable,str(ROOT/'tools/native_host_qualification.py'),'--expected-host',wrong],cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(p.returncode,0)

if __name__=='__main__': unittest.main()
