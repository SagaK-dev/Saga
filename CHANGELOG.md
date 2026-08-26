# Saga 0.53.0 (development)

- Refocused Saga on machine control, robotics, and autonomous/drone systems while keeping the existing general-purpose language surface available for tooling, telemetry, configuration, simulation, and integration.
- Added explainable Control Report output with source-level timing/evidence boundaries, per-check status, project source-unit loading, and explicit separation from target WCET, physical HIL, interlock/E-stop validation, and certification claims.
- Made the Production GA control contract apply consistently to class methods and same-receiver checked helpers in both the Python reference implementation and independent Go implementation, including recursive method-graph detection.
- Unified Python CLI version reporting with the package version and aligned Python/Go CLI identity with Saga's machine-control focus.
- Added permanent regression coverage for the 0.53 machine/drone control surface, control-method parity, Control Report behavior, and CLI identity.

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

- Added real host relocatable object generation for each loaded Saga source unit with native registration symbols and path-independent module payloads.
- Added a cached C-callable Go Standard Runtime archive, startup object, and host-linker final executable step.
- Added ABI-aware incremental object invalidation: implementation-only dependency changes reuse importer objects, while public ABI changes invalidate direct importers.
- Added digest-verified object/runtime/startup/link caches, no-change link skipping, atomic publication, build-directory cross-process serialization, and cache-tamper repair.
- Added `saga build --target native --profile object`, reproducibility qualification, and Linux x86-64 native-object evidence.

# Saga 0.30.0

- Added Natural Module Core: `module`, `public`/`internal`, namespaced `use ... as ...`, qualified nominal types and imported inheritance in both Python and Go implementations.
- Added deterministic `.smi.json` module interfaces with source/ABI/build hashes and dependency-ABI invalidation for frontend separate compilation.
- Added common Python/Go project-root discovery for module graphs and source/interface safety checks.
- Added cross-implementation module conformance and module-specific regression coverage.

# Changelog

## 0.28.0

- Added the hosted `machine` module for PID/profile/filter control, watchdogs, safety latches, control-cycle timing and encoder tracking.
- Added Linux I²C, SPI, UART, SocketCAN/CAN FD, PWM and IIO adapters behind explicit device capability.
- Added guarded servo and two-PWM H-bridge motor abstractions with immediate software zero-output handling on safety trip.
- Added 10-bit/repeated-START I²C, extended CAN identifiers, encoder wrap handling and portable hex/bytes helpers.
- Made watchdog/safety state concurrency-safe and hardware handles deterministic-close resources; close-after-use now fails closed.
- Added non-destructive machine-control qualification and separate software/physical platform gates.
- Expanded Hosted API validation to 237 functions across 29 modules.

## 0.25.0

- Added production Vulkan swapchain/present live qualification; current Linux evidence reaches `vkQueuePresentKHR` on a software Vulkan ICD without falsely claiming a physical GPU.
- Added live qualification harnesses/CI for AWS STS, GPIO, Spark, pygame, Android/iOS devices, native Windows/macOS hosts, physical gamepads and independent signed security-audit attestations.
- Expanded Hosted Standard to 168 entry points with finite-frame pygame, GPIO input/PWM/read/write and Spark local/SQL/range-count APIs.
- Added `--allow-device`; enforced process capability for Spark and public-method validation for cloud SDK calls.
- Fixed Android Gradle generation and StandardCoreRuntime mobile dependency closure; generated Android/iOS runtimes now build, vet and execute Saga source in regression tests.
- Hardened registry install identity validation, staging/rollback and live signed HTTPS qualification.
- Added current-release Python↔Go differential conformance and GA-readiness gates; fixed stale evidence/version metadata and SH-3 0.25 corpus packaging.
- Fixed a Native HTTP response-acknowledgement/request-context race found during final candidate Race Detector verification.
- Added `python -m saga` and a Python statement-level `debug` command with trace/breakpoints.
- Added Language Specification 1.0 RC2 with repaired clause/annex structure; it remains non-Final pending independent GA evidence.
- Fixed standalone qualification-tool imports and GA audit-evidence parsing.

## 0.24.1

- Added the Defensive Cybersecurity Profile with hashing/HMAC/KDF/AEAD, file integrity, IP/CIDR, X.509 and verified TLS facilities.
- Added cross-process KV database locking and persisted-revision transaction conflict detection.
- Hardened Native HTTP redirects/proxy behavior and response-size limits.
- Bounded argv-only process execution by time and output size.
- Fixed symlink lock aliases, DB close synchronization, dead fallback DB code and Hosted test-PKI metadata.
- Retained full language/browser/game/SH-3 regression qualification.

## 0.23.0

- Added the `app` Universal App Action Protocol for Saga-only expression of host/application actions.
- Added 10 typed source APIs and 53 first-party browser operation identifiers across permissions, media, device I/O, realtime, GPU, identity/payment and advanced browser capabilities.
- Added conservative Native actions for system, filesystem, time, cryptographic UUID, argv-only process execution and bounded HTTP GET.
- Fixed a Native checker/runtime mismatch where `use web` and `use embedded` checked successfully but runtime import dispatch rejected them.
- Fixed browser UUID execution on non-secure/opaque Chromium contexts with a `crypto.getRandomValues` RFC 4122 v4 fallback.
- Retained real Chromium Blink/V8 validation without bypassing enterprise URL policy.
- Retained SH-3 compiler/kernel fixed points and Standard Core / Edition 2027 conformance.

## 0.9.0 — 2026-08-07

### Added

- stable detailed learner/tool diagnostic IDs (`SAGA-L101` etc.) alongside broad processing categories;
- English/Japanese diagnostic catalogue, BCP-47-style locale selection and English fallback;
- JSON diagnostic schema 2 and SARIF 2.1.0;
- `saga explain` diagnostic reference command;
- typo suggestions for unknown names and members;
- dedicated malformed UTF-8, non-NFC identifier and bidi-control diagnostics;
- machine-readable diagnostic catalogue and JSON Schema;
- `saga lsp` diagnostic-focused Language Server Protocol bridge with UTF-16/UTF-8/UTF-32 position-encoding negotiation;
- Unicode XID project names and project-name support without a Saga-fixed length ceiling;
- 0.9 Working Draft, internationalization, diagnostic and vulnerability-review profiles.

### Fixed

- conformance tests no longer depend on localized Japanese prose;
- malformed UTF-8 now reaches a controlled lexical diagnostic rather than a host file-decoding error;
- SARIF no longer advertises an invented product URL;
- LSP non-BMP source positions are converted from Saga scalar columns to negotiated protocol positions;
- compatibility snapshot and independent-lab packaging use current 0.9 files;
- fuzz smoke tool no longer relies on external `PYTHONPATH` setup;
- native installer executes post-install self-conformance;
- active documents no longer contradict the no-fixed-normative-ceilings model.

## 0.8.0

- Removed former language-prescribed numeric resource ceilings.
- Added process-based CPU parallel map/filter/reduce with isolated worker values.
- Updated Go PCL1 arbitrary-precision range behavior and resource model.
