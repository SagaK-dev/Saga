from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import __version__
from . import ast_nodes as ast
from .api import parse_source
from .control_profile import validate_control_program
from .control_report_html import render_control_report_html
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
    """Describe the source-level machine-control contract without overstating it."""

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
