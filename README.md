# Saga

**Saga is a programming language for machine control, robotics, and autonomous/drone systems.**

The project starts from a simple problem: control software should be readable enough to review and tune, but timing-sensitive code and hardware authority should not disappear behind convenient APIs.

Saga combines three ideas:

- **readable control code** with a compact, Python-like surface;
- **explicit control boundaries** such as `@control_tick`, `@control_safe`, capabilities, move-only resources, and deterministic cleanup;
- **native systems reach** through machine/drone libraries, device buses, native code generation, and a second implementation in Go.

Saga is an active language project, not a safety-certified product. Software checks can reject risky source patterns; they cannot replace target WCET measurement, physical HIL, E-stop/STO/interlock validation, airworthiness work, or other deployment-specific evidence.

## The 60-second idea

A periodic machine-control path should make its timing contract visible in the source:

```saga
@control_safe
fn clamp_command(value: decimal) -> decimal {
    if value > 1.0 { return 1.0 }
    if value < -1.0 { return -1.0 }
    return value
}

@control_tick(20000, 35)
fn current_tick(error: decimal) -> decimal {
    return clamp_command(error * 0.5)
}
```

`@control_tick(20000, 35)` declares a 20 kHz control path with a 35 µs source-level execution budget. Saga checks the declared control surface and its verified helpers for patterns that make the path difficult to bound or reason about, including hidden blocking/external I/O, unbounded work, shared mutation, indirect calls, unverified helpers, and recursion.

Saga can also turn that analysis into an explainable report:

```bash
saga-control-report examples/contest/diff_safe_control.saga
saga-control-report examples/contest/diff_safe_control.saga --html build/control-report.html
```

The visual report converts 20 kHz into a 50 µs period, shows that the 35 µs declared budget uses 70% of the period, lists the checks performed, and keeps the boundary between source analysis and physical-safety evidence explicit.

A deliberately unsafe example is included too:

```bash
saga-control-report examples/contest/diff_unsafe_control.saga
```

The useful part is not simply that it fails. The report points to the source location, keeps a stable `SAGA-C...` diagnostic code, and suggests how to move time-dependent/raw I/O outside the periodic path.

See [`docs/DIFF_SHIZUOKA_2026.md`](docs/DIFF_SHIZUOKA_2026.md) for the contest demo and judging story.

## Project status

- **Latest frozen release:** Saga 0.50.0 — Production GA Control Hardening
- **Current development version:** Saga 0.53.0 — Machine & Drone Control Focus
- **Frozen release branch:** `release/0.50.0-production-ga`
- **Development branch:** `main`
- **License:** MIT
- **Python requirement:** 3.13+

The Production GA name refers to a language/toolchain control profile. It is not a functional-safety certificate for a physical machine or aircraft.

## Why a language instead of another control library?

A library can provide a PID controller or a CAN API. Saga's main experiment is different: make important control constraints visible to the parser, checker, diagnostics, and build tooling.

That gives the toolchain a chance to answer questions such as:

- Which functions are part of the periodic control surface?
- What frequency and budget did the author declare?
- Did a helper hide blocking I/O?
- Did shared mutation or recursion enter the control call graph?
- Does a device operation require an explicit capability?
- Can a reviewer see why a pattern was rejected and what to change?

These are language/tooling concerns, not just math-library concerns.

## Quick start

```bash
git clone https://github.com/SagaK-dev/Saga.git
cd Saga
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

Try the control demo:

```bash
saga check examples/contest/diff_safe_control.saga
saga run examples/contest/diff_safe_control.saga
saga-control-report examples/contest/diff_safe_control.saga
```

Run the regression tests added for the report:

```bash
python -m unittest tests.test_control_report_053 tests.test_control_ga_050
```

## Machine-control surface

Saga currently includes software implementations and adapters for areas such as:

- PID / 2-DOF PID, filtering, observers, Kalman/RLS, state-space and MPC-oriented primitives;
- jerk/acceleration-limited motion, synchronized axes, control allocation and kinematics;
- encoder/PWM/servo/DC motor and field-oriented-control building blocks;
- control cycles, deadline budgets, watchdogs, guards and safety latches;
- I2C, SPI, UART, CAN/CAN FD, Modbus and raw EtherCAT paths;
- CANopen/CiA 402 and PLC/process-image building blocks;
- capability-gated host/device access and explicit resource lifetime.

The project intentionally separates source-level restrictions from claims about a particular controller, actuator, fieldbus installation, or safety circuit.

## Drone and autonomous-control surface

The drone layer includes reusable software/control pieces such as:

- attitude, body-rate and position-control helpers;
- quaternion/RPY control and multirotor allocation;
- flight-state and health transitions;
- geofence, waypoint, RTL and landing helpers;
- MAVLink 2 framing, validation, signing/verification and telemetry decoding;
- DroneCAN and DShot/PWM helpers;
- trajectory, link-quality, vision/media and coordination integration paths.

Flight-state changes remain explicit. Health observations do not silently arm, disarm, or change modes.

## Language and toolchain

Saga also contains the infrastructure expected of an independent language project:

- lexer, parser and AST;
- static type checking;
- algebraic data types, `Option[T]` / `Result[T, E]`, generics and match checking;
- namespaced modules and separate-compilation interfaces;
- `async` / `await`, task groups, `defer`, `using`, and resource-focused `move`;
- native/WASM code generation and native ABI work;
- deterministic package/workspace locking;
- diagnostics, LSP, debugging and profiling;
- capability auditing;
- a Python reference implementation and an independent Go implementation.

The two implementations do not need identical internals. Documented common behavior should have matching observable semantics where the shared specification requires it.

## Core commands

```text
run               execute checked Saga source
check             parse/type-check without execution
repl              interactive REPL
new               create a project
lint / fmt        style and source checks
module            separate-compilation interfaces
test              run Saga tests
lock / verify     reproducible project locking
production-check  project/workspace production gate
pack              deterministic .sagapkg creation
build             native executable or WebAssembly build
conformance       Standard Core self-conformance
lsp               Language Server Protocol server
debug / profile   debugging and profiling
capabilities      static capability audit
doctor            environment diagnostics
```

Control report:

```text
saga-control-report   explain and visualize the source-level control profile
```

## Repository structure

```text
saga/               Python reference implementation and control libraries
implementations/go/ independent Go implementation and native control runtime
spec/               language and control-profile specifications
docs/               design, control, qualification and contest documentation
tests/              language/control regression tests
tools/              qualification/release/developer tooling
validation/         validation and qualification evidence
release/            frozen source manifests
examples/           Saga programs and control demos
.github/workflows/  CI and qualification workflows
```

## Qualification and evidence

For the stricter machine-production gate:

```bash
saga production-check --native --machine
```

This gate is designed to fail closed when required source-bound timing, hazard, WCET, HIL, or hardware-safety evidence is absent. A green software CI run is evidence for the software tests that actually ran; it is not proof that an arbitrary robot, motor, PLC, drone, radio link, or safety circuit is physically safe.

Useful documents:

- `docs/MACHINE_DRONE_CONTROL_0.53.md`
- `spec/SAGA_PRODUCTION_GA_CONTROL_0.50.md`
- `docs/PRODUCTION_GA_CONTROL_0.50.md`
- `RELEASE_NOTES_0.50.0.md`
- `saga-REVIEW_REPORT-0.50.0.md`
- `saga-VALIDATION-0.50.0.md`

## Contributing

See `CONTRIBUTING.md`.

For machine/drone-facing changes, add regression coverage for the affected control surface. Do not weaken an authority or safety check only to make a test pass, and do not rewrite historical frozen-release evidence to match a changed development tree.

At minimum, run the relevant Python control tests and the independent Go regression:

```bash
cd implementations/go
go test ./...
go vet ./...
```

## Security boundary

See `SECURITY.md` for vulnerability-reporting guidance and the machine-control safety boundary.

Do not commit secrets, MAVLink signing keys, device credentials, production tokens, or sensitive third-party data. Do not describe simulated, hosted, or software-only validation as physical HIL or certification.