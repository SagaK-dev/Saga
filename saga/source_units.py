from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from . import ast_nodes as ast
from .checker import TypeChecker
from .ast_limits import ast_node_count, validate_ast_size
from .errors import LexError, ParseError, ParseLimitError, TypeLimitError
from .lexer import Lexer
from .limits import ResourceBudget, check_import_depth, check_module_count, check_source_bytes, check_token_count
from .parser import Parser
from .project import _lexical_symlink_component, load_project
from .package_integrity import strict_json_loads, verify_installed_dependency
from .resource_runtime import adaptive_recursion_capacity


@dataclass(frozen=True, slots=True)
class LoadedProgram:
    program: ast.Program
    entry: Path
    root: Path
    files: tuple[Path, ...]
    sources: dict[Path, str]


def read_source_file(path: str | Path, *, resource_budget: ResourceBudget | None = None) -> str:
    target = Path(path)
    data = target.read_bytes()
    check_source_bytes(len(data), str(target), resource_budget)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise LexError(
            "ソースファイルは正しいUTF-8でなければなりません",
            1, 1, str(target),
            "ファイルをUTF-8として保存し直してください",
            detail_code="SAGA-L104",
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    current = root.absolute()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _default_root(entry: Path) -> Path:
    # A saga.toml project root is preferred.  Importing a stand-alone file is
    # confined to its containing directory.
    current = entry.parent
    while True:
        if (current / "saga.toml").is_file():
            return current
        if current.parent == current:
            return entry.parent
        current = current.parent


def _package_dependency(project_root: Path, spec: str) -> Path:
    # pkg:name/path.saga resolves through saga.dependencies.json so source files
    # never choose a package version implicitly. The registry installer writes
    # this content-addressed lock. Runtime loading re-verifies the installed
    # directory against that artifact digest so post-install tampering cannot be
    # executed silently.
    rest = spec[4:]
    name, sep, relative = rest.partition("/")
    if not sep or not name or not relative or not relative.endswith(".saga"):
        raise ParseError("pkg: import は pkg:name/path.saga 形式にしてください", 1, 1, spec)
    lock = project_root / "saga.dependencies.json"
    try:
        data = strict_json_loads(lock.read_text(encoding="utf-8"))
        record = data["packages"][name]
        if not isinstance(record, dict):
            raise TypeError("invalid dependency record")
        version = record["version"]
        artifact_sha = record["sha256"]
        recorded_path = record["path"]
        if not all(isinstance(v, str) and v for v in (version, artifact_sha, recorded_path)):
            raise TypeError("invalid dependency record")
        base = (project_root / recorded_path).resolve()
        base.relative_to(project_root)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ParseError(f"依存パッケージ '{name}' がlockにないか不正です", 1, 1, spec, "saga add name@version --registry ... を再実行してください") from exc
    target = (base / relative).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ParseError("pkg: importがパッケージ外を参照しています",1,1,spec) from exc
    try:
        # Re-anchor every package import to the original registry artifact.
        # Avoid a process-local "verified" cache here: a package directory and
        # saga.lock can both be replaced between two imports in one compilation.
        verify_installed_dependency(
            base,
            expected_name=name,
            expected_version=version,
            expected_archive_sha256=artifact_sha,
            required_member=relative,
        )
    except (OSError, ValueError) as exc:
        raise ParseError(f"依存パッケージ '{name}' の整合性検証に失敗しました: {exc}", 1, 1, spec, "依存パッケージを再インストールしてください") from exc
    return target


def load_program(
    entry: str | Path,
    *,
    root: str | Path | None = None,
    resource_budget: ResourceBudget | None = None,
) -> LoadedProgram:
    """Load one Saga program while preserving namespaced module boundaries.

    Legacy source units without a ``module`` directive remain flattened for
    compatibility. A source unit with ``module name`` is compiled as an isolated
    namespace and represented by ``SourceModuleStmt`` in its importer.

    ``resource_budget`` is optional deployment policy. Omitting it preserves the
    language's no-fixed-ceiling resource model.
    """
    entry_input = Path(entry).expanduser()
    lexical_entry = entry_input.absolute()
    lexical_root = Path(root).expanduser().absolute() if root is not None else _default_root(lexical_entry)
    if (
        _lexical_symlink_component(entry_input) is not None
        or _lexical_symlink_component(lexical_root) is not None
        or _has_symlink_component(lexical_entry, lexical_root)
    ):
        raise ParseError("エントリソースまたはプロジェクトルートにシンボリックリンクは使用できません", 1, 1, str(entry_input))
    entry_path = entry_input.resolve()
    if not entry_path.is_file():
        raise OSError(f"Sagaソースが見つかりません: {entry_path}")
    project_root = lexical_root.resolve()
    manifest = project_root / "saga.toml"
    if manifest.is_file():
        load_project(manifest)
    try:
        entry_path.relative_to(project_root)
    except ValueError as exc:
        raise OSError("エントリファイルはプロジェクトルート内に必要です") from exc

    visiting: list[Path] = []
    loaded: set[Path] = set()
    discovered: set[Path] = set()
    module_bindings: dict[Path, str] = {}
    module_names: dict[Path, str] = {}
    ordered: list[Path] = []
    sources: dict[Path, str] = {}

    def visit(path: Path, depth: int, *, imported: bool, requested_alias: str | None = None) -> list[ast.Stmt]:
        candidate = path.expanduser()
        check_import_depth(depth, str(candidate), resource_budget)
        if candidate.is_symlink() or _has_symlink_component(candidate, project_root):
            raise ParseError("use先にシンボリックリンクは使用できません", 1, 1, str(candidate))
        resolved = candidate.resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise ParseError(
                "useでプロジェクト外のソースを読み込むことはできません",
                1, 1, str(path), "依存ファイルをプロジェクト内へ配置してください",
            ) from exc
        if resolved in visiting:
            cycle = " -> ".join(p.name for p in [*visiting, resolved])
            raise ParseError(f"ソース単位の循環依存があります: {cycle}", 1, 1, str(resolved))
        if not resolved.is_file() or resolved.suffix != ".saga":
            raise ParseError("use先の .saga ファイルが見つかりません", 1, 1, str(resolved))

        if resolved in loaded:
            if imported:
                previous = module_bindings.get(resolved)
                requested = requested_alias or module_names.get(resolved)
                if previous is not None and requested is not None and previous != requested:
                    raise ParseError(
                        f"同じmoduleを複数aliasで読み込めません: '{previous}' と '{requested}'",
                        1, 1, str(resolved), "1つのcanonical aliasを使用してください", detail_code="SAGA-P109",
                    )
            return []

        if resolved not in discovered:
            check_module_count(len(discovered) + 1, str(resolved), resource_budget)
            discovered.add(resolved)

        visiting.append(resolved)
        source = read_source_file(resolved, resource_budget=resource_budget)
        sources[resolved] = source
        try:
            tokens = Lexer(source, str(resolved)).scan_tokens()
            check_token_count(len(tokens), str(resolved), resource_budget)
            with adaptive_recursion_capacity(len(tokens)):
                program = Parser(tokens, str(resolved)).parse()
            validate_ast_size(
                program,
                str(resolved),
                resource_budget.max_ast_nodes if resource_budget is not None else None,
            )
        except RecursionError as exc:
            raise ParseLimitError(
                "構文が深すぎるため安全に解析できません", 1, 1, str(resolved),
                "式、型、またはブロックを複数の関数へ分割してください",
            ) from exc

        module_decls = [stmt for stmt in program.statements if isinstance(stmt, ast.ModuleDecl)]
        if len(module_decls) > 1:
            raise ParseError("1つのソースファイルにmodule宣言は1つだけ書けます", module_decls[1].keyword.line, module_decls[1].keyword.column, str(resolved), detail_code="SAGA-P102")
        module_decl = module_decls[0] if module_decls else None
        if module_decl is not None and not isinstance(program.statements[0], ast.ModuleDecl):
            raise ParseError("module宣言はファイルの最初の宣言にしてください", module_decl.keyword.line, module_decl.keyword.column, str(resolved), detail_code="SAGA-P102")
        module_name = module_decl.name.lexeme if module_decl is not None else None

        dependency_statements: list[ast.Stmt] = []
        local_statements: list[ast.Stmt] = []
        for stmt in program.statements:
            if isinstance(stmt, ast.ModuleDecl):
                continue
            if isinstance(stmt, ast.UseStmt) and stmt.source_path is not None:
                dependency = _package_dependency(project_root, stmt.source_path) if stmt.source_path.startswith("pkg:") else (resolved.parent / stmt.source_path)
                alias = stmt.alias.lexeme if stmt.alias is not None else None
                dependency_statements.extend(visit(dependency, depth + 1, imported=True, requested_alias=alias))
            else:
                local_statements.append(stmt)

        visiting.pop()
        loaded.add(resolved)
        ordered.append(resolved)

        if imported and module_name is not None:
            bind = requested_alias or module_name
            module_bindings[resolved] = bind
            module_names[resolved] = module_name
            # Dependencies belong to the module's own lexical namespace; they
            # must not leak into the importer.
            body = [*dependency_statements, *local_statements]
            interface = None
            interface_path = resolved.with_suffix(".smi.json")
            if interface_path.is_file():
                try:
                    from .module_interface import load_module_interface
                    interface = load_module_interface(interface_path, source=resolved)
                except (OSError, ValueError, json.JSONDecodeError):
                    # Stale or damaged interface artifacts are never trusted.
                    # Falling back to source checking preserves correctness.
                    interface = None
            return [ast.SourceModuleStmt(module_name, bind, body, module_decl.keyword, interface)]
        if imported and requested_alias and module_name is None:
            raise ParseError(
                "module宣言のないlegacy source unitにはas aliasを付けられません",
                1, 1, str(resolved), "module name を追加するか、asを外してください", detail_code="SAGA-P109",
            )
        if not imported and module_decl is not None:
            # Keep the entry module directive as semantic context. It is a
            # no-op at runtime, but the checker uses it to validate the public
            # ABI surface even when the module is compiled as the entry file.
            return [module_decl, *dependency_statements, *local_statements]
        return [*dependency_statements, *local_statements]

    try:
        statements = visit(entry_path, 0, imported=False)
    except RecursionError as exc:
        raise ParseLimitError(
            "ソース単位の依存が深すぎてホストの読み込みスタックを使い切りました",
            1, 1, str(entry_path),
            "Saga規格の固定モジュール数上限ではありません。依存の段数を整理するか、より大きなホスト資源で再実行してください",
        ) from exc
    combined = ast.Program(statements)
    validate_ast_size(
        combined,
        str(entry_path),
        resource_budget.max_ast_nodes if resource_budget is not None else None,
    )
    try:
        with adaptive_recursion_capacity(ast_node_count(combined)):
            TypeChecker(str(entry_path)).check(combined)
    except RecursionError as exc:
        raise TypeLimitError(
            "型の構造が深すぎるため安全に検査できません", 1, 1, str(entry_path),
            "入れ子の型や宣言を小さなソース単位へ分割してください",
        ) from exc
    return LoadedProgram(combined, entry_path, project_root, tuple(ordered), sources)
