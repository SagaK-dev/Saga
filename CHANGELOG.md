# Saga 0.53.0 (development)

- Refocused Saga on machine control, robotics, and autonomous/drone systems while keeping the existing general-purpose language surface available for tooling, telemetry, configuration, simulation, and integration.
- Added explainable Control Report output with source-level timing/evidence boundaries, per-check status, project source-unit loading, and explicit separation from target WCET, physical HIL, interlock/E-stop validation, and certification claims.
- Made the Production GA control contract apply consistently to class methods and same-receiver checked helpers in both the Python reference implementation and independent Go implementation, including recursive method-graph detection.
- Unified Python CLI version reporting with the package version and aligned Python/Go CLI identity with Saga's machine-control focus.
- Added permanent regression coverage for the 0.53 machine/drone control surface, control-method parity, Control Report behavior, and CLI identity.
- Hardened the contest delivery path with clean wheel/sdist install evidence and a reproducible judge-facing demo; the safe/unsafe comparison is now mechanically verified as one added control-path line and uses the same file-based analysis path as `saga-control-report`.

# Saga 0.52.0 (development)

- Integrated intrinsic `Option[T]` and `Result[T, E]` with the Generic ADT constructor and exhaustive-match model while preserving legacy helpers.
- Added alpha-equivalent generic interface method matching and the `F[A]` applied type-constructor foundation.
- Hardened HKT signatures with consistent constructor arity and explicitly deferred function-kind constructors until their result kind can be represented without information loss.
- Made Go generic overrides compare normalized `where` constraints instead of allowing an implementation to narrow an interface contract.

# Saga 0.51.0 (development)

- Added generic enums such as `Maybe[T]`, constructor inference, contextual nullary variants, typed match payloads, and module-interface preservation in Python and Go.

# Saga 0.50.0

- Added transitive `@control_safe` Production GA control-call validation.
- Added fail-closed machine deployment safety-case gating and source-bound hazard/WCET/HIL evidence.
- Closed shared-mutation, indirect-call, recursion and unknown-call gaps in periodic control code.
- Fixed contextual `move` parsing before comparison/logical/range operators in Python and Go.

# Saga 0.49.0

- Added first-class workspaces and a fail-closed production project gate.
- Added deterministic package/native reproducibility checks for production CI.
- Added explicit `@control_tick(rate_hz, budget_us)` contracts in Python and Go.
- Added timestamped control guarding for stale inputs, jitter and execution budget.
- Retained the advanced-motion/FOC/EtherCAT/CAN-FD stack and external qualification boundaries.

# Saga 0.47.0

- Added a persistent FOC d/q current loop with PMSM feed-forward/decoupling, anti-windup, voltage limiting and SVPWM state.
- Added unified incremental/absolute encoder state with timestamp-derived velocity and explicit absolute alignment.
- Added fixed-size RLS online identification, bounded two-state MPC, disturbance observation and Stribeck friction compensation.
- Added electronic-gearing multi-axis synchronization with bounded correction and skew health.
- Added EtherCAT framing/raw Linux transport, CAN-FD BRS/ESI metadata and timestamp provenance.
- Added the common Python/Go `@control_tick` MCU/RTOS source profile plus allocation-free profile linting.
- Preserved capability-gated physical I/O and explicitly did not claim physical hard-real-time, Distributed Clocks, target WCET or functional-safety qualification.

# Saga 0.46.0

- Added common Python/Go 2-DOF PID with derivative-on-measurement, filtering, feed-forward and back-calculation anti-windup.
- Added motor feed-forward, alpha-beta state observation and second-order mechanical-resonance notch filtering.
- Added portable Clarke/Park/inverse-Park and SVPWM field-oriented-control mathematics.
- Added hosted deadline-budget observation while keeping reaction policy explicit in Saga source.
- Preserved capability-gated physical I/O and the 0.44 hosted soft-real-time/hardware safety boundary.
- Added cross-implementation precision-machine qualification and executable control examples.

# Saga 0.45.0

- Promoted `async fn` / `await` into the common Python/Go hosted language surface with `future[T]` caller typing.
- Added lexical `taskgroup` structured joining, LIFO `defer`, deterministic resource `using`, and resource-focused single-transfer `move`.
- Kept the new words contextual for source compatibility and fixed `using`/trailing-closure parsing ambiguity.
- Added common `.smi.json` async ABI encoding plus Python/Go differential execution and ABI tests.
- Retained the 0.44 4 kHz hosted-control profile and its explicit soft-real-time/hardware qualification boundary.

# Saga 0.39.0

- Added the hosted `drone` flight-control profile to the Python and independent Go implementations.
- Added quaternion attitude control, angular-rate PID, position/velocity control, Quad-X motor mixing, geofence prediction, missions, RTL and landing helpers.
- Added two-tier flight failsafes: controlled HOLD/RTL/LAND retain control authority while hard DISARM/E-stop trips machine safety.
- Added MAVLink 2 framing/CRC/signing/replay checks and DroneCAN classic-CAN single/multi-frame transport helpers.
- Added a SITL-first `drone` project template, deterministic rotational/position qualification and explicit physical-flight/hard-real-time safety boundaries.

# Saga 0.35.0

- Added direct-native inheritance, interfaces, abstract contracts and closed-world virtual dispatch with stable dispatch slots.
- Added managed Option/Result payload tracing and owned native UTF-8 text.
- Added GC-safe native exception unwinding, catchable native runtime failures and finally semantics across return/break/continue.
- Replaced the 0.34 stop-the-world-only collector with young/old generations, minor promotion, incremental major marking and optional concurrent sweep.
- Added concrete native monomorphization for local generic functions/classes, including explicit aggregate type arguments.
- Added dispatch-graph ABI invalidation and Native Runtime ABI 0.35 documentation/qualification.

# Saga 0.31.0
