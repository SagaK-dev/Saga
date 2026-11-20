#!/usr/bin/env python3
"""Saga platform qualification runner.

This runner deliberately distinguishes implementation availability from live
qualification.  Missing credentials/hardware are BLOCKED, never silently PASS.
Potentially destructive/live checks are opt-in through SAGA_*_LIVE variables.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "0.38.0"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(cmd, *, cwd=ROOT, env=None, timeout=180):
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode, proc.stdout


def gate(gate_id, status, *, implemented=True, detail="", evidence=None, reason=""):
    item = {"id": gate_id, "status": status, "implemented": bool(implemented)}
    if detail: item["detail"] = detail
    if evidence is not None: item["evidence"] = evidence
    if reason: item["reason"] = reason
    return item


def qualify_vulkan() -> dict:
    if platform.system() != "Linux":
        return gate("vulkan-swapchain-present", "READY_UNEXECUTED", reason="Live Vulkan qualification is executed by the native OS/hardware harness on this platform.")
    go = shutil.which("go"); xvfb = shutil.which("Xvfb")
    if not go or not xvfb:
        return gate("vulkan-swapchain-present", "BLOCKED", reason="go and Xvfb are required for local live qualification")
    env = os.environ.copy(); env["SAGA_VULKAN_LIVE"] = "1"; env.setdefault("SDL_AUDIODRIVER", "dummy")
    device_kind = "system"
    # Chromium ships a real SwiftShader Vulkan ICD in this validation host.  It
    # exercises the production Vulkan renderer without pretending to be a
    # physical GPU.  A system/physical ICD can be forced through the environment.
    swift = Path("/usr/lib/chromium/libvk_swiftshader.so")
    tmp_icd = None
    if not env.get("VK_ICD_FILENAMES") and swift.exists():
        fd, name = tempfile.mkstemp(prefix="saga-swiftshader-", suffix=".json")
        os.close(fd); tmp_icd = Path(name)
        tmp_icd.write_text(json.dumps({"file_format_version":"1.0.0","ICD":{"library_path":str(swift),"api_version":"1.3.0"}}))
        env["VK_ICD_FILENAMES"] = str(tmp_icd); device_kind = "software-swiftshader"
    configured_display = os.environ.get("SAGA_XVFB_DISPLAY")
    if configured_display:
        display = configured_display
    else:
        # Pick an unused display instead of hard-coding one that can collide in CI.
        # Xvfb creates /tmp/.X<N>-lock and /tmp/.X11-unix/X<N>.
        display = None
        for number in range(100, 200):
            if not Path(f"/tmp/.X{number}-lock").exists() and not Path(f"/tmp/.X11-unix/X{number}").exists():
                display = f":{number}"
                break
        if display is None:
            return gate("vulkan-swapchain-present", "BLOCKED", reason="No free Xvfb display was available in the 100..199 qualification range")
    xv = subprocess.Popen([xvfb, display, "-ac", "-screen", "0", "1024x768x24"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        import time; time.sleep(0.8)
        env["DISPLAY"] = display
        rc, out = run([go, "test", "-tags", "sagadesktop sagavulkan", "./cmd/saga-go", "-run", "TestDesktopVulkanSwapchainPresent", "-count=1", "-v"], cwd=ROOT/"implementations/go", env=env, timeout=90)
    finally:
        xv.terminate()
        try: xv.wait(timeout=3)
        except Exception: xv.kill()
        if tmp_icd: tmp_icd.unlink(missing_ok=True)
    match = re.search(r"Vulkan device=([^\n]+)", out)
    info = match.group(0).strip() if match else out[-1200:]
    if rc == 0:
        physical = device_kind == "system" and not re.search(r"SwiftShader|llvmpipe|software", info, re.I)
        return gate("vulkan-swapchain-present", "PASS_PHYSICAL" if physical else "PASS_SOFTWARE_DEVICE", detail=info, evidence={"device_kind":device_kind,"command":"go test ... TestDesktopVulkanSwapchainPresent"})
    return gate("vulkan-swapchain-present", "FAIL", reason=out[-2000:])


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def qualify_pygame() -> dict:
    if not _module_available("pygame"):
        return gate("pygame-runtime", "READY_UNEXECUTED", reason="pygame is not installed on this host; install the game extra or run the CI qualification job")
    old = os.environ.get("SDL_VIDEODRIVER"); os.environ["SDL_VIDEODRIVER"] = "dummy"
    try:
        from saga.api import run_source
        from saga.native import Capabilities
        out=[]
        run_source('use game\nlet r = game.run_frames("Saga Qualification", 64, 48, 3)\nprint(map_get(r, "frames", 0), map_get(r, "driver", ""))', output=out.append, capabilities=Capabilities(allow_ui=True))
        return gate("pygame-runtime", "PASS", detail=" | ".join(out))
    except Exception as exc:
        return gate("pygame-runtime", "FAIL", reason=f"{type(exc).__name__}: {exc}")
    finally:
        if old is None: os.environ.pop("SDL_VIDEODRIVER",None)
        else: os.environ["SDL_VIDEODRIVER"] = old


def qualify_spark() -> dict:
    if not _module_available("pyspark"):
        return gate("spark-runtime", "READY_UNEXECUTED", reason="pyspark is not installed on this host; CI installs it and executes local[2] qualification")
    try:
        from saga.api import run_source
        out=[]
        run_source('use spark\nlet s = spark.local_session("SagaQualification", 2)\nprint(spark.range_count(s, 0, 100))\nlet rows = spark.sql(s, "SELECT 6 * 7 AS answer")\nprint(map_get(get(rows, 0, map_of()), "answer", 0))\nspark.stop(s)', output=out.append, capabilities=__import__('saga.native', fromlist=['Capabilities']).Capabilities(allow_process=True))
        if out != ["100", "42"]: raise RuntimeError(f"unexpected output {out!r}")
        return gate("spark-runtime", "PASS", detail="local[2] Range/DataFrame count and Spark SQL executed through Saga")
    except Exception as exc:
        return gate("spark-runtime", "FAIL", reason=f"{type(exc).__name__}: {exc}")


def qualify_aws() -> dict:
    if not _module_available("boto3"):
        return gate("aws-live-account", "READY_UNEXECUTED", reason="boto3 is not installed")
    if os.environ.get("SAGA_AWS_LIVE") != "1":
        return gate("aws-live-account", "READY_UNEXECUTED", reason="Set SAGA_AWS_LIVE=1 with an authorized AWS credential chain to call STS GetCallerIdentity through Saga")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        from saga.api import run_source
        from saga.native import Capabilities
        out=[]
        source = f'use cloud\nlet c = cloud.aws_client("sts", "{region}")\nlet r = cloud.call(c, "get_caller_identity", map_of())\nprint(map_get(r, "Arn", "missing"))'
        run_source(source, output=out.append, capabilities=Capabilities(allow_cloud=True))
        if not out or not out[-1].startswith("arn:") or out[-1] == "missing":
            raise RuntimeError("STS identity missing or malformed")
        # The public workflow artifact must not expose a cloud account ID or
        # full principal ARN. The administrator can compare this fingerprint
        # with the expected role locally.
        principal_hash = hashlib.sha256(out[-1].encode("utf-8")).hexdigest()
        return gate("aws-live-account", "PASS",
                    detail="STS GetCallerIdentity through Saga succeeded (principal redacted)",
                    evidence={"principal_sha256": principal_hash, "region": region})
    except Exception as exc:
        return gate("aws-live-account", "FAIL", reason=f"{type(exc).__name__}: {exc}")


def qualify_gpio() -> dict:
    chips = sorted(Path("/dev").glob("gpiochip*")) if Path("/dev").exists() else []
    if not chips:
        return gate("physical-gpio", "BLOCKED", reason="No /dev/gpiochip device is exposed to this host. The GPIO API is implemented; physical qualification requires a board and operator-selected safe pins.")
    if not _module_available("gpiozero"):
        return gate("physical-gpio", "BLOCKED", reason="GPIO hardware is visible but gpiozero is not installed")
    if os.environ.get("SAGA_GPIO_LIVE") != "1":
        return gate("physical-gpio", "READY_UNEXECUTED", reason=f"GPIO chips detected ({', '.join(map(str,chips))}); set SAGA_GPIO_LIVE=1 and SAGA_GPIO_OUTPUT_PIN to a safe test pin")
    pin = os.environ.get("SAGA_GPIO_OUTPUT_PIN")
    if not pin or not pin.isdigit():
        return gate("physical-gpio", "BLOCKED", reason="SAGA_GPIO_OUTPUT_PIN must identify an operator-approved safe output pin")
    try:
        from saga.api import run_source
        from saga.native import Capabilities
        source=f'use gpio\nlet p = gpio.output({int(pin)})\ngpio.off(p)\ngpio.on(p)\ngpio.off(p)\ngpio.close(p)\nprint("GPIO_OK")'
        out=[]; run_source(source, output=out.append, capabilities=Capabilities(allow_device=True))
        return gate("physical-gpio", "PASS_OPERATOR_PIN", detail=f"Output pin {pin} toggled off/on/off through Saga. Electrical readback requires the external lab fixture.")
    except Exception as exc:
        return gate("physical-gpio", "FAIL", reason=f"{type(exc).__name__}: {exc}")



def qualify_machine_control() -> list[dict]:
    """Run the non-destructive machine-control qualification.

    Software qualification and physical-machine evidence are intentionally
    separate.  The default path never energizes an actuator.
    """
    report = ROOT / f"validation/machine-control-{RELEASE}.json"
    rc, out = run([sys.executable, str(ROOT / "tools/machine_control_qualification.py"), "--output", str(report)], timeout=180)
    if rc != 0:
        return [
            gate("machine-control-software", "FAIL", reason=out[-2000:]),
            gate("physical-machine-control", "BLOCKED", reason="Software qualification failed; physical qualification is not accepted."),
        ]
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            gate("machine-control-software", "FAIL", reason=f"qualification report unreadable: {exc}"),
            gate("physical-machine-control", "BLOCKED", reason="No trustworthy machine-control qualification report is available."),
        ]
    software = gate(
        "machine-control-software",
        "PASS" if data.get("pass") is True else "FAIL",
        detail=f"Hosted control algorithms, capability checks and adapter contracts; physical={data.get('physical_qualification','UNKNOWN')}",
        evidence=str(report),
    )
    physical_state = data.get("physical_qualification")
    if physical_state == "PASS":
        physical = gate("physical-machine-control", "PASS_OPERATOR_LAB", detail="Operator-controlled physical machine qualification passed.", evidence=str(report))
    else:
        physical = gate(
            "physical-machine-control",
            "READY_UNEXECUTED",
            reason=data.get("physical_qualification_reason", "Physical machine qualification requires an operator-controlled hardware lab."),
            evidence={"inventory": data.get("hardware_inventory", {}), "software_report": str(report)},
        )
    return [software, physical]

def qualify_mobile_and_os() -> list[dict]:
    sysname = platform.system()
    adb = shutil.which("adb")
    android = gate("android-device", "READY_UNEXECUTED" if adb else "BLOCKED", reason=("adb available; set SAGA_ANDROID_LIVE=1 with a device/emulator to execute validation/mobile/android/validate-device.sh" if adb else "Android SDK/adb and a target device/emulator are not available on this host"))
    ios = gate("ios-device", "READY_UNEXECUTED" if sysname == "Darwin" and shutil.which("xcrun") else "BLOCKED", reason="Requires macOS + Xcode + signed/simulator target; validation/mobile/ios/validate-device.sh is provided")
    windows = gate("windows-real-host", "PASS_HOST_PRESENT" if sysname == "Windows" else "BLOCKED", reason="Requires native Windows execution; cross-builds do not count")
    macos = gate("macos-real-host", "PASS_HOST_PRESENT" if sysname == "Darwin" else "BLOCKED", reason="Requires native macOS execution; cross-builds do not count")
    if os.environ.get("SAGA_PHYSICAL_GAMEPAD") == "1":
        go = shutil.which("go")
        if not go:
            gamepad = gate("physical-gamepad", "BLOCKED", reason="go toolchain is required to build the desktop qualification binary")
        else:
            with tempfile.TemporaryDirectory(prefix="saga-gamepad-") as td:
                binary = Path(td) / ("saga.exe" if sysname == "Windows" else "saga")
                rc, build_out = run([go, "build", "-tags", "sagadesktop", "-o", str(binary), "./cmd/saga-go"], cwd=ROOT/"implementations/go", timeout=120)
                if rc != 0:
                    gamepad = gate("physical-gamepad", "FAIL", reason=build_out[-1600:])
                else:
                    rc, out = run([str(binary), "run", str(ROOT/"validation/gamepad/physical_gamepad.saga")], timeout=30)
                    if rc == 0 and "PHYSICAL_GAMEPAD_PASS" in out:
                        gamepad = gate("physical-gamepad", "PASS_PHYSICAL", detail=out.strip())
                    elif "PHYSICAL_GAMEPAD_REQUIRED" in out:
                        gamepad = gate("physical-gamepad", "BLOCKED", reason="SAGA_PHYSICAL_GAMEPAD=1 was set but SDL enumerated no physical controller")
                    else:
                        gamepad = gate("physical-gamepad", "FAIL", reason=out[-1600:])
    else:
        gamepad = gate("physical-gamepad", "BLOCKED", reason="Attach a real USB/Bluetooth gamepad and set SAGA_PHYSICAL_GAMEPAD=1; virtual-controller E2E is a separate already-tested gate")
    return [android, ios, windows, macos, gamepad]


def qualify_external_audit() -> dict:
    att = os.environ.get("SAGA_EXTERNAL_SECURITY_ATTESTATION", "")
    key = os.environ.get("SAGA_EXTERNAL_SECURITY_PUBLIC_KEY", "")
    report = os.environ.get("SAGA_EXTERNAL_SECURITY_REPORT", "")
    manifest = os.environ.get("SAGA_SOURCE_MANIFEST", str(ROOT/f"release/source-manifest-{RELEASE}.json"))
    if not att or not key or not report:
        return gate("third-party-security-audit", "BLOCKED", implemented=True, reason="An independent auditor must supply a signed attestation, the bound report, and an out-of-band public key. Set SAGA_EXTERNAL_SECURITY_ATTESTATION, SAGA_EXTERNAL_SECURITY_PUBLIC_KEY and SAGA_EXTERNAL_SECURITY_REPORT.")
    rc, out = run([sys.executable, str(ROOT/"tools/verify_external_security_attestation.py"), att, key, "--report", report, "--source-manifest", manifest], timeout=20)
    return gate("third-party-security-audit", "PASS" if rc == 0 else "FAIL", detail=out.strip() if rc == 0 else "", evidence={"attestation":att,"report":report,"source_manifest":manifest} if rc == 0 else None, reason="" if rc == 0 else out.strip())


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output", default=str(ROOT/"validation/platform-qualification-0.38.0.json")); args=ap.parse_args()
    gates=[qualify_vulkan(), qualify_aws(), qualify_gpio(), *qualify_machine_control(), qualify_spark(), qualify_pygame(), *qualify_mobile_and_os(), qualify_external_audit()]
    doc={"schema":1,"release":RELEASE,"generated_at_utc":now(),"host":{"os":platform.platform(),"arch":platform.machine()},"gates":gates}
    doc["summary"]={"pass":sum(g["status"].startswith("PASS") for g in gates),"blocked":sum(g["status"] in {"BLOCKED","READY_UNEXECUTED"} for g in gates),"fail":sum(g["status"]=="FAIL" for g in gates)}
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(doc,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(doc,indent=2,ensure_ascii=False))
    return 1 if doc["summary"]["fail"] else 0
if __name__ == "__main__": raise SystemExit(main())
