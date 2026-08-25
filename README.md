# Saga

**Saga is a programming language for machine control, robotics, and autonomous/drone systems.**

Saga is designed to make control software readable enough to iterate on quickly while keeping hardware authority, timing-sensitive code, resource lifetime, and native execution explicit. The project aims to combine three qualities that are usually split across different ecosystems:

- **C-style hardware reach:** native interfaces, device buses, fieldbus work, deterministic low-level control paths, and direct systems integration;
- **Rust-style explicit control boundaries:** capability-gated hardware access, move-only resources, deterministic cleanup, fail-closed production checks, and static control-profile restrictions;
- **Python-style readability:** compact control code, a small surface language, rapid hosted/SITL experimentation, and a Python reference implementation.

Saga does **not** claim the ecosystem maturity, compiler maturity, hardware coverage, or certification history of C, Rust, or Python today. The goal of the 0.53 development line is narrower and measurable: make machine and drone control the primary language use case and continuously verify that control-facing behavior works in both Saga implementations.

## Project status

- **Latest frozen release:** Saga 0.50.0 — Production GA Control Hardening
- **Current development version:** Saga 0.53.0 — Machine & Drone Control Focus
- **Frozen release branch:** `release/0.50.0-production-ga`
- **Development branch:** `main`
- **License:** MIT
- **Python requirement:** 3.13+

`release/source-manifest-0.50.0.json` remains the immutable source description of the frozen 0.50.0 candidate. Saga 0.53.0 on `main` is a development line. A future frozen release must create new source-bound evidence rather than rewriting historical 0.50 evidence.

The **Production GA** label on Saga 0.50 describes a language/toolchain control profile. It is **not** a functional-safety certificate for a physical machine or aircraft. Hard real-time guarantees, WCET, physical HIL, actuator/drive validation, E-stop/STO/interlock behavior, airworthiness, SIL/PL evidence, and regulatory approval remain target- and deployment-specific.

## Why Saga for control systems?

Machine and flight-control code has conflicting requirements: it needs low-level access and predictable behavior, but it also needs to remain understandable during tuning, diagnosis, simulation, and review. Saga makes those concerns part of the language/toolchain rather than treating them as unrelated libraries.

The intended development loop is:

1. write readable control logic in Saga;
2. type-check and apply control-profile restrictions before execution;
3. run portable control logic in the hosted reference implementation;
4. run the same control surface through the independent Go implementation;
5. use native, SITL, HIL, and target-specific adapters where appropriate;
6. require source-bound qualification before making deployment claims.

## Machine-control stack

Saga's current machine-control surface includes software implementations and adapters for:

- PID and 2-DOF PID control;
- low-pass and biquad filtering;
- alpha-beta observation, Kalman filtering, disturbance observation, RLS, state-space control, LQR/MPC-oriented control support;
- jerk-limited and acceleration-limited motion profiles;
- synchronized multi-axis motion and control allocation;
- encoder tracking, PWM, servo, and DC motor control;
- field-oriented motor-control building blocks;
- monotonic control cycles, deadline budgets, watchdogs, control guards, and safety latches;
- I2C, SPI, UART, CAN/CAN FD, Modbus RTU/TCP, and raw EtherCAT paths;
- CANopen/CiA 402, PLC scan/process-image, and kinematic-control building blocks;
- capability-gated access to host devices and explicit resource lifetime.

Example:

```saga
use machine

let safety = machine.safety_latch()
let pid = machine.pid(1.2, 0.08, 0.01, -1.0, 1.0)

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

print(machine.pid_step(pid, 10.0, 8.0, 0.01))
```

`@control_safe` and `@control_tick` are source/toolchain contracts. The production control profile rejects classes of hidden allocation/I/O, recursion, indirect calls, shared mutation, and unapproved external behavior from declared control paths.

## Drone and autonomous-control stack

Saga's current drone surface is built around explicit flight-state transitions and reusable control primitives rather than automatic hidden policy. It includes:

- attitude estimation for hosted/SITL loops;
- Euler/RPY and quaternion attitude control;
- body-rate and position controllers;
- quad-X mixing and general multirotor control allocation;
- actuator-disable handling and allocation residual reporting;
- geofence checks and predictive boundary checks;
- waypoint missions, RTL planning, and landing-profile helpers;
- MAVLink 2 framing, checksum validation, signing/verification, common telemetry decoding, stream parsing, and offboard command builders;
- DroneCAN framing helpers;
- DShot packet creation and PWM ESC helpers;
- jerk-limited 3D trajectory generation;
- link-quality monitoring;
- visual servoing, VIO/SLAM, multi-drone coordination, and vision/media integration paths.

A minimal software-only flight-control surface looks like this:

```saga
use machine
use drone

let safety = machine.safety_latch()
let flight = drone.flight_manager(safety, 0.2)

drone.health_update(flight, true, true, 0.8, true, true, true)
drone.arm(flight, true)

let mixer = drone.quad_x_mixer(0.05, 1.0)
let motors = drone.mix_quad_x(mixer, 0.5, 0.0, 0.0, 0.0)

print(drone.flight_allowed(flight))
print(len(motors))
```

Health updates are observations; they do not silently change the flight mode. Arming, disarming, mode changes, and safety trips remain explicit control events.

## Hardware authority and resource safety

Control software should not gain hardware authority merely because a module was imported. Saga keeps host/device operations behind capabilities and treats hardware-facing objects as resources.

Language/toolchain mechanisms include:

- capability-gated device and external authority;
- `using` for deterministic cleanup;
- `move` for explicit transfer of move-only resources;
- static checking around resource reuse;
- fail-closed production checks when required evidence is missing;
- separate software qualification from physical-system certification.

This is intentionally less pervasive than making every ordinary value subject to ownership rules. Stronger lifetime/authority rules are concentrated where the program can affect external state.

## Control-oriented language design

Saga remains a complete general-purpose language, but general-purpose features support the control use case rather than define the project identity. Important language/toolchain areas include:

- static types with exact-number-oriented defaults;
- `Option[T]` / `Result[T, E]` and explicit failure propagation;
- generic ADTs and generic abstraction work;
- namespaced modules, public/internal visibility, separate compilation, and deterministic interfaces;
- `async` / `await`, lexical `taskgroup`, `defer`, deterministic `using`, and resource-focused `move`;
- native/WASM code generation and native ABI work;
- package/workspace locking and deterministic artifacts;
- LSP, debugging, profiling, diagnostics, and capability auditing;
- Python reference implementation plus an independent Go implementation.

## Two implementations, one control surface

Saga keeps a Python reference implementation and an independent Go implementation. For machine/drone-facing language behavior, 0.53 adds permanent control regression coverage so changes cannot silently preserve one implementation while breaking the other.

The implementations are not assumed to be identical internally. The requirement is that documented common Saga behavior has matching observable semantics and diagnostics where the shared specification requires them.

## Quick start

Clone and install the reference implementation:

```bash
git clone https://github.com/SagaK-dev/Saga.git
cd Saga
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Check the toolchain:

```bash
saga --version
saga doctor
```

Run a control program:

```bash
saga check examples/control/machine_pid.saga
saga run examples/control/machine_pid.saga
```

## Core CLI

```text
run               execute checked Saga source
check             parse/type-check without execution
repl              interactive REPL
new               create a Saga project
lint / fmt        style and source checks
module            generate separate-compilation module interfaces
test              run Saga tests
lock / verify     reproducible project locking
production-check  project/workspace production gate
pack              deterministic .sagapkg creation
build             native executable or WebAssembly build
conformance       Standard Core self-conformance
lsp               Language Server Protocol server
debug / profile   debugging and profiling
registry          package-registry server/client workflows
mobile            generate mobile runtime projects
ecosystem         package/bridge SDK tooling
capabilities      static capability audit
doctor            environment diagnostics
```

Run `saga --help` or `saga <command> --help` for the full surface.

## Qualification

For control-oriented deployment checks:

```bash
saga production-check --native --machine
```

The machine-production gate is designed to fail closed when required source-bound timing, hazard, WCET, HIL, or independent hardware-safety evidence is absent.

Development CI covers software behavior. Additional workflows cover native desktop hosts, platform/runtime qualification, mobile build evidence, live registry qualification, and self-hosted physical hardware-lab qualification.

A green software CI run is evidence that the tested software behavior passed. It is **not** proof that an arbitrary motor, PLC, robot, drone, flight controller, radio link, or safety circuit is safe in the physical world.

See:

- `docs/MACHINE_DRONE_CONTROL_0.53.md`
- `spec/SAGA_PRODUCTION_GA_CONTROL_0.50.md`
- `docs/PRODUCTION_GA_CONTROL_0.50.md`
- `RELEASE_NOTES_0.50.0.md`
- `saga-REVIEW_REPORT-0.50.0.md`
- `saga-VALIDATION-0.50.0.md`

## Repository structure

```text
saga/               Python reference implementation and control libraries
implementations/go/ independent Go implementation and native control runtime
spec/               language and control-profile specifications
docs/               design, control, and qualification documentation
tests/              Python language/control regression tests
tools/              qualification/release/developer tooling
validation/         validation and qualification evidence
release/            frozen source manifests
examples/           Saga programs, including control examples
.github/workflows/  CI and qualification workflows
```

## Contributing

See `CONTRIBUTING.md`. Machine/drone-facing changes must include regression coverage for the affected control surface and must not weaken safety/authority checks merely to make a test pass.

At minimum, run the relevant Python control tests plus:

```bash
cd implementations/go
go test ./...
go vet ./...
```

Do not rewrite historical release evidence to make it match a changed development tree.

## Security and physical-safety boundary

See `SECURITY.md` for vulnerability-reporting guidance and the machine-control safety boundary.

Do not commit secrets, MAVLink signing keys, device credentials, production tokens, or sensitive third-party data. Do not describe simulated, hosted, or software-only validation as physical HIL or safety certification.

## Release history

The repository retains release notes, specifications, review reports, validation documents, and source manifests for prior milestones. Saga 0.50.0 remains the latest frozen release; Saga 0.53.0 is the active machine/drone-control development line.