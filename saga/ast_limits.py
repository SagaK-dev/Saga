from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as ast
from .errors import ParseLimitError


def ast_node_count(program: ast.Program) -> int:
    stack: list[object] = [program]
    count = 0
    while stack:
        value = stack.pop()
        if isinstance(value, ast.Node):
            count += 1
            if is_dataclass(value):
                for descriptor in fields(value):
                    stack.append(getattr(value, descriptor.name))
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return count


def validate_ast_size(program: ast.Program, filename: str, max_nodes: int | None = None) -> None:
    """Apply an optional host/deployment AST budget.

    Saga has no normative AST-node ceiling.  ``max_nodes`` is deliberately a
    caller-supplied implementation policy so existing language semantics remain
    unchanged when it is omitted.
    """
    if max_nodes is None:
        return
    count = ast_node_count(program)
    if count > max_nodes:
        raise ParseLimitError(
            f"ASTノード数が実行環境の予算を超えています ({count} > {max_nodes})",
            1,
            1,
            filename,
            "ResourceBudget.max_ast_nodes を見直すか、宣言や式を分割してください。これはSaga言語仕様の固定上限ではありません",
        )
