# Saga 0.53.0 — Machine & Drone Control Focus

Saga 0.53.0 is a development milestone that makes machine control, robotics, and drone/autonomous systems the primary product direction of the language.

## What changes in 0.53

- Repositions Saga as a control-systems language rather than a general-purpose language that happens to include control libraries.
- Preserves the existing general-purpose language surface for tooling, telemetry, configuration, simulation, and application integration.
- Promotes the existing `machine` and `drone` modules, control annotations, hardware capabilities, resource lifetime rules, and production qualification path as core language/toolchain concerns.
- Adds an explainable Control Report that combines normal Saga language checking with source-level control-profile analysis, timing-contract summaries, stable diagnostics, and explicit evidence boundaries.
- Applies the control contract consistently to class methods and checked helpers across the Python reference implementation and independent Go implementation.
- Adds permanent machine/drone regression coverage to the core CI path for both the Python reference implementation and independent Go implementation.
- Adds a reproducible contest-facing safe/unsafe demo whose comparison is mechanically verified as exactly one added control-path line, plus clean wheel and source-distribution installation smoke tests.
- Adds control-oriented examples and dedicated design/contest documents describing what is software evidence versus target-specific physical evidence.

## Existing machine-control capabilities carried into this line

The 0.53 development line retains PID/2-DOF PID, filtering/observation, advanced motion/control primitives, PWM/servo/motor/encoder support, I2C/SPI/UART/CAN/CAN FD, Modbus, EtherCAT, CANopen/CiA 402, PLC/process-image support, watchdogs, deadline/control guards, and explicit safety-latch behavior.

## Existing drone capabilities carried into this line

The 0.53 development line retains attitude/quaternion/rate/position control, geofencing, missions, RTL/landing helpers, MAVLink 2 and signing, DroneCAN, DShot/PWM ESC helpers, 3D jerk-limited trajectories, multirotor control allocation, link monitoring, visual servoing, VIO/SLAM, multi-drone coordination, and offboard/SITL integration paths.

## Language direction

Saga aims for C-like hardware reach, Rust-like explicit resource/authority boundaries, and Python-like readability. This is a design target, not a claim that Saga currently matches the ecosystem maturity, optimization depth, hardware coverage, memory-safety guarantees, or certification history of those languages.

## Qualification boundary

The version bump and software tests do not create a new frozen production release. Saga 0.50.0 remains the latest frozen release until a later release candidate receives its own source manifest and qualification evidence.

Software CI, native-host execution, SITL, physical HIL, WCET analysis, and functional-safety/regulatory certification remain distinct evidence levels. No physical-system or safety certification is implied by this milestone.
