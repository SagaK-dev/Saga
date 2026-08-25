from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from . import ast_nodes as ast
from .api import compile_file, compile_source, parse_source
from .control_profile import validate_control_program
from .control_report_html import render_control_report_html
from .errors import SourceError
from .source_units import read_source_file


_CHECKS = (
    ("timing-contract", "周期と実行予算が静的に決まっている", {"SAGA-C480", "SAGA-C481", "SAGA-C482", "SAGA-C483"}),
    ("synchronous-control", "制御経路に async / await を持ち込まない", {"SAGA-C470", "SAGA-C473", "SAGA-C484"}),
    ("bounded-work", "ループと制御フローが有界である", {"SAGA-C477", "SAGA-C478", "SAGA-C486"}),
    ("no-dynamic-allocation", "周期内で動的なリストやクロージャを生成しない", {"SAGA-C471", "SAGA-C472"}),
    ("no-hidden-io", "制御周期の中にブロッキング・外部I/Oを隠さない", {"SAGA-C479", "SAGA-C492", "SAGA-C493"}),
    ("no-shared-mutation", "共有状態を制御関数から直接変更しない", {"SAGA-C487", "SAGA-C488"}),
    ("static-calls", "制御経路の呼び出し先を静的に追跡できる", {"SAGA-C489", "SAGA-C491"}),
    ("checked-helpers", "補助関数も @control_safe として検査される", {"SAGA-C490"}),
    ("no-recursion", "制御呼び出しグラフに再帰がない", {"SAGA-C485"}),
    ("no-dynamic-lifetime", "周期内で動的な資源・タスク寿命や例外制御を作らない", {"SAGA-C474", "SAGA-C475", "SAGA-C476"}),
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


def _program_scopes(program: ast.Program, prefix: str = "") -> Iterator[tuple[str, ast.Program]]:
    """Yield each lexical source-module scope exactly once.

    `load_program()` keeps namespaced source units as SourceModuleStmt nodes.
    Control validation is lexical too, so the report follows those same module
    boundaries instead of flattening unrelated helper names into one graph.
    """

    yield prefix, program
    for statement in program.statements:
        if not isinstance(statement, ast.SourceModuleStmt):
            continue
        bind = statement.bind_name or statement.name
        child_prefix = f"{prefix}{bind}."
        yield from _program_scopes(ast.Program(statement.statements), child_prefix)


def _control_entry(function: ast.FunctionDecl, qualified_name: str) -> dict[str, Any] | None:
    tick = _annotation(function, "control_tick")
    safe = _annotation(function, "control_safe")
    if tick is None and safe is None:
        return None

    entry: dict[str, Any] = {
        "name": qualified_name,
        "file": function.name.filename or "<input>",
        "line": function.name.line,
        "role": "tick" if tick is not None else "helper",
    }

    if tick is not None:
        entry["timing_contract"] = "legacy-untimed" if not tick.arguments else "invalid"
        if len(tick.arguments) == 2:
            rate_hz = _int_literal(tick.arguments[0])
            budget_us = _int_literal(tick.arguments[1])
            if (
                rate_hz is not None
                and budget_us is not None
                and 0 < rate_hz <= 1_000_000
                and budget_us > 0
                and budget_us * rate_hz <= 1_000_000
            ):
                period_us = 1_000_000 / rate_hz
                entry["timing_contract"] = "declared"
                entry["timing"] = {
                    "rate_hz": rate_hz,
                    "period_us": round(period_us, 3),
                    "budget_us": budget_us,
                    "budget_percent": round((budget_us / period_us) * 100, 1),
                    "headroom_us": round(period_us - budget_us, 3),
                }

    return entry


def _control_functions(program: ast.Program) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for prefix, scope in _program_scopes(program):
        for statement in scope.statements:
            if isinstance(statement, ast.FunctionDecl):
                entry = _control_entry(statement, f"{prefix}{statement.name.lexeme}")
                if entry is not None:
                    functions.append(entry)
                continue

            if isinstance(statement, ast.ClassDecl):
                class_name = statement.name.lexeme
                for method in statement.methods:
                    entry = _control_entry(method, f"{prefix}{class_name}.{method.name.lexeme}")
                    if entry is not None:
                        functions.append(entry)
    return functions


def _issue_dict(error: SourceError) -> dict[str, Any]:
    return {
        "code": error.diagnostic_id,
        "file": error.filename,
        "line": error.line,
        "column": error.column,
        "message": error.message,
        "hint": error.hint,
    }


def _control_issues(program: ast.Program) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for _prefix, scope in _program_scopes(program):
        for item in validate_control_program(scope):
            issues.append({
                "code": item.code,
                "file": item.token.filename or "<input>",
                "line": item.token.line,
                "column": item.token.column,
                "message": item.message,
                "hint": item.hint,
            })
    return issues


def _check_results(functions: list[dict[str, Any]], issues: list[dict[str, Any]]) -> list[dict[str, str]]:
    codes = {str(item["code"]) for item in issues}
    ticks = [item for item in functions if item["role"] == "tick"]
    declared_ticks = [item for item in ticks if item.get("timing_contract") == "declared"]
    results: list[dict[str, str]] = []

    for key, label, failure_codes in _CHECKS:
        failed = bool(codes & failure_codes)
        if key == "timing-contract":
            if failed:
                status = "fail"
            elif not ticks:
                status = "not-applicable"
            elif len(declared_ticks) == len(ticks):
                status = "pass"
            elif declared_ticks:
                status = "partial"
            else:
                status = "not-declared"
        elif failed:
            status = "fail"
        elif not functions:
            status = "not-applicable"
        else:
            status = "pass"
        results.append({"id": key, "label": label, "status": status})

    return results


def _timing_summary(functions: list[dict[str, Any]]) -> dict[str, Any]:
    ticks = [item for item in functions if item["role"] == "tick"]
    declared = [item for item in ticks if item.get("timing_contract") == "declared"]
    invalid = [item for item in ticks if item.get("timing_contract") == "invalid"]
    if not ticks:
        status = "not-applicable"
    elif invalid:
        status = "invalid" if len(invalid) == len(ticks) else "partial-invalid"
    elif len(declared) == len(ticks):
        status = "declared"
    elif declared:
        status = "partial"
    else:
        status = "not-declared"
    return {
        "status": status,
        "declared_ticks": len(declared),
        "invalid_ticks": len(invalid),
        "total_ticks": len(ticks),
    }


def build_control_report(
    program: ast.Program,
    filename: str = "<input>",
    *,
    language_check: dict[str, Any] | None = None,
    analysis_scope: str = "program",
    source_units: list[str] | None = None,
) -> dict[str, Any]:
    """Describe Saga's source-level control contract without overstating evidence."""

    functions = _control_functions(program)
    issues = _control_issues(program)
    language = language_check or {"status": "not-run", "diagnostic": None}

    if issues:
        verdict = "fail"
    elif language["status"] == "fail":
        verdict = "invalid"
    elif functions:
        verdict = "pass"
    else:
        verdict = "not-applicable"

    return {
        "schema": 2,
        "language": "Saga",
        "implementation_version": __version__,
        "file": filename,
        "verdict": verdict,
        "analysis_scope": analysis_scope,
        "source_units": source_units or [filename],
        "language_check": language,
        "timing_contract": _timing_summary(functions),
        "control_functions": functions,
        "checks": _check_results(functions, issues),
        "issues": issues,
        "boundary": (
            "PASS means no violation was found in Saga's supported source-level control-profile rules. "
            "It does not prove execution time. Target WCET measurement, physical HIL, emergency-stop/interlock "
            "behavior and certification remain separate evidence."
        ),
    }


def _parse_failure_report(error: SourceError, filename: str) -> dict[str, Any]:
    return {
        "schema": 2,
        "language": "Saga",
        "implementation_version": __version__,
        "file": filename,
        "verdict": "invalid",
        "analysis_scope": "none",
        "source_units": [filename],
        "language_check": {"status": "fail", "diagnostic": _issue_dict(error)},
        "timing_contract": {"status": "not-applicable", "declared_ticks": 0, "invalid_ticks": 0, "total_ticks": 0},
        "control_functions": [],
        "checks": [
            {"id": key, "label": label, "status": "not-applicable"}
            for key, label, _ in _CHECKS
        ],
        "issues": [],
        "boundary": (
            "The source could not be parsed, so no control-profile conclusion was made. "
            "Target WCET, physical HIL, emergency-stop/interlock behavior and certification are separate evidence."
        ),
    }


def analyze_control_source(source: str, filename: str = "<input>") -> dict[str, Any]:
    """Run the normal Saga language check and control analysis for one source unit."""

    try:
        program = parse_source(source, filename)
    except SourceError as error:
        return _parse_failure_report(error, filename)

    try:
        compile_source(source, filename)
    except SourceError as error:
        language_check = {"status": "fail", "diagnostic": _issue_dict(error)}
    else:
        language_check = {"status": "pass", "diagnostic": None}

    return build_control_report(
        program,
        filename,
        language_check=language_check,
        analysis_scope="single-source",
        source_units=[filename],
    )


def analyze_control_file(path: str | Path) -> dict[str, Any]:
    """Analyze a Saga entry file with the same source-unit loading used by `saga check`."""

    entry = Path(path).expanduser()
    filename = str(entry)
    try:
        loaded = compile_file(filename)
    except SourceError as error:
        # Keep the original project diagnostic. Parsing the entry still lets us
        # show any entry-local control declarations without pretending that the
        # complete dependency graph was successfully loaded.
        try:
            entry_program = parse_source(read_source_file(entry), filename)
        except SourceError:
            return _parse_failure_report(error, filename)

        diagnostic = _issue_dict(error)
        report = build_control_report(
            entry_program,
            filename,
            language_check={"status": "fail", "diagnostic": diagnostic},
            analysis_scope="entry-only-after-load-failure",
            source_units=[filename],
        )
        if str(error.diagnostic_id).startswith("SAGA-C"):
            if not any(
                item["code"] == diagnostic["code"]
                and item["file"] == diagnostic["file"]
                and item["line"] == diagnostic["line"]
                and item["column"] == diagnostic["column"]
                for item in report["issues"]
            ):
                report["issues"].append(diagnostic)
            report["checks"] = _check_results(report["control_functions"], report["issues"])
            report["verdict"] = "fail"
        else:
            report["verdict"] = "invalid"
        report["boundary"] = (
            "Full source-unit loading or language checking failed, so this is not a complete project control conclusion. "
            "Target WCET measurement, physical HIL, emergency-stop/interlock behavior and certification are separate evidence."
        )
        return report

    source_units = [str(item) for item in loaded.files]
    return build_control_report(
        loaded.program,
        str(loaded.entry),
        language_check={"status": "pass", "diagnostic": None},
        analysis_scope="loaded-program",
        source_units=source_units,
    )


def _check_marker(status: str) -> str:
    return {
        "pass": "ok",
        "fail": "fail",
        "partial": "note",
        "not-declared": "note",
        "not-applicable": "n/a",
    }.get(status, status)


def _location(item: dict[str, Any], report_file: str) -> str:
    source = str(item.get("file") or report_file)
    location = f"line {item['line']}:{item['column']}"
    if source not in {report_file, "<input>"}:
        location = f"{source}:{item['line']}:{item['column']}"
    return location


def render_control_report(report: dict[str, Any]) -> str:
    lines = ["Saga Control Report", "===================", f"File: {report['file']}"]
    language = report.get("language_check", {"status": "not-run"})
    lines.append(f"Language check: {str(language['status']).upper()}")
    if len(report.get("source_units", [])) > 1:
        lines.append(f"Source units: {len(report['source_units'])}")
    functions = report["control_functions"]

    if not functions:
        lines.extend(["", "No @control_tick or @control_safe functions were found."])
    else:
        lines.append(f"Control surface: {len(functions)} function(s)")
        for function in functions:
            role = "periodic tick" if function["role"] == "tick" else "checked helper"
            location = f"line {function['line']}"
            if function.get("file") not in {report["file"], "<input>"}:
                location = f"{function['file']}:{function['line']}"
            line = f"  - {function['name']} ({location}, {role})"
            timing = function.get("timing")
            if timing:
                line += (
                    f" — {timing['rate_hz']} Hz, {timing['budget_us']} us budget / "
                    f"{timing['period_us']} us period ({timing['budget_percent']}%)"
                )
            elif function["role"] == "tick":
                line += " — timing contract not declared"
            lines.append(line)

    lines.append("")
    verdict = report["verdict"]
    if verdict == "pass":
        lines.append("PASS — no supported source-level control-profile violation was found.")
    elif verdict == "fail":
        lines.append(f"FAIL — {len(report['issues'])} control-profile issue(s) found.")
    elif verdict == "invalid":
        lines.append("INVALID — the Saga language check failed; no passing control conclusion is reported.")
    else:
        lines.append("NOT APPLICABLE — no declared control surface was found.")

    if functions:
        for check in report["checks"]:
            lines.append(f"  [{_check_marker(check['status'])}] {check['label']}")

    if report["timing_contract"]["status"] in {"not-declared", "partial"}:
        summary = report["timing_contract"]
        lines.append(
            f"  [note] timing contract declared for {summary['declared_ticks']}/{summary['total_ticks']} tick(s); "
            "profile compatibility is not a frequency or budget claim."
        )

    if report["issues"]:
        lines.append("")
        for issue in report["issues"]:
            lines.append(f"  [{issue['code']}] {_location(issue, report['file'])}  {issue['message']}")
            if issue["hint"]:
                lines.append(f"      fix: {issue['hint']}")

    diagnostic = language.get("diagnostic")
    if language.get("status") == "fail" and diagnostic:
        duplicate = any(
            issue["code"] == diagnostic["code"]
            and issue.get("file") == diagnostic.get("file")
            and issue["line"] == diagnostic["line"]
            and issue["column"] == diagnostic["column"]
            for issue in report["issues"]
        )
        if not duplicate:
            lines.extend([
                "",
                f"  [{diagnostic['code']}] {_location(diagnostic, report['file'])}  {diagnostic['message']}",
            ])
            if diagnostic.get("hint"):
                lines.append(f"      fix: {diagnostic['hint']}")

    lines.extend(["", "Boundary:", f"  {report['boundary']}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="saga-control-report",
        description="Explain Saga's source-level machine/drone control contract.",
    )
    parser.add_argument("file", help="Saga entry source file to inspect")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    output.add_argument("--html", metavar="FILE", help="write a self-contained visual report")
    args = parser.parse_args(argv)

    report = analyze_control_file(args.file)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.html:
        destination = Path(args.html).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_control_report_html(report), encoding="utf-8")
        print(f"Wrote: {destination}")
    else:
        print(render_control_report(report))

    return 1 if report["verdict"] in {"fail", "invalid"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
