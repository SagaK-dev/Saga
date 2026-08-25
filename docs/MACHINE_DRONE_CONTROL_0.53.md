# Saga 0.53 Machine & Drone Control Focus

Saga 0.53 makes machine control, robotics, and autonomous/drone systems the primary engineering target of the language.

The objective is not to imitate another language's syntax. Saga instead combines properties that control engineers commonly need from several ecosystems:

- the direct systems and device reach associated with C;
- explicit resource, authority, and failure boundaries inspired by the safety discipline expected from Rust systems code;
- the readability and iteration speed associated with Python control and robotics work.

Saga 0.53 does not claim equal ecosystem size, compiler maturity, target coverage, hard-real-time guarantees, or certification history to C, Rust, or Python. Those are evidence questions, not branding claims.

## Design priorities

Control-facing development follows these priorities, in order:

1. **Observable control semantics before convenience.** No hidden flight-mode transition, actuator command, or hardware authority escalation should occur as a side effect of an unrelated operation.
2. **Fail-closed authority.** Host device access is capability-gated and control production checks fail when required evidence is missing.
3. **Readable hot paths.** Control loops should be short enough to audit and tune without hiding hardware or timing-sensitive behavior behind a large framework.
4. **Portable control math, explicit adapters.** Algorithms should run in hosted/SITL tests while hardware-specific access remains isolated behind adapters and resources.
5. **Two-implementation regression.** Common machine/drone behavior is tested through both the Python reference implementation and the independent Go implementation.
6. **Evidence-bound deployment claims.** Software tests, native-host tests, SITL, physical HIL, and functional-safety evidence are different levels and must remain distinguishable.

## Machine-control surface

The current control stack includes:

- classic PID and 2-DOF PID control;
- low-pass/biquad filters and alpha-beta/Kalman observers;
- state-space, LQR-oriented, MPC, RLS, and disturbance-observer building blocks;
- velocity/acceleration/jerk-limited motion profiles;
- multi-axis synchronization and fine cyclic-control helpers;
- PWM, servo, DC motor, encoder, FOC/current-loop building blocks;
- I2C, SPI, UART, CAN/CAN FD, Modbus RTU/TCP, and raw EtherCAT adapters;
- CANopen/CiA 402 and process-image/PLC building blocks;
- monotonic-cycle timing, watchdogs, deadline budgets, control guards, and safety latches.

These facilities are useful for hosted control development and for target-specific native adapters. Availability of an API is not evidence that a particular physical controller, bus topology, actuator, or safety circuit has been validated.

## Drone-control surface

The current drone stack includes:

- hosted/SITL attitude estimation;
- Euler and quaternion attitude control;
- rate and position controllers;
- quad-X mixing and general multirotor control allocation;
- actuator-disable handling and allocation residual reports;
- geofence and predictive breach checks;
- waypoint mission state, RTL planning, and landing helpers;
- MAVLink 2 framing, checksum validation, signing and verification;
- common MAVLink telemetry decoding and offboard command builders;
- MAVLink stream parsing and link-quality monitoring;
- DroneCAN framing helpers;
- DShot and PWM ESC helpers;
- jerk-limited 3D trajectories;
- vision, visual servoing, VIO/SLAM, multi-drone coordination, and media-link integration paths.

Flight-state changes remain explicit. Sensor/link/health observations do not silently arm, disarm, enter RTL, or select another mode.

## Control language contracts

Saga control code can use source-level contracts such as:

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

The production control profile rejects constructs that conflict with the declared control path, including categories of hidden allocation/I/O, recursion, indirect calls, shared mutation, and unapproved external calls.

Hardware-facing resources use explicit lifetime/authority mechanisms. `using` provides deterministic cleanup and `move` makes transfer of move-only resources visible in source. Device/network authority is capability-gated at the host boundary.

## Development profiles

Saga deliberately separates several execution/qualification profiles:

### Hosted/reference

Portable algorithms, parsing, type checking, protocol framing, simulation utilities, and control math. Suitable for fast regression and algorithm work; not hard real time.

### Native software qualification

Native executable/runtime behavior on supported hosts. This can prove that code builds and executes on a target OS/architecture, but it does not prove motor/flight hardware behavior.

### SITL / virtual HIL

Software-in-the-loop and virtual-device integration. Useful for flight-control, protocol, mission, and failure-path testing. It remains software evidence.

### Physical HIL / target qualification

Requires actual target hardware, adapters, fieldbus topology, sensors, actuators, timing measurement, and source-bound evidence. This is the point at which target-specific timing and hardware behavior can be claimed.

### Functional-safety / regulatory evidence

Outside the automatic implication of the language. SIL/PL, airworthiness, medical/industrial regulatory requirements, E-stop/STO/interlock architecture, and independent safety assessment require the relevant engineering and evidence.

## Direction toward C/Rust-class systems control

The engineering path is intentionally incremental:

- keep the control surface available without Python application dependencies when using the independent Go/native path;
- continue native lowering and ABI work so more control code can execute without a hosted interpreter;
- keep allocation and external calls statically constrained inside declared control ticks;
- expand deterministic numeric and fixed-width integer support where protocols and hardware registers require it;
- maintain explicit unsafe/native boundaries rather than making arbitrary memory access ordinary language behavior;
- bind release qualification to exact source/toolchain artifacts;
- grow target adapters only when their tests can distinguish software simulation from physical validation.

The target is a control language that can reach hardware like systems languages while remaining practical to read and tune. The project will not claim hard-real-time, memory-safety, flight-worthiness, or industrial-safety properties that have not been demonstrated by the corresponding implementation and target evidence.
