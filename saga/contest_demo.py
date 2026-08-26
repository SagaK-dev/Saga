from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .control_report import analyze_control_source, render_control_report
from .control_report_html import render_control_report_html


SAFE_SOURCE = """use machine

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

print(current_tick(0.6))
"""

UNSAFE_SOURCE = """use machine

@control_tick(1000, 200)
fn current_tick(error: decimal) -> decimal {
    let sampled_at = machine.monotonic_ns()
    return error * 0.5
}
"""


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _issue_summary(report: dict[str, Any]) -> str:
    issues = report.get("issues") or []
    if not issues:
        return "No control-profile issue was reported."
    first = issues[0]
    location = f"line {first.get('line', '?')}:{first.get('column', '?')}"
    return f"{first.get('code', 'SAGA-C?')} at {location}: {first.get('message', '')}"


def _report_card(title: str, report: dict[str, Any], report_name: str) -> str:
    verdict = str(report.get("verdict", "unknown")).upper()
    timing = report.get("timing_contract") or {}
    functions = report.get("control_functions") or []
    ticks = [item for item in functions if item.get("role") == "tick"]
    timing_line = "No periodic timing contract"
    if ticks and ticks[0].get("timing"):
        data = ticks[0]["timing"]
        timing_line = (
            f"{data['rate_hz']} Hz · {data['period_us']} µs period · "
            f"{data['budget_us']} µs budget · {data['budget_percent']}% used"
        )
    issue = _issue_summary(report)
    return f"""
    <section class="card">
      <div class="eyebrow">{html.escape(title)}</div>
      <h2>{html.escape(verdict)}</h2>
      <p class="timing">{html.escape(timing_line)}</p>
      <p>{html.escape(issue)}</p>
      <a href="{html.escape(report_name)}">Open full Control Report</a>
    </section>
    """


def _render_index(safe_report: dict[str, Any], unsafe_report: dict[str, Any]) -> str:
    safe_card = _report_card("Readable periodic control path", safe_report, "safe-report.html")
    unsafe_card = _report_card("One risky change: time-dependent host call", unsafe_report, "unsafe-report.html")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Saga contest demo</title>
  <style>
    :root {{ font-family: Inter, ui-sans-serif, system-ui, sans-serif; color-scheme: light dark; }}
    body {{ max-width: 1040px; margin: 0 auto; padding: 40px 20px 64px; line-height: 1.55; }}
    header {{ margin-bottom: 28px; }}
    h1 {{ font-size: clamp(2rem, 6vw, 4.25rem); line-height: 1; margin: 0 0 18px; }}
    .lead {{ max-width: 760px; font-size: 1.1rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }}
    .card {{ border: 1px solid currentColor; border-radius: 18px; padding: 22px; }}
    .card h2 {{ margin: 8px 0; font-size: 2rem; }}
    .eyebrow {{ font-size: .82rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .timing {{ font-weight: 650; }}
    code {{ padding: .1rem .35rem; border: 1px solid currentColor; border-radius: .35rem; }}
    .steps {{ margin-top: 30px; padding-top: 8px; }}
    li {{ margin: .55rem 0; }}
    footer {{ margin-top: 34px; font-size: .92rem; opacity: .82; }}
  </style>
</head>
<body>
  <header>
    <div class="eyebrow">Saga {html.escape(__version__)} · contest demo</div>
    <h1>Readable control code, explainable control boundaries.</h1>
    <p class="lead">The two programs differ by one control-path behavior. Saga analyzes both with the real language checker and control profile, then explains why one is accepted and the other needs review.</p>
  </header>
  <main>
    <div class="grid">{safe_card}{unsafe_card}</div>
    <section class="steps">
      <h2>What the judge should notice</h2>
      <ol>
        <li>The timing contract is visible in source with <code>@control_tick(rate_hz, budget_us)</code>.</li>
        <li>Helpers called from the periodic path are checked transitively with <code>@control_safe</code>.</li>
        <li>The unsafe example is not a prerecorded failure: the same analyzer emits the diagnostic and source location.</li>
        <li>A PASS is source-level evidence only. It is not WCET, physical HIL, E-stop/STO, airworthiness, or safety certification evidence.</li>
      </ol>
    </section>
  </main>
  <footer>Generated locally by <code>saga-contest-demo</code>. No network connection or physical device is required.</footer>
</body>
</html>
"""


def run_demo(output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir).expanduser()
    output.mkdir(parents=True, exist_ok=True)

    safe_source_path = output / "diff_safe_control.saga"
    unsafe_source_path = output / "diff_unsafe_control.saga"
    _write_text(safe_source_path, SAFE_SOURCE)
    _write_text(unsafe_source_path, UNSAFE_SOURCE)

    safe_report = analyze_control_source(SAFE_SOURCE, str(safe_source_path))
    unsafe_report = analyze_control_source(UNSAFE_SOURCE, str(unsafe_source_path))

    _write_text(output / "safe-report.txt", render_control_report(safe_report) + "\n")
    _write_text(output / "unsafe-report.txt", render_control_report(unsafe_report) + "\n")
    _write_text(output / "safe-report.json", json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_text(output / "unsafe-report.json", json.dumps(unsafe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_text(output / "safe-report.html", render_control_report_html(safe_report))
    _write_text(output / "unsafe-report.html", render_control_report_html(unsafe_report))
    _write_text(output / "index.html", _render_index(safe_report, unsafe_report))

    unsafe_codes = [str(item.get("code") or "") for item in unsafe_report.get("issues", [])]
    valid = (
        safe_report.get("verdict") == "pass"
        and unsafe_report.get("verdict") == "fail"
        and any(code.startswith("SAGA-C") for code in unsafe_codes)
    )

    manifest = {
        "schema": 1,
        "demo": "safe-vs-unsafe-control",
        "language": "Saga",
        "implementation_version": __version__,
        "valid": valid,
        "expected": {"safe": "pass", "unsafe": "fail"},
        "observed": {
            "safe": safe_report.get("verdict"),
            "unsafe": unsafe_report.get("verdict"),
            "unsafe_diagnostics": unsafe_codes,
        },
        "artifacts": [
            "index.html",
            "diff_safe_control.saga",
            "diff_unsafe_control.saga",
            "safe-report.txt",
            "unsafe-report.txt",
            "safe-report.json",
            "unsafe-report.json",
            "safe-report.html",
            "unsafe-report.html",
        ],
        "boundary": (
            "This demo proves the expected source-analysis contrast only. It does not provide target WCET, "
            "physical HIL, emergency-stop/interlock, airworthiness, or functional-safety certification evidence."
        ),
    }
    _write_text(output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="saga-contest-demo",
        description="Generate the reproducible Saga safe-vs-unsafe machine-control contest demo.",
    )
    parser.add_argument("--output", default="build/contest-demo", help="artifact directory")
    parser.add_argument("--json", action="store_true", help="print the demo manifest as JSON")
    args = parser.parse_args(argv)

    manifest = run_demo(args.output)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Saga {__version__} contest demo")
        print(f"safe:   {str(manifest['observed']['safe']).upper()}")
        print(f"unsafe: {str(manifest['observed']['unsafe']).upper()}")
        if manifest["observed"]["unsafe_diagnostics"]:
            print("diagnostic: " + ", ".join(manifest["observed"]["unsafe_diagnostics"]))
        print(f"open: {Path(args.output).expanduser() / 'index.html'}")

    return 0 if manifest["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
