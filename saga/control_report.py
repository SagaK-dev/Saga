from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from typing import Any

from . import __version__
from . import ast_nodes as ast
from .api import parse_source
from .control_profile import validate_control_program
from .source_units import read_source_file


_CHECKS = (
    ("timing-contract", "周期と実行予算が静的に決まっている"),
    ("bounded-work", "ループと制御フローが有界である"),
    ("no-hidden-io", "制御周期の中にブロッキングI/Oを隠さない"),
    ("no-shared-mutation", "共有状態を制御関数から直接変更しない"),
    ("static-calls", "制御経路の呼び出し先を静的に追跡できる"),
    ("checked-helpers", "補助関数も @control_safe として検査される"),
    ("no-recursion", "制御呼び出しグラフに再帰がない"),
    ("no-dynamic-lifetime", "周期内で動的な資源・タスク寿命を作らない"),
)


def _annotation(function: ast.FunctionDecl, name: str) -> ast.Annotation | None:
    for item in function.annotations:
        if item.name.lexeme == name:
            return item
    return None


def _int_literal(value: ast.Expr) -> int | None:
    if isinstance(value, ast.Literal) and isinstance(value.value, int) and not isinstance(value.value, bool):
        return value.value
    return None


def _control_functions(program: ast.Program) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for statement in program.statements:
        if not isinstance(statement, ast.FunctionDecl):
            continue

        tick = _annotation(statement, "control_tick")
        safe = _annotation(statement, "control_safe")
        if tick is None and safe is None:
            continue

        entry: dict[str, Any] = {
            "name": statement.name.lexeme,
            "line": statement.name.line,
            "role": "tick" if tick is not None else "helper",
        }

        if tick is not None and len(tick.arguments) == 2:
            rate_hz = _int_literal(tick.arguments[0])
            budget_us = _int_literal(tick.arguments[1])
            if rate_hz and budget_us:
                period_us = 1_000_000 / rate_hz
                entry["timing"] = {
                    "rate_hz": rate_hz,
                    "period_us": round(period_us, 3),
                    "budget_us": budget_us,
                    "budget_percent": round((budget_us / period_us) * 100, 1),
                    "headroom_us": round(period_us - budget_us, 3),
                }

        functions.append(entry)
    return functions


def build_control_report(program: ast.Program, filename: str = "<input>") -> dict[str, Any]:
    """Build a judge- and developer-friendly view of Saga's control profile.

    This report describes the source-level control contract. It is deliberately
    separate from target WCET, HIL and physical-safety evidence so the tool does
    not turn a successful static check into a stronger claim than it can prove.
    """

    functions = _control_functions(program)
    violations = validate_control_program(program)

    issues = [
        {
            "code": item.code,
            "line": item.token.line,
            "column": item.token.column,
            "message": item.message,
            "hint": item.hint,
        }
        for item in violations
    ]

    if not functions:
        verdict = "not-applicable"
    else:
        verdict = "pass" if not issues else "fail"

    return {
        "schema": 1,
        "language": "Saga",
        "implementation_version": __version__,
        "file": filename,
        "verdict": verdict,
        "control_functions": functions,
        "checks": [{"id": key, "label": label} for key, label in _CHECKS],
        "issues": issues,
        "boundary": (
            "Source-level control-profile analysis only. "
            "Target WCET, physical HIL, emergency-stop/interlock behavior and certification are separate evidence."
        ),
    }


def render_control_report(report: dict[str, Any]) -> str:
    lines = ["Saga Control Report", "===================", f"File: {report['file']}"]
    functions = report["control_functions"]

    if not functions:
        lines.extend(["", "No @control_tick or @control_safe functions were found."])
        return "\n".join(lines)

    lines.append(f"Control surface: {len(functions)} function(s)")
    for function in functions:
        role = "periodic tick" if function["role"] == "tick" else "checked helper"
        line = f"  - {function['name']} (line {function['line']}, {role})"
        timing = function.get("timing")
        if timing:
            line += (
                f" — {timing['rate_hz']} Hz, {timing['budget_us']} us budget / "
                f"{timing['period_us']} us period ({timing['budget_percent']}%)"
            )
        lines.append(line)

    lines.append("")
    if report["verdict"] == "pass":
        lines.append("PASS — the declared control surface satisfies Saga's source-level control profile.")
        for check in report["checks"]:
            lines.append(f"  [ok] {check['label']}")
    else:
        issues = report["issues"]
        lines.append(f"FAIL — {len(issues)} control-profile issue(s) found.")
        for issue in issues:
            lines.append(f"  [{issue['code']}] line {issue['line']}:{issue['column']}  {issue['message']}")
            if issue["hint"]:
                lines.append(f"      fix: {issue['hint']}")

    lines.extend(["", "Boundary:", f"  {report['boundary']}"])
    return "\n".join(lines)


def _function_card(function: dict[str, Any]) -> str:
    name = escape(str(function["name"]))
    line = int(function["line"])
    role = "周期制御" if function["role"] == "tick" else "検査済みヘルパー"
    timing = function.get("timing")
    timing_html = ""
    if timing:
        percent = max(0.0, min(float(timing["budget_percent"]), 100.0))
        timing_html = f"""
        <div class="timing">
          <div><strong>{timing['rate_hz']:,} Hz</strong><span>周期 {timing['period_us']} µs</span></div>
          <div><strong>{timing['budget_us']} µs</strong><span>実行予算 / 余白 {timing['headroom_us']} µs</span></div>
        </div>
        <div class="meter" aria-label="周期に対する実行予算 {timing['budget_percent']}%">
          <span style="width:{percent:.1f}%"></span>
        </div>
        <p class="meter-label">1周期の {timing['budget_percent']}% を実行予算として宣言</p>
        """
    return f"""
    <article class="card">
      <div class="card-title"><code>{name}</code><span>{role} · line {line}</span></div>
      {timing_html}
    </article>
    """


def render_control_report_html(report: dict[str, Any]) -> str:
    verdict = report["verdict"]
    status_text = {
        "pass": "CONTROL PROFILE PASS",
        "fail": "REVIEW NEEDED",
        "not-applicable": "NO CONTROL SURFACE",
    }[verdict]
    status_class = {"pass": "pass", "fail": "fail", "not-applicable": "na"}[verdict]

    function_cards = "".join(_function_card(item) for item in report["control_functions"])
    if not function_cards:
        function_cards = '<article class="card"><p>@control_tick / @control_safe は見つかりませんでした。</p></article>'

    check_items = "".join(
        f'<li><span aria-hidden="true">✓</span>{escape(str(item["label"]))}</li>'
        for item in report["checks"]
    )

    if report["issues"]:
        issue_items = "".join(
            f"""
            <article class="issue">
              <div><code>{escape(str(item['code']))}</code><span>line {int(item['line'])}:{int(item['column'])}</span></div>
              <p>{escape(str(item['message']))}</p>
              <small>修正案: {escape(str(item['hint']))}</small>
            </article>
            """
            for item in report["issues"]
        )
    else:
        issue_items = '<p class="quiet">検出された制御プロファイル違反はありません。</p>'

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saga Control Report</title>
<style>
:root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f4f6f8; color: #18212f; }}
main {{ width: min(1040px, calc(100% - 32px)); margin: 32px auto 56px; }}
.hero {{ padding: 32px; border-radius: 24px; background: #111827; color: white; box-shadow: 0 18px 45px rgba(15, 23, 42, .12); }}
.eyebrow {{ margin: 0 0 8px; font-size: 13px; letter-spacing: .14em; text-transform: uppercase; opacity: .72; }}
h1 {{ margin: 0; font-size: clamp(34px, 7vw, 68px); line-height: .98; letter-spacing: -.045em; }}
.hero-row {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 24px; }}
.status {{ display: inline-flex; padding: 8px 12px; border-radius: 999px; font-weight: 800; font-size: 13px; letter-spacing: .04em; }}
.status.pass {{ background: #d1fae5; color: #065f46; }}
.status.fail {{ background: #fee2e2; color: #991b1b; }}
.status.na {{ background: #e5e7eb; color: #374151; }}
.file {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; opacity: .78; overflow-wrap: anywhere; }}
section {{ margin-top: 28px; }}
section > h2 {{ margin: 0 0 12px; font-size: 18px; letter-spacing: -.02em; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
.card, .issue, .checks {{ background: white; border: 1px solid #e5e7eb; border-radius: 18px; padding: 20px; box-shadow: 0 8px 24px rgba(15, 23, 42, .045); }}
.card-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
.card-title code {{ font-size: 18px; font-weight: 800; }}
.card-title span {{ color: #64748b; font-size: 12px; }}
.timing {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 22px; }}
.timing div {{ display: flex; flex-direction: column; gap: 3px; }}
.timing strong {{ font-size: 22px; }}
.timing span, .meter-label, .quiet {{ color: #64748b; font-size: 12px; }}
.meter {{ height: 10px; margin-top: 18px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
.meter span {{ display: block; height: 100%; background: #2563eb; border-radius: inherit; }}
.meter-label {{ margin: 7px 0 0; }}
.checks {{ list-style: none; margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px 18px; }}
.checks li {{ display: flex; gap: 10px; align-items: start; }}
.checks li span {{ display: inline-grid; place-items: center; width: 22px; height: 22px; flex: 0 0 22px; border-radius: 50%; background: #d1fae5; color: #065f46; font-weight: 900; }}
.issue {{ margin-bottom: 10px; border-left: 5px solid #dc2626; }}
.issue div {{ display: flex; gap: 10px; align-items: baseline; }}
.issue code {{ font-weight: 800; color: #991b1b; }}
.issue div span, .issue small {{ color: #64748b; }}
.issue p {{ margin: 10px 0 8px; }}
.boundary {{ padding: 16px 18px; border-radius: 14px; background: #eef2ff; color: #3730a3; font-size: 13px; line-height: 1.55; }}
footer {{ margin-top: 24px; color: #64748b; font-size: 12px; }}
@media (max-width: 560px) {{ .hero {{ padding: 24px; }} .timing {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  <header class="hero">
    <p class="eyebrow">Saga {escape(str(report['implementation_version']))} · explainable machine control</p>
    <h1>Control<br>Report</h1>
    <div class="hero-row">
      <span class="status {status_class}">{status_text}</span>
      <span class="file">{escape(str(report['file']))}</span>
    </div>
  </header>

  <section>
    <h2>制御サーフェス</h2>
    <div class="grid">{function_cards}</div>
  </section>

  <section>
    <h2>静的に確認すること</h2>
    <ul class="checks">{check_items}</ul>
  </section>

  <section>
    <h2>検出結果</h2>
    {issue_items}
  </section>

  <section>
    <h2>判定の境界</h2>
    <div class="boundary">{escape(str(report['boundary']))}</div>
  </section>

  <footer>Generated locally by Saga. Source text, keystrokes, credentials and telemetry are not uploaded by this report command.</footer>
</main>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="saga-control-report",
        description="Explain Saga's source-level machine/drone control contract.",
    )
    parser.add_argument("file", help="Saga source file to inspect")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    output.add_argument("--html", metavar="FILE", help="write a self-contained visual report")
    args = parser.parse_args(argv)

    path = Path(args.file).expanduser()
    source = read_source_file(path)
    program = parse_source(source, str(path))
    report = build_control_report(program, str(path))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.html:
        destination = Path(args.html).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_control_report_html(report), encoding="utf-8")
        print(f"Wrote: {destination}")
    else:
        print(render_control_report(report))

    return 1 if report["verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
