# DIFF SHIZUOKA 2026 submission plan

This document is intentionally narrower than Saga's full roadmap. The contest story is one problem, one language idea, and one demo that a judge can understand in under two minutes.

## Category

**Programming / middle-school / problem-solving**

Saga should be presented as a programming language for people who need to write machine, robot, and drone control code without hiding timing-sensitive behavior behind a large runtime surface.

One-sentence pitch:

> Saga makes control code readable, while the language can explain when a periodic control path contains work that should not be there.

Do not pitch Saga as "a language that can do everything." The repository has a wide feature set, but that makes the contest story harder to understand. For DIFF, the focus is explainable machine control.

## Problem to show

A beginner can write readable control logic, but machine-control software also has concerns that ordinary application code does not:

- a periodic loop has a fixed cycle and execution budget;
- blocking I/O inside that loop can make timing unpredictable;
- hidden helper functions can accidentally reintroduce unsafe work;
- shared mutation, recursion, and indirect calls make the control path harder to reason about;
- a compiler error that only says "invalid" is difficult to learn from.

Saga already has `@control_tick` and `@control_safe`. The current development line also includes a human-readable **Control Report** and a self-contained Japanese judge view, so these language rules are visible as a product feature rather than remaining an implementation detail.

## 2-minute execution-video flow

The contest requests an execution video of no more than two minutes. Keep the camera on the product; do not spend most of the video on slides.

### 0:00–0:15 — the problem

Show `examples/contest/diff_safe_control.saga` and say:

"This is a 20 kHz current-control function. A machine-control program has to stay readable, but it also has to make its timing boundary explicit."

Point to:

```saga
@control_tick(20000, 35)
fn current_tick(error: decimal) -> decimal {
    return clamp_command(error * 0.5)
}
```

### 0:15–0:45 — explain a safe control path

Run:

```bash
saga-control-report examples/contest/diff_safe_control.saga
saga-control-report examples/contest/diff_safe_control.saga --html build/diff-safe.html
```

Open `build/diff-safe.html`.

Show that 20,000 Hz means a 50 µs period and the declared 35 µs budget consumes 70% of that period. Then show the checks for bounded work, hidden I/O, shared mutation, static calls, checked helpers, recursion, and resource lifetime.

### 0:45–1:15 — make one dangerous change

Open `examples/contest/diff_unsafe_control.saga`. It calls `machine.monotonic_ns()` from inside the periodic path.

Run:

```bash
saga-control-report examples/contest/diff_unsafe_control.saga
saga-control-report examples/contest/diff_unsafe_control.saga --html build/diff-unsafe.html
```

The important moment is not merely that it fails. Show the exact line, stable `SAGA-C...` diagnostic code, and the suggested fix: move time-dependent/raw I/O outside the periodic path and pass prepared state into the tick.

### 1:15–1:40 — prove that it is a language feature

Run the existing compiler check on the safe example:

```bash
saga check examples/contest/diff_safe_control.saga
```

Briefly show `tests/test_control_report_053.py` and `tests/test_control_ga_050.py`. Explain that the same control restrictions are regression-tested rather than being a hard-coded contest animation.

### 1:40–2:00 — value and future

Close with:

"Saga's goal is not to claim that a static check makes a robot or drone physically safe. The goal is to make timing and hardware authority visible in the language, catch mistakes earlier, and explain the reason in a form a learner can understand."

That boundary is important. It makes the project more credible than claiming certification or physical safety that has not been measured.

## How this maps to the 2026 judging criteria

For the middle/high-school problem-solving track, the published rubric totals 27 points: idea/originality, target breadth, and value created (6), UX/UI (6), specification and programming level (6), appeal/social relevance/future potential (4), and final-presentation quality (5). The contest demo should make as many of those points visible without requiring a judge to inspect the repository first.


### Originality — 2 points

Lead with the combination, not individual ingredients:

- readable, compact source syntax;
- explicit periodic control contracts in source;
- transitive restrictions on helpers called from control code;
- an explainable report that converts compiler rules into a visual interface.

Do not claim that PID, static analysis, ownership, or real-time programming is itself new. The originality claim is the way Saga combines readable source, explicit control boundaries, and explainable feedback in one language/toolchain.

### Breadth of target users — 2 points

Use concrete groups:

- students learning robotics and control;
- school robotics teams and makers;
- developers prototyping robots, small machines, and drones;
- engineers reviewing control logic before target-specific testing.

Avoid saying "everyone." Explain the shared problem these groups have: control logic must be understandable, but the dangerous timing boundary must remain visible.

### Value created — 2 points

The value is earlier feedback and easier review:

- mistakes are found before target execution;
- a learner sees why a pattern is rejected and what to change;
- a reviewer can see declared frequency, period, budget and control boundaries in one report;
- the tool clearly separates source-level checks from physical-safety claims.

No unmeasured claim such as "prevents accidents by X%" should be used without data.

### UX/UI — 6 points total

The Control Report and contest index exist for this criterion as much as for debugging:

- one command from source to a self-contained judge view;
- the judge view is Japanese-first and shows the problem, one-line comparison, PASS/FAIL result, diagnosis, and evidence boundary on one page;
- the same analysis is also available as terminal text, JSON, or a detailed self-contained HTML report;
- timing numbers are converted into period, declared budget, budget percentage, and headroom;
- problems include the source location, stable diagnostic code, explanation, and suggested fix;
- the layout is responsive, print-friendly, keyboard-readable, and does not require network access.

A browser editor can still be a later extension, but the submission should not depend on one. Offline reproducibility is a stronger first-round property.

### Specification — 4 points

Explain the boundary precisely:

- `@control_tick(rate_hz, budget_us)` declares a source-level periodic contract;
- `@control_safe` marks helpers that may be called from a control path after the same restricted-surface checks;
- control-path validation rejects unbounded/dynamic work, hidden blocking or external I/O, shared mutation, indirect calls, unverified helpers, and recursion as defined by the control profile;
- a passing report is **not** WCET evidence, physical HIL evidence, an E-stop/STO test, airworthiness evidence, or a safety certificate.

The last point is part of the specification, not a weakness.

### Program level — 2 points

When asked about implementation difficulty, show real components rather than listing buzzwords:

- lexer / parser / AST;
- static type checker;
- control-profile whole-program call-graph validation;
- stable diagnostics;
- Python reference implementation and independent Go implementation;
- native/codegen work and machine/drone libraries;
- regression tests and CI.

### Appeal, social relevance, and future potential — 4 points

Keep the future believable:

1. improve the visual report and editor integration;
2. measure false positives on real student/robotics control programs;
3. run hardware-in-the-loop tests on specific boards and control targets;
4. publish small, reproducible examples for school robotics;
5. only then make stronger target-specific performance or safety claims backed by evidence.

## Submission checklist

Before uploading:

- [ ] `python -m unittest tests.test_control_report_053 tests.test_control_ga_050`
- [ ] `saga check examples/contest/diff_safe_control.saga`
- [ ] safe report returns exit code 0
- [ ] unsafe report returns exit code 1 and an actionable diagnostic
- [ ] judge-facing `index.html` is Japanese-first and readable at desktop/mobile widths
- [ ] PASS/FAIL, the one-line diff, timing numbers, diagnostic, and correction hint are visible without opening developer tools
- [ ] HTML reports render correctly on desktop and mobile widths
- [ ] core GitHub Actions CI is green
- [ ] execution video is at most 2 minutes
- [ ] source submitted matches the version shown in the video
- [ ] optional programming-flow material uses the same terminology as the source
- [ ] claims distinguish source-level validation from physical testing/certification
- [ ] author can explain `@control_tick`, `@control_safe`, one rejected example, and one design trade-off without reading a script

## Eligibility note

The 2026 programming contest requires an original work that has not already been submitted to another contest. Confirm eligibility before submission. If this exact Saga work has already been entered elsewhere, do not assume it can be submitted unchanged; check the current contest rules with the organizer.

## AI-assisted development note

The 2026 judging criteria state that the presence or absence of generative-AI use is not itself part of the program-level score. Regardless of tools used during development, the submission should be code the author can read, test, change, and explain.

For this branch, prefer ordinary engineering style: small focused functions, explicit names, comments that explain a non-obvious reason, regression tests for behavior, and narrow commits. Avoid mass cosmetic rewrites or comments that narrate every line.
