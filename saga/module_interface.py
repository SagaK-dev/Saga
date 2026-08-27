from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json
import os
import tempfile

from . import ast_nodes as ast
from .checker import TypeChecker
from .lexer import Lexer
from .parser import Parser
from .source_units import _default_root, load_program, read_source_file
from .project import _lexical_symlink_component
from .typesys import Type

SCHEMA = "saga.module-interface.v1"
LANGUAGE_VERSION = "0.35"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _reject_interface_output(path: Path) -> None:
    raw = path.expanduser()
    if raw.is_symlink():
        raise ValueError(f"module interface output may not be a symbolic link: {raw}")
    absolute = raw.absolute()
    cwd = Path.cwd().absolute()
    try:
        relative = absolute.relative_to(cwd)
    except ValueError:
        return
    current = cwd
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"module interface output may not contain a symbolic link: {current}")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _source_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _module_parse(path: Path) -> tuple[str, ast.Program]:
    source = read_source_file(path)
    program = Parser(Lexer(source, str(path)).scan_tokens(), str(path)).parse()
    modules = [s for s in program.statements if isinstance(s, ast.ModuleDecl)]
    if len(modules) != 1 or not program.statements or not isinstance(program.statements[0], ast.ModuleDecl):
        raise ValueError(f"separate compilation requires exactly one leading module directive: {path}")
    return modules[0].name.lexeme, program


def _type_text(value: Type) -> str:
    if value.name == "typevar":
        return value.args[0].name if value.args else "T"
    if value.name == "fn":
        parts = [_type_text(v) for v in value.args]
        parts.append(_type_text(value.result) if value.result is not None else "unit")
        return "fn[" + ",".join(parts) + "]"
    name = value.name.split(":", 1)[1] if value.name.startswith("object:") else value.name
    if value.args:
        return name + "[" + ",".join(_type_text(v) for v in value.args) + "]"
    return name


def _fn_export(name: str, info) -> dict:
    return {
        "kind": "fn",
        "name": name,
        "type_params": list(info.type_params),
        "params": [_type_text(v) for v in info.params],
        "return": _type_text(info.function_type().result or Type("unit")),
    }


def _class_export(name: str, info) -> dict:
    return {
        "kind": "class" if not info.interface else "interface",
        "name": name,
        "type_params": list(info.type_params),
        "abstract": bool(info.abstract),
        "base": _type_text(info.base) if info.base is not None else None,
        "interfaces": sorted(_type_text(v) for v in info.interfaces),
        "fields": [
            {
                "name": field_name,
                "type": _type_text(info.own_fields[field_name].type),
                "mutable": bool(info.own_fields[field_name].mutable),
                "private": bool(info.own_fields[field_name].private),
            }
            for field_name in info.own_fields
        ],
        "methods": [
            {
                "name": method_name,
                "params": [_type_text(v) for v in info.own_methods[method_name].params],
                "return": _type_text(info.own_methods[method_name].function_type().result or Type("unit")),
                "type_params": list(info.own_methods[method_name].type_params),
                "abstract": bool(info.own_methods[method_name].abstract),
            }
            for method_name in sorted(info.own_methods)
        ],
    }


def build_module_interface(
    source_path: str | Path,
    *,
    output: str | Path | None = None,
    root: str | Path | None = None,
    recursive: bool = True,
    _active: set[Path] | None = None,
) -> dict:
    """Compile one namespaced source module into a stable public ABI artifact.

    The `.smi.json` file contains no executable implementation body. It is the
    separate-compilation boundary used for ABI comparison, dependency invalidation,
    IDE indexing and cross-implementation conformance. Its hashes prove freshness
    and internal consistency, not provenance; normal execution does not trust an
    interface as a substitute for source validation unless explicitly requested.
    """
    raw_source = Path(source_path).expanduser()
    if raw_source.is_symlink() or _lexical_symlink_component(raw_source) is not None:
        raise ValueError(f"module interface source may not use a symbolic link: {raw_source}")
    path = raw_source.absolute()
    active = _active if _active is not None else set()
    if path in active:
        raise ValueError(f"cyclic module interface compilation: {path}")
    active.add(path)
    try:
        module_name, parsed = _module_parse(path)
        project_root = Path(root).expanduser().absolute() if root is not None else _default_root(path)

        dependencies: list[dict] = []
        if recursive:
            for stmt in parsed.statements:
                if not isinstance(stmt, ast.UseStmt) or stmt.source_path is None or stmt.source_path.startswith("pkg:"):
                    continue
                dep_path = (path.parent / stmt.source_path).absolute()
                try:
                    dep_name, _ = _module_parse(dep_path)
                except ValueError:
                    continue  # legacy flattened source unit, not an ABI module
                dep = build_module_interface(dep_path, root=project_root, recursive=True, _active=active)
                dependencies.append({"module": dep_name, "abi_sha256": dep["abi_sha256"], "source": stmt.source_path})

        loaded = load_program(path, root=project_root)
        checker = TypeChecker(str(path))
        checker.check(loaded.program)
        public_classes = {
            stmt.name.lexeme for stmt in loaded.program.statements
            if isinstance(stmt, (ast.ClassDecl, ast.EnumDecl)) and stmt.visibility == "public"
        }
        # Separate compilation may not freeze an ABI that leaks a module-internal
        # nominal type. Validate the public surface before serializing it.
        for stmt in loaded.program.statements:
            if getattr(stmt, "visibility", "internal") != "public":
                continue
            if isinstance(stmt, ast.VarDecl):
                info = checker._find_var(stmt.name.lexeme)
                if info is not None:
                    checker._public_type_is_exportable(info.type, public_classes, stmt.name)
            elif isinstance(stmt, ast.FunctionDecl):
                checker._public_type_is_exportable(checker.functions[stmt.name.lexeme].function_type(), public_classes, stmt.name)
            elif isinstance(stmt, ast.ClassDecl):
                ci = checker.classes[stmt.name.lexeme]
                if ci.base is not None:
                    checker._public_type_is_exportable(ci.base, public_classes, stmt.name)
                for relation in ci.interfaces:
                    checker._public_type_is_exportable(relation, public_classes, stmt.name)
                for f in ci.own_fields.values():
                    checker._public_type_is_exportable(f.type, public_classes, stmt.name)
                for m in ci.own_methods.values():
                    checker._public_type_is_exportable(m.function_type(), public_classes, stmt.name)
        exports: list[dict] = []
        # Only declarations originating in the module entry are direct statements;
        # imported namespaced modules are represented by SourceModuleStmt.
        for stmt in loaded.program.statements:
            if isinstance(stmt, ast.VarDecl) and stmt.visibility == "public":
                info = checker._find_var(stmt.name.lexeme)
                if info is not None:
                    exports.append({
                        "kind": "var", "name": stmt.name.lexeme,
                        "type": _type_text(info.type), "mutable": bool(stmt.mutable),
                    })
            elif isinstance(stmt, ast.FunctionDecl) and stmt.visibility == "public":
                exports.append(_fn_export(stmt.name.lexeme, checker.functions[stmt.name.lexeme]))
            elif isinstance(stmt, ast.ClassDecl) and stmt.visibility == "public":
                exports.append(_class_export(stmt.name.lexeme, checker.classes[stmt.name.lexeme]))
            elif isinstance(stmt, ast.EnumDecl) and stmt.visibility == "public":
                exports.append({
                    "kind": "enum", "name": stmt.name.lexeme,
                    "type_params": list(stmt.type_params),
                    # Declaration order is ABI-significant because the Native
                    # tagged-union discriminant is the variant index.
                    "variants": [
                        {"name": variant.name.lexeme, "payload": list(variant.payload_types)}
                        for variant in stmt.variants
                    ],
                })
        exports.sort(key=lambda item: (item["kind"], item["name"]))
        dependencies.sort(key=lambda item: item["module"])
        abi_payload = {"schema": SCHEMA, "module": module_name, "exports": exports}
        interface = {
            "schema": SCHEMA,
            "language_version": LANGUAGE_VERSION,
            "module": module_name,
            "source_sha256": _source_sha(path),
            "exports": exports,
            "dependencies": dependencies,
            "abi_sha256": _sha(abi_payload),
        }
        interface["build_sha256"] = _sha({
            "source_sha256": interface["source_sha256"],
            "abi_sha256": interface["abi_sha256"],
            "dependencies": dependencies,
        })
        target = Path(output).expanduser() if output is not None else path.with_suffix(".smi.json")
        if not str(target).endswith(".smi.json"):
            raise ValueError("module interface output must end with .smi.json")
        _reject_interface_output(target)
        _write_atomic(target, _canonical_bytes(interface) + b"\n")
        return interface
    finally:
        active.remove(path)


def load_module_interface(path: str | Path, *, source: str | Path | None = None) -> dict:
    target = Path(path)
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or data.get("language_version") != LANGUAGE_VERSION or not isinstance(data.get("exports"), list):
        raise ValueError(f"invalid Saga module interface: {target}")
    expected_abi = _sha({"schema": SCHEMA, "module": data.get("module"), "exports": data["exports"]})
    if data.get("abi_sha256") != expected_abi:
        raise ValueError(f"module interface ABI hash mismatch: {target}")
    expected_build = _sha({
        "source_sha256": data.get("source_sha256"),
        "abi_sha256": data.get("abi_sha256"),
        "dependencies": data.get("dependencies", []),
    })
    if data.get("build_sha256") != expected_build:
        raise ValueError(f"module interface build hash mismatch: {target}")
    if source is not None:
        source_path = Path(source)
        if data.get("source_sha256") != _source_sha(source_path):
            raise ValueError(f"stale module interface: {target}")
        # A separately compiled module is fresh only if every namespaced module
        # ABI it compiled against is still the same. Implementation-only changes
        # in dependencies do not invalidate importers.
        for dep in data.get("dependencies", []):
            rel = dep.get("source")
            if not isinstance(rel, str) or not rel or rel.startswith("pkg:"):
                continue
            dep_source = (source_path.parent / rel).absolute()
            dep_interface = interface_path_for_source(dep_source)
            nested = load_module_interface(dep_interface, source=dep_source)
            if nested.get("abi_sha256") != dep.get("abi_sha256"):
                raise ValueError(f"stale dependency ABI for {dep.get('module')}: {target}")
    return data


def interface_path_for_source(path: str | Path) -> Path:
    return Path(path).with_suffix(".smi.json")
