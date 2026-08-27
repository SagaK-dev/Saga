from __future__ import annotations

from collections.abc import Callable
import copy

from .checker import TypeChecker
from .ast_limits import ast_node_count, validate_ast_size
from .errors import ParseLimitError, RuntimeResourceError, TypeLimitError
from .interpreter import Interpreter
from .lexer import Lexer
from .limits import (
    ResourceBudget,
    bounded_output,
    check_source_bytes,
    check_token_count,
    effective_step_limit,
    source_size_bytes,
)
from .native import Capabilities
from .parser import Parser
from .source_units import LoadedProgram, load_program
from .resource_runtime import adaptive_recursion_capacity


def _ast_budget(resource_budget: ResourceBudget | None) -> int | None:
    return resource_budget.max_ast_nodes if resource_budget is not None else None


def parse_source(
    source: str,
    filename: str = "<input>",
    *,
    resource_budget: ResourceBudget | None = None,
):
    check_source_bytes(source_size_bytes(source), filename, resource_budget)
    try:
        tokens = Lexer(source, filename).scan_tokens()
        check_token_count(len(tokens), filename, resource_budget)
        with adaptive_recursion_capacity(len(tokens)):
            program = Parser(tokens, filename).parse()
        validate_ast_size(program, filename, _ast_budget(resource_budget))
        return program
    except RecursionError as exc:
        raise ParseLimitError(
            "ホストの解析スタックを使い切ったため解析を継続できません",
            1, 1, filename,
            "Saga規格の固定上限ではありません。実装資源を増やすか、必要に応じて式を分割してください",
        ) from exc


def compile_source(
    source: str,
    filename: str = "<input>",
    *,
    resource_budget: ResourceBudget | None = None,
):
    program = parse_source(source, filename, resource_budget=resource_budget)
    try:
        with adaptive_recursion_capacity(ast_node_count(program)):
            TypeChecker(filename).check(program)
    except RecursionError as exc:
        raise TypeLimitError(
            "ホストの型検査スタックを使い切ったため検査を継続できません",
            1, 1, filename,
            "Saga規格の固定上限ではありません。実装資源を増やすか、必要に応じて宣言を分割してください",
        ) from exc
    return program


def run_source(
    source: str,
    filename: str = "<input>",
    output: Callable[[str], None] = print,
    precision: int = 50,
    step_limit: int | None = None,
    capabilities: Capabilities | None = None,
    resource_budget: ResourceBudget | None = None,
) -> None:
    program = compile_source(source, filename, resource_budget=resource_budget)
    interpreter = Interpreter(
        filename,
        output=bounded_output(output, filename, resource_budget),
        precision=precision,
        step_limit=effective_step_limit(step_limit, resource_budget),
        capabilities=capabilities,
    )
    try:
        try:
            interpreter.interpret(program)
        except RecursionError as exc:
            raise RuntimeResourceError(
                "ホストの実行スタックを使い切りました", 1, 1, filename,
                "Saga規格の固定再帰上限ではありません。ホスト資源またはアルゴリズムを確認してください",
            ) from exc
    finally:
        interpreter.close()


def compile_file(
    path: str,
    *,
    root: str | None = None,
    resource_budget: ResourceBudget | None = None,
) -> LoadedProgram:
    """Compile an entry file and all ``use \"...saga\"`` source units."""
    return load_program(path, root=root, resource_budget=resource_budget)


def run_file(
    path: str,
    *,
    root: str | None = None,
    output: Callable[[str], None] = print,
    precision: int = 50,
    step_limit: int | None = None,
    capabilities: Capabilities | None = None,
    resource_budget: ResourceBudget | None = None,
) -> None:
    loaded = compile_file(path, root=root, resource_budget=resource_budget)
    interpreter = Interpreter(
        str(loaded.entry),
        output=bounded_output(output, str(loaded.entry), resource_budget),
        precision=precision,
        step_limit=effective_step_limit(step_limit, resource_budget), capabilities=capabilities,
    )
    try:
        try:
            interpreter.interpret(loaded.program)
        except RecursionError as exc:
            raise RuntimeResourceError(
                "ホストの実行スタックを使い切りました", 1, 1, str(loaded.entry),
                "Saga規格の固定再帰上限ではありません。ホスト資源またはアルゴリズムを確認してください",
            ) from exc
    finally:
        interpreter.close()


class SagaSession:
    """Incremental checked execution session used by the REPL and notebooks."""

    def __init__(
        self,
        filename: str = "<session>",
        output: Callable[[str], None] = print,
        precision: int = 50,
        step_limit: int | None = None,
        capabilities: Capabilities | None = None,
        resource_budget: ResourceBudget | None = None,
    ) -> None:
        self.filename = filename
        self.resource_budget = resource_budget
        self.checker = TypeChecker(filename)
        self.interpreter = Interpreter(
            filename,
            output=bounded_output(output, filename, resource_budget),
            precision=precision,
            step_limit=effective_step_limit(step_limit, resource_budget),
            capabilities=capabilities,
        )

    def execute(self, source: str) -> None:
        check_source_bytes(source_size_bytes(source), self.filename, self.resource_budget)
        tokens = Lexer(source, self.filename).scan_tokens()
        check_token_count(len(tokens), self.filename, self.resource_budget)
        try:
            with adaptive_recursion_capacity(len(tokens)):
                program = Parser(tokens, self.filename).parse()
        except RecursionError as exc:
            raise ParseLimitError(
                "ホストの解析スタックを使い切ったため解析を継続できません",
                1, 1, self.filename,
                "Saga規格の固定上限ではありません。実装資源を増やすか、必要に応じて式を分割してください",
            ) from exc
        validate_ast_size(program, self.filename, _ast_budget(self.resource_budget))
        try:
            candidate = copy.deepcopy(self.checker)
            with adaptive_recursion_capacity(ast_node_count(program)):
                candidate.check(program)
        except RecursionError as exc:
            raise TypeLimitError(
                "ホストの型検査スタックを使い切ったため検査を継続できません",
                1, 1, self.filename,
                "Saga規格の固定上限ではありません。実装資源を増やすか、必要に応じて宣言を分割してください",
            ) from exc
        try:
            self.interpreter.interpret_incremental(program)
        except RecursionError as exc:
            raise RuntimeResourceError(
                "ホストの実行スタックを使い切りました",
                1, 1, self.filename,
                "Saga規格の固定再帰上限ではありません。ホスト資源またはアルゴリズムを確認してください",
            ) from exc
        self.checker = candidate

    def close(self) -> None:
        self.interpreter.close()

    def __enter__(self) -> "SagaSession":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
