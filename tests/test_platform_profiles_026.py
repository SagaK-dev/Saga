from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest import mock

from saga.api import compile_source
from saga.interpreter import Interpreter
from saga.native import Capabilities
from saga.stdlib import MODULES


class PlatformProfiles026Tests(unittest.TestCase):
    def setUp(self):
        self.interpreter = Interpreter("<platform-025>", output=lambda _x: None, capabilities=Capabilities(allow_ui=True, allow_cloud=True, allow_device=True, allow_process=True))

    def tearDown(self):
        self.interpreter.close()

    def call(self, module, name, *args):
        return MODULES[module].get(name)(self.interpreter, list(args))

    def test_new_profiles_typecheck(self):
        compile_source('''
use game
use gpio
use spark
let g = game.run_frames("Saga", 64, 48, 2)
let input = gpio.input(17, true)
let pwm = gpio.pwm(18, 100.0, 0.25)
gpio.write(pwm, 0.75)
let value: decimal = gpio.read(input)
let s = spark.local_session("Saga", 2)
let count: int = spark.range_count(s, 0, 10)
let rows = spark.sql(s, "SELECT 42 AS answer")
spark.stop(s)
''')

    def test_gpio_extended_adapter_contract(self):
        gpio = ModuleType("gpiozero")
        class Device:
            def close(self): self.closed=True
        class OutputDevice(Device):
            def __init__(self,pin): self.pin=pin; self.value=0.0; self.closed=False
            def on(self): self.value=1.0
            def off(self): self.value=0.0
        class DigitalInputDevice(Device):
            def __init__(self,pin,pull_up=False): self.pin=pin; self.value=1.0 if pull_up else 0.0; self.closed=False
        class PWMOutputDevice(OutputDevice):
            def __init__(self,pin,frequency=100.0,initial_value=0.0): super().__init__(pin); self.frequency=frequency; self.value=initial_value
        gpio.Device=Device; gpio.OutputDevice=OutputDevice; gpio.DigitalInputDevice=DigitalInputDevice; gpio.PWMOutputDevice=PWMOutputDevice
        with mock.patch.dict(sys.modules,{"gpiozero":gpio}):
            inp=self.call("gpio","input",17,True)
            self.assertEqual(self.call("gpio","read",inp),Decimal("1.0"))
            pwm=self.call("gpio","pwm",18,Decimal("100"),Decimal("0.25"))
            self.call("gpio","write",pwm,Decimal("0.75"))
            self.assertEqual(self.call("gpio","read",pwm),Decimal("0.75"))
            self.call("gpio","close",inp); self.call("gpio","close",pwm)

    def test_pygame_finite_frame_adapter(self):
        pygame=ModuleType("pygame"); pygame.QUIT=1
        pygame.init=lambda:None; pygame.quit=lambda:None
        pygame.display=SimpleNamespace(set_mode=lambda size:SimpleNamespace(fill=lambda color:None),set_caption=lambda title:None,flip=lambda:None,get_driver=lambda:"double")
        pygame.event=SimpleNamespace(get=lambda:[])
        pygame.time=SimpleNamespace(Clock=lambda:SimpleNamespace(tick=lambda fps:None))
        with mock.patch.dict(sys.modules,{"pygame":pygame}):
            result=self.call("game","run_frames","Saga",64,48,3)
        self.assertEqual(result["frames"],3); self.assertEqual(result["driver"],"double")


    def test_gpio_requires_device_capability(self):
        denied = Interpreter("<gpio-denied>", output=lambda _x: None, capabilities=Capabilities())
        gpio = ModuleType("gpiozero")
        class Device: pass
        class OutputDevice(Device):
            def __init__(self,pin): self.pin=pin
        gpio.Device=Device; gpio.OutputDevice=OutputDevice
        try:
            with mock.patch.dict(sys.modules,{"gpiozero":gpio}):
                with self.assertRaisesRegex(Exception, "--allow-device"):
                    MODULES["gpio"].get("output")(denied,[17])
        finally:
            denied.close()


    def test_spark_requires_process_capability(self):
        denied = Interpreter("<spark-denied>", output=lambda _x: None, capabilities=Capabilities())
        pyspark=ModuleType("pyspark"); sqlmod=ModuleType("pyspark.sql")
        class Spark: pass
        class Builder:
            def appName(self,n): return self
            def getOrCreate(self): return Spark()
        Spark.builder=Builder(); sqlmod.SparkSession=Spark
        try:
            with mock.patch.dict(sys.modules,{"pyspark":pyspark,"pyspark.sql":sqlmod}):
                with self.assertRaisesRegex(Exception, "--allow-process"):
                    MODULES["spark"].get("session")(denied,["Saga"])
        finally:
            denied.close()

    def test_spark_local_sql_and_range_contract(self):
        pyspark=ModuleType("pyspark"); sqlmod=ModuleType("pyspark.sql")
        class Row:
            def asDict(self,recursive=True): return {"answer":42}
        class DF:
            def __init__(self,count=0,rows=()): self._count=count; self._rows=rows
            def count(self): return self._count
            def collect(self): return list(self._rows)
        class Spark:
            def __init__(self): self.stopped=False
            def range(self,start,end): return DF(max(0,end-start))
            def sql(self,q): return DF(rows=[Row()])
            def stop(self): self.stopped=True
        class Builder:
            def master(self,m): self.m=m; return self
            def appName(self,n): self.n=n; return self
            def getOrCreate(self): return Spark()
        Spark.builder=Builder(); sqlmod.SparkSession=Spark
        with mock.patch.dict(sys.modules,{"pyspark":pyspark,"pyspark.sql":sqlmod}):
            s=self.call("spark","local_session","Saga",2)
            self.assertEqual(self.call("spark","range_count",s,0,100),100)
            rows=self.call("spark","sql",s,"SELECT 42 AS answer")
            self.assertEqual(rows[0]["answer"],42)
            self.call("spark","stop",s); self.assertTrue(s.stopped)


    def test_release_qualification_tools_launch_without_pythonpath(self):
        root = Path(__file__).parents[1]
        for tool in ("cross_implementation_validation.py", "registry_live_qualification.py"):
            proc = subprocess.run(
                [sys.executable, str(root / "tools" / tool), "--help"],
                cwd=tempfile.gettempdir(),
                text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, tool + "\n" + proc.stdout + proc.stderr)

    def test_ga_readiness_handles_audit_issue_list_and_fails_closed(self):
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "ga.json"
            proc = subprocess.run(
                [sys.executable, str(root / "tools" / "ga_readiness.py"), "--output", str(out)],
                cwd=root, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(doc["ga_ready"])
            self.assertEqual(doc["core_gates"]["security-evidence"]["status"], "BLOCKED")

    def test_external_audit_attestation_verifier_contract(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from tools.review_evidence import build_manifest
        from tools.verify_external_security_attestation import RELEASE, canonical
        import hashlib
        root=Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as td0:
            td=Path(td0); report=td/"report.md"; manifest=td/"source-manifest-fixture.json"
            report.write_text("independent security review\n",encoding="utf-8")
            # The production verifier is intentionally frozen to the 0.50.0
            # evidence line. Build an ephemeral manifest for this contract test
            # rather than pretending the current development tree is that
            # historical frozen source tree.
            manifest.write_text(json.dumps(build_manifest(root),indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
            payload={
                "schema":1, "target_release":RELEASE,
                "source_manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "auditor":{"organization":"Independent Test Lab","reviewer":"Reviewer A"},
                "completed_at_utc":"2026-08-10T10:00:00Z",
                "scope":["compiler","runtime","package-manager","registry","capability-sandbox","crypto-tls","native-host-boundaries"],
                "methods":["source-review","dynamic-testing"],
                "report_sha256":hashlib.sha256(report.read_bytes()).hexdigest(),
                "critical_open":0,"high_open":0,"medium_open":0,"low_open":0,
                "independent":True,"decision":"PASS",
            }
            key=Ed25519PrivateKey.generate(); sig=key.sign(canonical(payload))
            att={"payload":payload,"signature_ed25519_base64":base64.b64encode(sig).decode()}
            pub=key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
            ap=td/"att.json"; kp=td/"pub.hex"; out=td/"verified.json"
            ap.write_text(json.dumps(att),encoding="utf-8"); kp.write_text(pub,encoding="utf-8")
            cmd=[sys.executable,str(root/"tools/verify_external_security_attestation.py"),str(ap),str(kp),"--report",str(report),"--source-manifest",str(manifest),"--output",str(out)]
            proc=subprocess.run(cmd,text=True,capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
            self.assertTrue(json.loads(out.read_text())["pass"])
            payload["high_open"]=1; sig=key.sign(canonical(payload)); att={"payload":payload,"signature_ed25519_base64":base64.b64encode(sig).decode()}; ap.write_text(json.dumps(att),encoding="utf-8")
            proc=subprocess.run(cmd,text=True,capture_output=True)
            self.assertNotEqual(proc.returncode,0)


if __name__ == "__main__": unittest.main()
