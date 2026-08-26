# Saga contest demo

This is the shortest reproducible path for showing Saga as a contest work. The story is intentionally narrower than the full language roadmap: **readable machine-control code + an explainable source-level control boundary**.

## 1. Install

Saga requires Python 3.13 or later.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

The Core CI also builds both a wheel and source distribution, installs each into a clean virtual environment outside the repository checkout, and smoke-tests the installed commands.

## 2. Generate the judge-facing demo

```bash
saga-contest-demo --output build/contest-demo
```

Open:

```text
build/contest-demo/index.html
```

The command generates the source files, terminal/JSON/HTML Control Reports, and a machine-readable `manifest.json`.

Expected result:

```text
single change: VERIFIED
safe:   PASS
unsafe: FAIL
```

The command exits successfully only when all three conditions remain true: the two sources differ by exactly one expected added line, the safe program passes, and the unsafe program fails with a real `SAGA-C...` control diagnostic.

## 3. What changed between the two programs?

Both programs keep the same 20 kHz periodic path, the same 35 µs source-level budget, the same checked `@control_safe` helper, the same return expression, and the same output.

The unsafe program adds exactly one line inside the periodic control path:

```saga
let sampled_at = machine.monotonic_ns()
```

Removing that line reconstructs the safe source byte-for-byte. The point of the demo is not that Saga can print a red screen; it is that the normal file-based language/control analysis follows the control boundary and explains why that one added host-time operation is rejected, with a stable diagnostic and source location.

## 4. Two-minute presentation flow

**0:00-0:20 — problem**

Show the safe source. Explain that machine/robot/drone control code should remain readable, but timing-sensitive paths should not silently accumulate hidden work.

**0:20-0:50 — safe path**

Open `safe-report.html`. Point out the declared rate, period, budget, checked helper, and PASS result.

**0:50-1:20 — one risky change**

Show the comparison section in `index.html`, then the unsafe source and `unsafe-report.html`. Point to the single added line and `SAGA-C...` diagnostic. Explain the suggested design: sample time or perform raw/external I/O outside the periodic path, then pass prepared state into the tick.

**1:20-1:45 — prove it is language behavior**

Run:

```bash
saga check build/contest-demo/diff_safe_control.saga
saga-control-report build/contest-demo/diff_safe_control.saga
```

Then show `tests/test_contest_demo_054.py` and the green Core CI. The regression suite proves that removing the one risky line from the unsafe source reconstructs the safe source exactly, and that the generated demo sources match the checked-in contest examples.

**1:45-2:00 — boundary and future**

State the evidence boundary clearly: a passing source-level report is not target WCET evidence, physical HIL evidence, E-stop/STO/interlock validation, airworthiness evidence, or a functional-safety certificate. Future work can add target-specific measurements without changing that distinction.

## 5. Submission-day checklist

```bash
python -m unittest tests.test_contest_demo_054 tests.test_control_report_053 tests.test_control_ga_050
saga-contest-demo --output build/contest-demo
saga check build/contest-demo/diff_safe_control.saga
saga-control-report build/contest-demo/diff_safe_control.saga
```

Also confirm:

- Core CI is green on the exact commit being submitted.
- The demo prints `single change: VERIFIED` before the PASS/FAIL contrast.
- `build/contest-demo/index.html` opens correctly at the presentation resolution.
- The execution video uses the same commit/source as the submission.
- The author can explain `@control_tick`, `@control_safe`, the one rejected operation, and one design trade-off without reading a script.
- No claim goes beyond the evidence actually produced by the repository.

## Positioning

Do not present Saga as "a language that can do everything." Present the contest work as:

> Saga makes machine-control code readable while letting the language explain when a periodic control path contains work that should not be there.

That gives the judge one problem, one language idea, one controlled source change, and one reproducible demonstration.
