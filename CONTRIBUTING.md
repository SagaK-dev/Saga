# Contributing to Saga

Saga is a programming-language and toolchain project focused on machine control, robotics, and drone/autonomous systems. Changes should preserve readability, explicit hardware authority, deterministic behavior where promised, and the distinction between software qualification and physical-machine certification.

## Development setup

Requirements:

- Python 3.13+
- Go toolchain compatible with `implementations/go`
- Git

Create an isolated Python environment and install development dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Control changes

Machine-, robot-, or drone-facing changes must preserve the common Saga control surface in both implementations. Add focused regression coverage for every behavior change, especially around:

- actuator output and saturation;
- timing/deadline behavior;
- watchdog and safety-latch behavior;
- device/transport capability checks;
- motion and control math;
- MAVLink/DroneCAN/CAN/fieldbus framing;
- flight arming, disarming, and mode transitions;
- resource ownership and deterministic cleanup.

Do not weaken a safety or authority check merely to make a test pass. Health telemetry must not acquire hidden policy unless the language specification explicitly defines that policy.

## Before opening a pull request

Run at least:

```bash
python -m compileall -q saga tools
python -m unittest \
  tests.test_machine_drone_focus_053 \
  tests.test_machine_control_028 \
  tests.test_machine_control_036 \
  tests.test_drone_control_040 \
  tests.test_control_4khz_044 \
  tests.test_control_ga_050 \
  tests.test_production_industrial_049 \
  tests.test_virtual_hil_048 \
  tests.test_advanced_motion_047 \
  tests.test_precision_machine_046 \
  tests.test_generic_abstractions_052 \
  tests.test_generic_adts_051 \
  tests.test_language_synthesis_045
cd implementations/go
go test ./...
go vet ./...
```

Changes requiring OpenCV, physical devices, external services, native-host behavior, registry credentials, mobile toolchains, or physical-lab hardware must also run the matching qualification workflow or tool when that environment is available.

## Pull-request rules

- Keep one coherent purpose per PR.
- Explain user-visible language, control-surface, protocol, or ABI changes.
- Add or update tests for behavior changes.
- Prefer one common Saga scenario in both implementations when changing shared language/control behavior.
- Do not claim physical HIL, WCET, SIL/PL, fieldbus, motor/drive, flight, device, or security certification without the corresponding evidence.
- Do not commit credentials, MAVLink signing keys, tokens, generated virtual environments, caches, or local CI evidence.
- Preserve historical release evidence; new development must not rewrite an old frozen manifest to make it match a changed tree.

## Release evidence

`release/source-manifest-*.json` files describe frozen historical source candidates. Once a release is frozen, later development on `main` may diverge from that historical manifest. A new release qualification must bind a new candidate to a new manifest rather than modifying old evidence.
