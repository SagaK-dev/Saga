from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Iterable

from . import ast_nodes as ast
from .tokens import Token


@dataclass(frozen=True, slots=True)
class ControlProfileViolation:
    token: Token
    code: str
    message: str
    hint: str


_FORBIDDEN_CALLS = {
    "task.await",
    "task.pool",
    "task.submit",
    "task.shutdown",
    "machine.can_recv",
    "machine.canfd_recv",
    "machine.ethercat_exchange",
    "machine.uart_read",
    "machine.spi_transfer",
    "machine.i2c_read",
    "machine.i2c_write_read",
    "machine.modbus_read_holding",
    "machine.modbus_read_input",
}
_FORBIDDEN_PREFIXES = ("net.", "process.", "database.", "cloud.")


def _annotation_names(fn: ast.FunctionDecl) -> set[str]:
    return {annotation.name.lexeme for annotation in fn.annotations}


def is_control_tick(fn: ast.FunctionDecl) -> bool:
    return "control_tick" in _annotation_names(fn)


def is_control_safe(fn: ast.FunctionDecl) -> bool:
    return "control_safe" in _annotation_names(fn)


def _callee_path(expr: ast.Expr) -> str | None:
    if isinstance(expr, ast.Variable):
        return expr.name.lexeme
    if isinstance(expr, ast.Member):
        parent = _callee_path(expr.target)
        if parent:
            return f"{parent}.{expr.name.lexeme}"
    return None


def _token(node: object, fallback: Token) -> Token:
    for attr in ("keyword", "token", "paren", "brace", "bracket", "name", "operator", "equals", "dot"):
        value = getattr(node, attr, None)
        if isinstance(value, Token):
            return value
    return fallback


def _iter_nodes(node: object) -> Iterable[object]:
    if node is None:
        return
    yield node
    if isinstance(node, (str, bytes, int, float, bool, Token)):
        return
    if isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)
        return
    if is_dataclass(node):
        for f in fields(node):
            # Annotations are compile-time metadata; their literal arguments do
            # not execute inside the periodic control function.
            if isinstance(node, ast.FunctionDecl) and f.name == "annotations":
                continue
            yield from _iter_nodes(getattr(node, f.name))


def validate_control_tick(fn: ast.FunctionDecl) -> list[ControlProfileViolation]:
    """Validate Saga's source-level MCU/RTOS control-tick profile.

    The profile guarantees a restricted *Saga source surface*: no explicit
    aggregate construction, async/task constructs, resource lifetime changes,
    exceptions, unbounded while loops, or known blocking receive/exchange calls
    inside a function annotated ``@control_tick``.  It is intentionally not a
    claim that the hosted Python/Go runtimes perform zero host allocations; a
    target backend must separately prove allocator-free lowering.
    """

    if not is_control_tick(fn):
        return []
    out: list[ControlProfileViolation] = []
    fallback = fn.name

    control_annotations = [a for a in fn.annotations if a.name.lexeme == "control_tick"]
    annotation = control_annotations[0] if control_annotations else None
    if annotation is not None and annotation.arguments:
        if len(annotation.arguments) != 2:
            out.append(ControlProfileViolation(
                annotation.name, "SAGA-C480",
                "@control_tick の周期契約は (rate_hz, budget_us) の2引数で指定してください",
                "例: @control_tick(20000, 35)",
            ))
        else:
            rate_expr, budget_expr = annotation.arguments
            rate = rate_expr.value if isinstance(rate_expr, ast.Literal) and isinstance(rate_expr.value, int) and not isinstance(rate_expr.value, bool) else None
            budget = budget_expr.value if isinstance(budget_expr, ast.Literal) and isinstance(budget_expr.value, int) and not isinstance(budget_expr.value, bool) else None
            if rate is None or rate <= 0 or rate > 1_000_000:
                out.append(ControlProfileViolation(
                    _token(rate_expr, annotation.name), "SAGA-C481",
                    "@control_tick rate_hz は1..1000000の整数リテラルにしてください",
                    "制御周期をコンパイル時に確定できる値で指定してください",
                ))
            if budget is None or budget <= 0:
                out.append(ControlProfileViolation(
                    _token(budget_expr, annotation.name), "SAGA-C482",
                    "@control_tick budget_us は正の整数リテラルにしてください",
                    "1周期内の実行予算をマイクロ秒で明示してください",
                ))
            elif rate is not None and rate > 0 and budget * rate > 1_000_000:
                out.append(ControlProfileViolation(
                    _token(budget_expr, annotation.name), "SAGA-C483",
                    "@control_tick budget_us が指定周期を超えています",
                    "budget_us * rate_hz <= 1000000 になるようにしてください",
                ))

    if fn.async_:
        out.append(ControlProfileViolation(
            fn.name, "SAGA-C470",
            "@control_tick 関数を async にすることはできません",
            "周期制御の外側で非同期処理を行い、制御tickには事前取得した状態だけを渡してください",
        ))

    root = fn.body if fn.body is not None else fn.expression_body
    for node in _iter_nodes(root):
        tok = _token(node, fallback)
        if isinstance(node, ast.ListLiteral):
            out.append(ControlProfileViolation(tok, "SAGA-C471", "@control_tick 内ではリストを生成できません", "固定状態オブジェクトまたはscalar getterを使ってください"))
        elif isinstance(node, (ast.ClosureExpr, ast.FunctionDecl)):
            out.append(ControlProfileViolation(tok, "SAGA-C472", "@control_tick 内ではクロージャ/ネスト関数を生成できません", "制御用ヘルパー関数をtickの外で定義してください"))
        elif isinstance(node, ast.AwaitExpr):
            out.append(ControlProfileViolation(tok, "SAGA-C473", "@control_tick 内では await を使えません", "非同期I/Oは周期制御の外側に分離してください"))
        elif isinstance(node, ast.MoveExpr):
            out.append(ControlProfileViolation(tok, "SAGA-C474", "@control_tick 内ではresource moveを行えません", "デバイス資源の所有権は制御開始前に確定してください"))
        elif isinstance(node, (ast.UsingStmt, ast.TaskGroupStmt, ast.DeferStmt)):
            out.append(ControlProfileViolation(tok, "SAGA-C475", "@control_tick 内では動的な寿命/タスク構造を使えません", "resource/task setupは制御tickの外側で行ってください"))
        elif isinstance(node, (ast.TryStmt, ast.ThrowStmt)):
            out.append(ControlProfileViolation(tok, "SAGA-C476", "@control_tick 内では例外制御を使えません", "boundedなstatus/resultを制御状態として扱ってください"))
        elif isinstance(node, ast.WhileStmt):
            out.append(ControlProfileViolation(tok, "SAGA-C477", "@control_tick 内ではwhileループを使えません", "コンパイル時に上限が分かるrange forまたは固定回数アルゴリズムを使ってください"))
        elif isinstance(node, ast.ForStmt):
            rng = node.iterable
            bounded = isinstance(rng, ast.RangeExpr) and isinstance(rng.start, ast.Literal) and isinstance(rng.end, ast.Literal) and isinstance(rng.start.value, int) and isinstance(rng.end.value, int)
            if not bounded:
                out.append(ControlProfileViolation(tok, "SAGA-C478", "@control_tick のforループは整数リテラルrangeで上限を固定する必要があります", "例: for i in 0..7 { ... }"))
        elif isinstance(node, ast.Call):
            name = _callee_path(node.callee)
            if name and (name in _FORBIDDEN_CALLS or name.startswith(_FORBIDDEN_PREFIXES)):
                out.append(ControlProfileViolation(tok, "SAGA-C479", f"@control_tick 内で '{name}' は使用できません", "ブロッキング/外部I/Oは周期境界の外で行い、timestamped stateをtickへ渡してください"))
    return out


# 0.50 Production-GA control-surface hardening.
# A @control_tick function may call user code only when that code is explicitly
# marked @control_safe (or is itself @control_tick). This closes the 0.49 hole
# where an apparently bounded tick could hide blocking I/O/allocation behind a
# helper function.
_CONTROL_SAFE_BUILTINS = {
    "abs", "min", "max", "floor", "ceil", "round", "int", "decimal",
    "is_ok", "is_err", "unwrap_result_or", "is_some", "is_none", "unwrap_or",
}

_CONTROL_UNSAFE_MACHINE_PREFIXES = (
    "can_", "canfd_", "ethercat_", "i2c_", "spi_", "uart_", "modbus_", "pwm_",
    "iio_", "motor_write", "motor_stop", "servo_write", "plc_commit", "plc_scan",
    "cycle_wait", "cyclic_clock", "monotonic_ns", "watchdog_",
)

_CONTROL_SAFE_MACHINE_EXACT = {
    "machine.clarke", "machine.park", "machine.inverse_park", "machine.svpwm",
    "machine.pid_step", "machine.pid_reset", "machine.pid_integral_limits",
    "machine.pid2_step", "machine.pid2_reset", "machine.filter_step", "machine.filter_reset",
    "machine.alpha_beta_step", "machine.alpha_beta_reset", "machine.foc_step", "machine.foc_reset",
    "machine.foc_duty", "machine.foc_id", "machine.foc_iq", "machine.foc_vd", "machine.foc_vq",
    "machine.fast_state_predict", "machine.fast_state_command", "machine.state_space_predict",
    "machine.state_space_command", "machine.kalman_predict", "machine.kalman_update",
    "machine.rls2_update", "machine.rls2_error", "machine.rls2_theta0", "machine.rls2_theta1",
    "machine.mpc2_step", "machine.mpc2_reset", "machine.disturbance_step", "machine.disturbance_reset",
    "machine.friction_compensation", "machine.axis_step", "machine.axis_done",
    "machine.axis_planned_position", "machine.axis_sync_correction", "machine.axis_sync_error",
    "machine.axis_sync_ok", "machine.profile_step", "machine.profile_done", "machine.profile_velocity",
    "machine.s_curve_step", "machine.s_curve_done", "machine.s_curve_velocity", "machine.s_curve_acceleration",
    "machine.actuator_step", "machine.actuator_set", "machine.actuator_set_all", "machine.actuator_zero",
    "machine.control_guard_begin", "machine.control_guard_end", "machine.control_guard_ok",
    "machine.budget_begin", "machine.budget_end",
}


def _literal_int(expr: object) -> int | None:
    if isinstance(expr, ast.Literal) and isinstance(expr.value, int) and not isinstance(expr.value, bool):
        return expr.value
    return None


def _control_local_names(fn: ast.FunctionDecl) -> set[str]:
    names = {p.name.lexeme for p in fn.parameters}
    root = fn.body if fn.body is not None else fn.expression_body
    for node in _iter_nodes(root):
        if isinstance(node, ast.VarDecl):
            names.add(node.name.lexeme)
        elif isinstance(node, ast.ForStmt):
            names.add(node.name.lexeme)
    return names


def _control_surface_violations(fn: ast.FunctionDecl, functions: dict[str, ast.FunctionDecl]) -> tuple[list[ControlProfileViolation], set[str]]:
    """Return local violations and direct user-function callees for GA control code."""
    out: list[ControlProfileViolation] = []
    callees: set[str] = set()
    fallback = fn.name
    locals_ = _control_local_names(fn)
    root = fn.body if fn.body is not None else fn.expression_body
    for node in _iter_nodes(root):
        tok = _token(node, fallback)
        if isinstance(node, ast.ForStmt):
            rng = node.iterable
            if isinstance(rng, ast.RangeExpr):
                start, end = _literal_int(rng.start), _literal_int(rng.end)
                if start is not None and end is not None and abs(end - start) > 4096:
                    out.append(ControlProfileViolation(tok, "SAGA-C486", "制御用forループの静的上限が4096反復を超えています", "処理を分割するか、対象ごとのWCET根拠を持つ専用primitiveを使用してください"))
        elif isinstance(node, ast.Assign):
            if isinstance(node.target, ast.Variable):
                if node.target.name.lexeme not in locals_:
                    out.append(ControlProfileViolation(node.target.name, "SAGA-C487", "制御関数から共有/外部変数を直接変更できません", "状態は引数または専用のdeterministic machine primitiveとして渡してください"))
            elif isinstance(node.target, ast.Member):
                out.append(ControlProfileViolation(tok, "SAGA-C488", "制御関数から任意オブジェクトのフィールドを直接変更できません", "共有可変状態を避け、検証済みmachine primitiveを使用してください"))
        elif isinstance(node, ast.Call):
            name = _callee_path(node.callee)
            if not name:
                out.append(ControlProfileViolation(tok, "SAGA-C489", "制御関数では間接/動的呼び出しを使用できません", "呼び出し先を静的に名前解決できる関数へ固定してください"))
                continue
            if name in functions:
                callees.add(name)
                target = functions[name]
                if not (is_control_safe(target) or is_control_tick(target)):
                    out.append(ControlProfileViolation(tok, "SAGA-C490", f"制御関数から未検証のユーザー関数 '{name}' を呼べません", "呼び出し先を @control_safe として検証するか、tick外で計算してください"))
                continue
            if "." not in name:
                if name not in _CONTROL_SAFE_BUILTINS:
                    out.append(ControlProfileViolation(tok, "SAGA-C491", f"制御関数からbuiltin '{name}' を呼べません", "Production GA control profileでは明示的に許可された決定的builtinだけを使用してください"))
                continue
            if name.startswith("machine."):
                leaf = name.split(".", 1)[1]
                if name not in _CONTROL_SAFE_MACHINE_EXACT or leaf.startswith(_CONTROL_UNSAFE_MACHINE_PREFIXES):
                    out.append(ControlProfileViolation(tok, "SAGA-C492", f"Production GA制御領域では '{name}' は許可されていません", "raw/blocking/time-dependent I/Oをtick外へ分離し、timestamped inputとcommand outputを渡してください"))
                continue
            out.append(ControlProfileViolation(tok, "SAGA-C493", f"Production GA制御領域では外部モジュール呼び出し '{name}' は許可されていません", "I/O・network・vision処理は周期制御の外側へ分離してください"))
    return out, callees


def validate_control_program(program: ast.Program) -> list[ControlProfileViolation]:
    """Whole-program Production-GA validation for control-critical functions.

    This includes the local ``@control_tick`` restrictions as well as transitive
    helper, recursion, indirect-call, shared-mutation and hidden-I/O checks.
    Older non-production code remains source-compatible unless it opts into
    ``@control_safe`` or ``@control_tick``.
    """
    functions = {s.name.lexeme: s for s in program.statements if isinstance(s, ast.FunctionDecl)}
    graph: dict[str, set[str]] = {}
    out: list[ControlProfileViolation] = []
    for name, fn in functions.items():
        if not (is_control_tick(fn) or is_control_safe(fn)):
            continue
        if is_control_tick(fn):
            out.extend(validate_control_tick(fn))
        if is_control_safe(fn) and fn.async_:
            out.append(ControlProfileViolation(fn.name, "SAGA-C484", "@control_safe 関数を async にすることはできません", "周期制御から呼ばれるhelperは同期・有界・決定的にしてください"))
        # Reuse the proven 0.47 restricted source surface for helpers too.
        if is_control_safe(fn) and not is_control_tick(fn):
            shadow = ast.FunctionDecl(
                fn.keyword, fn.name, fn.parameters, fn.return_type, fn.body, fn.expression_body,
                fn.type_params, [ast.Annotation(fn.name, [])], fn.abstract, fn.override, fn.visibility, fn.async_,
            )
            # The synthetic annotation name is rewritten only for validation.
            shadow.annotations[0].name = Token(fn.name.kind, "control_tick", None, fn.name.line, fn.name.column, fn.name.filename)
            out.extend(validate_control_tick(shadow))
        local, callees = _control_surface_violations(fn, functions)
        out.extend(local)
        graph[name] = {c for c in callees if c in functions and (is_control_safe(functions[c]) or is_control_tick(functions[c]))}

    visiting: set[str] = set()
    visited: set[str] = set()
    def dfs(name: str, path: list[str]) -> None:
        if name in visiting:
            cycle = path[path.index(name):] + [name] if name in path else path + [name]
            fn = functions[name]
            out.append(ControlProfileViolation(fn.name, "SAGA-C485", "Production GA制御呼び出しグラフに再帰があります: " + " -> ".join(cycle), "再帰を固定上限ループまたは反復primitiveへ変換してください"))
            return
        if name in visited:
            return
        visiting.add(name)
        for child in sorted(graph.get(name, ())):
            dfs(child, path + [name])
        visiting.remove(name)
        visited.add(name)
    for name in sorted(graph):
        dfs(name, [])
    return out
