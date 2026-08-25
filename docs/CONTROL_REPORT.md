# Saga Control Report

`Saga Control Report` explains the source-level control contract that Saga can actually observe. It is meant for review, debugging, education, and qualification preparation. It is not a replacement for target timing or physical safety evidence.

## What the report answers

For an entry source file, `saga-control-report` uses the same source-unit loading and normal language checking path as `saga check`. It then reports:

- which `@control_tick` and `@control_safe` functions form the visible control surface;
- which source file and module each control function belongs to;
- whether a tick declares `(rate_hz, budget_us)`;
- the declared period, budget ratio, and arithmetic headroom when that declaration is valid;
- source-level control-profile violations with stable `SAGA-C...` diagnostics and fix hints;
- whether the ordinary Saga parser/type checker accepted the program;
- whether the analysis covered a successfully loaded program or only a partial entry file after a project-load failure.

## Verdicts

### `pass`

The Saga language check completed and no violation was found in the supported source-level control-profile rules for the loaded control surface.

`pass` does **not** mean that the declared budget was measured on hardware. A declaration such as `@control_tick(20000, 35)` states a 20 kHz / 35 µs source contract; it does not establish WCET by itself.

### `fail`

At least one Saga control-profile rule was violated. The report keeps the diagnostic code, source location, message, and fix hint.

### `invalid`

The program did not pass normal Saga parsing/type checking for a reason that is not itself a control-profile conclusion. The report deliberately avoids presenting this as a passing control program.

### `not-applicable`

The loaded program contains no visible `@control_tick` or `@control_safe` control surface.

## Timing status

Saga keeps the legacy bare form below source-compatible:

```saga
@control_tick
fn tick(error: decimal) -> decimal {
    return error
}
```

The control restrictions still apply, but the report marks the timing contract as `not-declared`. It must not show a green timing claim or invent a frequency/budget.

A timed declaration is explicit:

```saga
@control_tick(20000, 35)
fn current_tick(error: decimal) -> decimal {
    return error * 0.5
}
```

For this declaration the report can derive a 50 µs period, 35 µs declared budget, 70% declared budget ratio, and 15 µs arithmetic headroom. Those values describe the declaration only.

## Project and module behavior

Saga source units with `module` / `use "...saga"` keep their lexical namespace in the report. For example, a `tick` function from a module imported as `ctrl` is displayed as `ctrl.tick` rather than being flattened into the entry file.

If project loading fails, the original diagnostic is retained. The report may inspect the entry source for context, but its `analysis_scope` becomes `entry-only-after-load-failure`, and the boundary text states that the result is not a complete project control conclusion.

## JSON schema 2

The machine-readable report includes these top-level fields:

- `schema`: currently `2`;
- `implementation_version`: Saga implementation version used to produce the report;
- `verdict`: `pass`, `fail`, `invalid`, or `not-applicable`;
- `analysis_scope`: how much of the program was available to analysis;
- `source_units`: loaded source files when available;
- `language_check`: normal Saga language-check status and diagnostic;
- `timing_contract`: aggregate timing-declaration status;
- `control_functions`: discovered control functions and their source locations;
- `checks`: per-category `pass`, `fail`, `partial`, `not-declared`, or `not-applicable` status;
- `issues`: control diagnostics;
- `boundary`: explicit statement of what the report does not prove.

Consumers should branch on fields/status values rather than scraping terminal or HTML wording.

## Evidence boundary

The report is source-level evidence. It does not establish:

- measured WCET or deadline behavior on a target controller;
- allocator-free behavior of a particular backend unless separately demonstrated;
- physical HIL results;
- E-stop, STO, interlock, actuator, sensor, bus, or wiring behavior;
- flight-worthiness, SIL/PL, regulatory approval, or functional-safety certification.

Those claims require the corresponding target-specific measurements, hardware configuration, procedures, and independent evidence.