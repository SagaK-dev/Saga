from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile

from . import ast_nodes as ast
from .aot import AOTError, _compiler_temp_output, _reject_output_collision, _reject_symlink_output
from .file_lock import exclusive_file_lock
from .lexer import Lexer
from .module_interface import build_module_interface, load_module_interface
from .native_object import _canonical_bytes, _sha_bytes, _sha_file, _write_atomic, _command_identity, _safe_name, _virtual_id
from .parser import Parser
from .project import _lexical_symlink_component
from .source_units import LoadedProgram, _package_dependency, load_program
from .tokens import TokenKind
from .typesys import parse_type, Type, is_typevar, typevar_name

ABI_SCHEMA = "saga.native-codegen-abi.v1"
OBJECT_SCHEMA = "saga.native-codegen-object.v1"
STATE_SCHEMA = "saga.native-codegen-state.v1"
LANGUAGE_VERSION = "0.35"
IMPLEMENTATION_VERSION = "0.50.0"
ABI_VERSION = "0.35"


@dataclass(frozen=True, slots=True)
class NativeCodegenBuildResult:
    output: Path
    build_dir: Path
    state: Path
    report: Path
    objects: tuple[Path, ...]
    compiled_objects: tuple[str, ...]
    reused_objects: tuple[str, ...]
    support_rebuilt: bool
    startup_rebuilt: bool
    linked: bool


@dataclass(frozen=True, slots=True)
class FunctionABI:
    name: str
    visibility: str
    params: tuple[str, ...]
    result: str
    symbol: str
    owner: str | None = None
    dispatch_slot: int | None = None
    declaring_identity: str | None = None
    type_args: tuple[str, ...] = ()

    def public_record(self) -> dict[str, object]:
        return {
            "kind": "fn",
            "name": self.name,
            "params": list(self.params),
            "return": self.result,
            "symbol": self.symbol,
            **({"dispatch_slot": f"0x{self.dispatch_slot:016x}"} if self.dispatch_slot is not None else {}),
            **({"type_args": list(self.type_args)} if self.type_args else {}),
        }


@dataclass(frozen=True, slots=True)
class EnumVariantABI:
    name: str
    payload_types: tuple[str, ...] = ()


@dataclass(slots=True)
class EnumABI:
    name: str
    visibility: str
    identity: str
    type_id: int
    declaration: ast.EnumDecl
    variants: tuple[EnumVariantABI, ...] = ()

    def public_record(self) -> dict[str, object]:
        return {
            "kind": "enum",
            "name": self.name,
            "native_type": f"enum[{self.identity}]",
            "type_id": f"0x{self.type_id:016x}",
            "variants": [
                {"name": variant.name, "tag": index, "payload": list(variant.payload_types)}
                for index, variant in enumerate(self.variants)
            ],
        }


@dataclass(frozen=True, slots=True)
class FieldABI:
    name: str
    type_name: str
    mutable: bool
    private: bool
    index: int

    def public_record(self) -> dict[str, object]:
        return {"name": self.name, "type": self.type_name, "mutable": self.mutable, "private": self.private, "index": self.index}


@dataclass(slots=True)
class ClassABI:
    name: str
    visibility: str
    identity: str
    type_id: int
    declaration: ast.ClassDecl
    fields: tuple[FieldABI, ...]
    methods: dict[str, FunctionABI]
    base_identity: str | None = None
    interface_identities: tuple[str, ...] = ()
    abstract: bool = False
    interface: bool = False
    type_params: tuple[str, ...] = ()
    template_identity: str | None = None

    def public_record(self) -> dict[str, object]:
        return {
            "kind": "class",
            "name": self.name,
            "native_type": f"object[{self.identity}]",
            "type_id": f"0x{self.type_id:016x}",
            # Public source visibility and native layout are separate concerns.
            # Private fields remain absent from the public member surface, but
            # their positional ABI must participate in the class ABI hash so an
            # importer can never reuse an object compiled for a stale layout.
            "fields": [field.public_record() for field in self.fields if not field.private],
            "layout": [
                {"index": field.index, "type": field.type_name, "mutable": field.mutable, "private": field.private}
                for field in self.fields
            ],
            "base": self.base_identity,
            "interfaces": list(self.interface_identities),
            "abstract": self.abstract,
            "interface": self.interface,
            "type_params": list(self.type_params),
            "template_fields": [
                {"name": field.name.lexeme, "type": field.type_name, "mutable": field.mutable, "private": field.private}
                for field in self.declaration.fields
            ] if self.type_params else [],
            "template_methods": [
                {"name": method.name.lexeme, "params": [p.type_name for p in method.parameters], "return": method.return_type or "unit", "abstract": method.abstract, "override": method.override}
                for method in self.declaration.methods
            ] if self.type_params else [],
            "methods": [
                {
                    "name": method.name,
                    "params": list(method.params),
                    "return": method.result,
                    "symbol": method.symbol,
                    "dispatch_symbol": _virtual_symbol(self.identity, method.name),
                    **({"dispatch_slot": f"0x{method.dispatch_slot:016x}"} if method.dispatch_slot is not None else {}),
                }
                for method in sorted(self.methods.values(), key=lambda item: item.name)
            ],
        }


@dataclass(slots=True)
class ModuleUnit:
    path: Path
    virtual_id: str
    module_name: str | None
    source: str
    program: ast.Program
    imports: dict[str, Path]
    functions: dict[str, ast.FunctionDecl]
    function_abis: dict[str, FunctionABI]
    enums: dict[str, EnumABI]
    classes: dict[str, ClassABI]

    @property
    def identity(self) -> str:
        return self.module_name or self.virtual_id


SUPPORTED_TYPES = {"int", "Int", "bool", "Bool", "text", "Text", "unit", "Unit", None}


def _cc() -> str:
    clang = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    if not clang:
        raise AOTError("Native Codegen ABI requires clang/cc")
    return clang


def _target_triple() -> str:
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def _symbol_component(text: str) -> str:
    return text.encode("utf-8").hex() or "00"


def native_function_symbol(module_identity: str, function_name: str) -> str:
    return f"saga_abi035_m{_symbol_component(module_identity)}_f{_symbol_component(function_name)}"


def _entry_symbol(virtual_id: str) -> str:
    return f"saga_abi035_entry_{sha256(virtual_id.encode('utf-8')).hexdigest()[:24]}"

def _type_id(identity: str) -> int:
    # Stable, path-independent nominal identity.  0 remains reserved for
    # "no dynamic type" in the C ABI.
    value = int.from_bytes(sha256(identity.encode("utf-8")).digest()[:8], "big")
    return value or 1


def _method_symbol(module_identity: str, class_name: str, method_name: str) -> str:
    return f"saga_abi035_m{_symbol_component(module_identity)}_c{_symbol_component(class_name)}_f{_symbol_component(method_name)}"


def _constructor_symbol(module_identity: str, class_name: str) -> str:
    return f"saga_abi035_m{_symbol_component(module_identity)}_c{_symbol_component(class_name)}_new"


def _virtual_symbol(class_identity: str, method_name: str) -> str:
    return f"saga_abi035_v{_symbol_component(class_identity)}_f{_symbol_component(method_name)}"


def _dispatch_type_register_symbol(class_identity: str) -> str:
    return f"saga_abi035_register_t{_symbol_component(class_identity)}"


def _dispatch_thunk_symbol(class_identity: str, method_name: str) -> str:
    return f"saga_abi035_thunk_t{_symbol_component(class_identity)}_f{_symbol_component(method_name)}"


def _dispatch_slot(method_name: str, params: tuple[str, ...], result: str) -> int:
    signature = method_name + "(" + ",".join(params) + ")->" + result
    value = int.from_bytes(sha256(signature.encode("utf-8")).digest()[:8], "big")
    return value or 1


def _specialization_suffix(type_args: tuple[str, ...]) -> str:
    if not type_args:
        return ""
    digest = sha256("\x1f".join(type_args).encode("utf-8")).hexdigest()[:16]
    return f"_g{digest}"


def _nominal_identity(raw_name: str, unit: "ModuleUnit", units: dict[Path, "ModuleUnit"], *, enum: bool | None = None) -> tuple[str, str]:
    if "." in raw_name:
        alias, local = raw_name.split(".", 1)
        dep_path = unit.imports.get(alias)
        if dep_path is None:
            raise AOTError(f"Native Codegen ABI 0.35 cannot resolve qualified type '{raw_name}'")
        dep = units[dep_path]
        if enum is True and local not in dep.enums:
            raise AOTError(f"Native Codegen ABI 0.35 enum type not found: {raw_name}")
        if enum is False and local not in dep.classes:
            raise AOTError(f"Native Codegen ABI 0.35 class type not found: {raw_name}")
        return dep.identity, local
    if enum is True and raw_name not in unit.enums:
        raise AOTError(f"Native Codegen ABI 0.35 enum type not found: {raw_name}")
    if enum is False and raw_name not in unit.classes:
        raise AOTError(f"Native Codegen ABI 0.35 class type not found: {raw_name}")
    return unit.identity, raw_name


def _abi_type(value: Type, unit: "ModuleUnit | None" = None, units: dict[Path, "ModuleUnit"] | None = None) -> str:
    if value.name == "int": return "int"
    if value.name == "bool": return "bool"
    if value.name == "text": return "text"
    if value.name == "unit": return "unit"
    if value.name in {"list", "set"} and len(value.args) == 1:
        inner = _abi_type(value.args[0], unit, units)
        if inner == "unit" or inner.startswith("option[") or inner.startswith("result["):
            raise AOTError(f"Native Aggregate ABI 0.35 does not store {inner} directly in {value.name}")
        return f"{value.name}[{inner}]"
    if value.name == "map" and len(value.args) == 2:
        key = _abi_type(value.args[0], unit, units)
        val = _abi_type(value.args[1], unit, units)
        if key == "unit" or val == "unit" or key.startswith(("option[", "result[")) or val.startswith(("option[", "result[")):
            raise AOTError("Native Aggregate ABI 0.35 map keys/values cannot be unit/option/result yet")
        return f"map[{key},{val}]"
    if value.name == "option" and len(value.args) == 1:
        inner = _abi_type(value.args[0], unit, units)
        if inner == "unit" or inner.startswith(("option[", "result[")):
            raise AOTError(f"Native Managed Option ABI 0.35 does not nest tagged value {inner} directly")
        return f"option[{inner}]"
    if value.name == "result" and len(value.args) == 2:
        ok, err = _abi_type(value.args[0], unit, units), _abi_type(value.args[1], unit, units)
        if ok == "unit" or err == "unit" or ok.startswith(("option[", "result[")) or err.startswith(("option[", "result[")):
            raise AOTError("Native Managed Result ABI 0.35 does not nest option/result/unit payloads directly")
        return f"result[{ok},{err}]"
    if value.name.startswith("object:"):
        if unit is None or units is None:
            raise AOTError(f"Native Codegen ABI 0.35 needs module context for nominal type {value}")
        raw = value.name.split(":", 1)[1]
        # parse_type cannot distinguish enum names from classes. Resolve against
        # the module graph before assigning a Native ABI representation.
        if "." in raw:
            alias, local = raw.split(".", 1)
            dep_path = unit.imports.get(alias)
            if dep_path is None:
                raise AOTError(f"Native Codegen ABI 0.35 cannot resolve type '{raw}'")
            dep = units[dep_path]
            if local in dep.enums:
                return f"enum[{dep.identity}.{local}]"
            if local in dep.classes:
                return f"object[{dep.identity}.{local}]"
        else:
            if raw in unit.enums:
                return f"enum[{unit.identity}.{raw}]"
            if raw in unit.classes:
                return f"object[{unit.identity}.{raw}]"
        raise AOTError(f"Native Codegen ABI 0.35 nominal type is not part of the current module graph: {raw}")
    raise AOTError(f"Native Codegen ABI 0.35 type is not yet stable: {value}")


def _type_name(name: str | None, unit: "ModuleUnit | None" = None, units: dict[Path, "ModuleUnit"] | None = None) -> str:
    if name is None:
        return "unit"
    try:
        return _abi_type(parse_type(name), unit, units)
    except ValueError as exc:
        raise AOTError(str(exc)) from exc


def _split_generic(name: str, prefix: str) -> tuple[str, ...]:
    if not (name.startswith(prefix + "[") and name.endswith("]")):
        return ()
    body = name[len(prefix) + 1:-1]
    depth = 0
    parts: list[str] = []
    start = 0
    for index, ch in enumerate(body):
        if ch == "[": depth += 1
        elif ch == "]": depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:index]); start = index + 1
    parts.append(body[start:])
    return tuple(part for part in parts if part)


def _inner_types(name: str) -> tuple[str, ...]:
    for prefix in ("option", "result", "list", "map", "set"):
        values = _split_generic(name, prefix)
        if values:
            return values
    return ()


def _is_ref_type(name: str) -> bool:
    return name.startswith(("list[", "map[", "set[", "object["))


def _is_enum_type(name: str) -> bool:
    return name.startswith("enum[")


def _ctype(name: str) -> str:
    if name == "int": return "int64_t"
    if name == "bool": return "uint8_t"
    if name == "text": return "SagaText"
    if name == "unit": return "void"
    if _is_enum_type(name): return "SagaTagged"
    if _is_ref_type(name): return "SagaRef"
    if name.startswith("option["): return "SagaOption"
    if name.startswith("result["): return "SagaResult"
    if name == "error": return "SagaException"
    raise AssertionError(name)


def _value_field(kind: str) -> str:
    if kind == "int": return "i64"
    if kind == "bool": return "boolean"
    if kind == "text": return "text"
    if _is_enum_type(kind): return "tagged"
    if _is_ref_type(kind): return "ref"
    raise AOTError(f"Native Value ABI 0.35 cannot store payload type {kind}")


def _heap_kind(kind: str) -> str:
    if kind == "int": return "SAGA_HV_I64"
    if kind == "bool": return "SAGA_HV_BOOL"
    if kind == "text": return "SAGA_HV_TEXT"
    if _is_enum_type(kind): return "SAGA_HV_TAGGED"
    if _is_ref_type(kind): return "SAGA_HV_REF"
    raise AOTError(f"Native Aggregate ABI 0.35 cannot store value type {kind}")


def _heap_value(kind: str, value: str) -> str:
    return f"(SagaHeapValue){{{_heap_kind(kind)}, {{.{_value_field(kind)} = {value}}}}}"


def _text_literal(value: str) -> str:
    data = value.encode("utf-8")
    encoded = ''.join(f"\\{b:03o}" for b in data)
    return f'(SagaText){{(const uint8_t*)"{encoded}", UINT64_C({len(data)}), NULL}}'

def _parse(path: Path, source: str) -> ast.Program:
    return Parser(Lexer(source, str(path)).scan_tokens(), str(path)).parse()


def _module_name(program: ast.Program) -> str | None:
    for st in program.statements:
        if isinstance(st, ast.ModuleDecl):
            return st.name.lexeme
    return None


def _resolve_graph(loaded: LoadedProgram) -> tuple[dict[Path, ModuleUnit], dict[Path, str]]:
    ids = {path: _virtual_id(path, loaded) for path in loaded.files}
    parsed = {path: _parse(path, loaded.sources[path]) for path in loaded.files}
    module_names = {path: _module_name(parsed[path]) for path in loaded.files}

    seen_names: dict[str, Path] = {}
    for path, name in module_names.items():
        if name is None:
            continue
        previous = seen_names.get(name)
        if previous is not None and previous != path:
            raise AOTError(f"Native Codegen ABI requires unique module identities: {name}")
        seen_names[name] = path

    imports_by_path: dict[Path, dict[str, Path]] = {}
    for path in loaded.files:
        imports: dict[str, Path] = {}
        for st in parsed[path].statements:
            if not isinstance(st, ast.UseStmt) or st.source_path is None:
                continue
            dep = _package_dependency(loaded.root, st.source_path).resolve() if st.source_path.startswith("pkg:") else (path.parent / st.source_path).resolve()
            dep_name = module_names.get(dep)
            if dep not in ids:
                raise AOTError(f"Native Codegen graph is missing dependency {st.source_path} from {path}")
            if dep_name is None:
                raise AOTError("Native Codegen ABI 0.35 requires namespaced dependencies; legacy flattened source units use --profile object/standard")
            alias = st.alias.lexeme if st.alias is not None else dep_name
            if alias in imports and imports[alias] != dep:
                raise AOTError(f"duplicate module alias in Native Codegen graph: {alias}")
            imports[alias] = dep
        imports_by_path[path] = imports

    # Pass 1: create all nominal cells before resolving relationships.
    units: dict[Path, ModuleUnit] = {}
    for path in loaded.files:
        identity = module_names[path] or ids[path]
        enums: dict[str, EnumABI] = {}
        classes: dict[str, ClassABI] = {}
        functions: dict[str, ast.FunctionDecl] = {}
        for st in parsed[path].statements:
            if isinstance(st, ast.EnumDecl):
                enum_identity = f"{identity}.{st.name.lexeme}"
                enums[st.name.lexeme] = EnumABI(st.name.lexeme, st.visibility, enum_identity, _type_id(enum_identity), st)
            elif isinstance(st, ast.ClassDecl):
                class_identity = f"{identity}.{st.name.lexeme}"
                classes[st.name.lexeme] = ClassABI(
                    st.name.lexeme, st.visibility, class_identity, _type_id(class_identity), st, (), {},
                    abstract=st.abstract, interface=st.interface, type_params=tuple(st.type_params),
                )
            elif isinstance(st, ast.FunctionDecl):
                functions[st.name.lexeme] = st
        units[path] = ModuleUnit(path, ids[path], module_names[path], loaded.sources[path], parsed[path], imports_by_path[path], functions, {}, enums, classes)

    def resolve_class(raw: str, unit: ModuleUnit) -> ClassABI:
        parsed_type = parse_type(raw)
        if not parsed_type.name.startswith("object:"):
            raise AOTError(f"Native class relationship requires class/interface type, got {raw}")
        if parsed_type.args:
            raise AOTError(f"Native generic inheritance is deferred; specialize the aggregate before inheritance: {raw}")
        name = parsed_type.name.split(":", 1)[1]
        if "." in name:
            alias, local = name.split(".", 1)
            dep_path = unit.imports.get(alias)
            if dep_path is None:
                raise AOTError(f"Native class relationship cannot resolve {raw}")
            found = units[dep_path].classes.get(local)
        else:
            found = unit.classes.get(name)
        if found is None:
            raise AOTError(f"Native class/interface not found: {raw}")
        return found

    # Enums are independent of class hierarchy.
    for unit in units.values():
        for enum in unit.enums.values():
            converted: list[EnumVariantABI] = []
            for variant in enum.declaration.variants:
                if len(variant.payload_types) > 4:
                    raise AOTError(f"Native Tagged Union ABI 0.35 supports at most 4 payload values per variant: {enum.name}.{variant.name.lexeme}")
                payload = tuple(_abi_type(parse_type(text), unit, units) for text in variant.payload_types)
                for item in payload:
                    if item == "unit" or item.startswith(("option[", "result[")) or _is_enum_type(item):
                        raise AOTError(f"Native Tagged Union ABI 0.35 payload type is not yet stable: {item}")
                converted.append(EnumVariantABI(variant.name.lexeme, payload))
            enum.variants = tuple(converted)

    resolving: set[str] = set()
    resolved: set[str] = set()

    def resolve_layout(unit: ModuleUnit, cls: ClassABI) -> None:
        if cls.identity in resolved:
            return
        if cls.identity in resolving:
            raise AOTError(f"Native class inheritance cycle: {cls.identity}")
        resolving.add(cls.identity)
        decl = cls.declaration
        if cls.type_params:
            # Generic class declarations are templates. Concrete layouts are
            # materialized lazily by the emitter's monomorphizer.
            resolving.remove(cls.identity)
            resolved.add(cls.identity)
            return

        inherited_fields: list[FieldABI] = []
        inherited_methods: dict[str, FunctionABI] = {}
        if decl.base_name:
            base = resolve_class(decl.base_name, unit)
            if base.interface:
                raise AOTError(f"class {cls.name} cannot extend interface {base.name}")
            if base.type_params:
                raise AOTError(f"Native generic base specialization not available in direct hierarchy yet: {decl.base_name}")
            base_unit = next(u for u in units.values() if base.name in u.classes and u.classes[base.name] is base)
            resolve_layout(base_unit, base)
            cls.base_identity = base.identity
            inherited_fields.extend(base.fields)
            inherited_methods.update(base.methods)

        interface_ids: list[str] = []
        for raw in decl.interfaces:
            iface = resolve_class(raw, unit)
            if not iface.interface:
                raise AOTError(f"implements target must be interface: {raw}")
            iface_unit = next(u for u in units.values() if iface.name in u.classes and u.classes[iface.name] is iface)
            resolve_layout(iface_unit, iface)
            interface_ids.append(iface.identity)
        cls.interface_identities = tuple(interface_ids)

        fields = list(inherited_fields)
        for field in decl.fields:
            field_type = _type_name(field.type_name, unit, units)
            if field_type == "unit" or field_type.startswith(("option[", "result[")):
                raise AOTError(f"Native Object ABI 0.35 field '{cls.name}.{field.name.lexeme}' has unsupported type {field_type}")
            fields.append(FieldABI(field.name.lexeme, field_type, field.mutable, field.private, len(fields)))

        methods = dict(inherited_methods)
        for method in decl.methods:
            if method.type_params:
                raise AOTError(f"Native generic methods are not yet part of aggregate monomorphization: {cls.name}.{method.name.lexeme}")
            params = tuple(_type_name(param.type_name, unit, units) for param in method.parameters)
            if "unit" in params:
                raise AOTError(f"Native Object ABI 0.35 does not define unit-valued method parameters: {cls.name}.{method.name.lexeme}")
            result = _type_name(method.return_type, unit, units)
            slot = _dispatch_slot(method.name.lexeme, params, result)
            inherited = methods.get(method.name.lexeme)
            if method.override and inherited is not None and (inherited.params != params or inherited.result != result):
                raise AOTError(f"Native override signature mismatch: {cls.name}.{method.name.lexeme}")
            methods[method.name.lexeme] = FunctionABI(
                method.name.lexeme, method.visibility, params, result,
                _method_symbol(unit.identity, cls.name, method.name.lexeme), owner=cls.name,
                dispatch_slot=slot, declaring_identity=cls.identity,
            )

        # Interface methods are abstract contracts but still occupy stable slots.
        if cls.interface:
            fields = []
        cls.fields = tuple(fields)
        cls.methods = methods
        resolving.remove(cls.identity)
        resolved.add(cls.identity)

    for unit in units.values():
        for cls in unit.classes.values():
            resolve_layout(unit, cls)

    # Ensure concrete classes satisfy every directly implemented interface using
    # the exact stable dispatch signature. The source checker already validates
    # semantics; this is the ABI-level guard.
    by_identity = {cls.identity: cls for unit in units.values() for cls in unit.classes.values()}
    for unit in units.values():
        for cls in unit.classes.values():
            if cls.interface or cls.type_params:
                continue
            for iface_id in cls.interface_identities:
                iface = by_identity[iface_id]
                for name, required in iface.methods.items():
                    actual = cls.methods.get(name)
                    if actual is None or actual.params != required.params or actual.result != required.result:
                        raise AOTError(f"Native interface ABI not implemented: {cls.name} -> {iface.name}.{name}")

        for fn_name, st in unit.functions.items():
            if st.abstract or st.override:
                raise AOTError(f"Native top-level function cannot be abstract/override: {st.name.lexeme}")
            if st.type_params:
                # Generic functions are templates and get concrete ABI records
                # when first called. They are intentionally absent from the
                # unspecialized public ABI surface.
                continue
            params = tuple(_type_name(p.type_name, unit, units) for p in st.parameters)
            if "unit" in params:
                raise AOTError(f"Native Codegen ABI 0.35 does not define unit-valued parameters: {st.name.lexeme}")
            result = _type_name(st.return_type, unit, units)
            unit.function_abis[fn_name] = FunctionABI(st.name.lexeme, st.visibility, params, result, native_function_symbol(unit.identity, st.name.lexeme))
    return units, ids

def _validate_codegen_graph(loaded: LoadedProgram, units: dict[Path, ModuleUnit]) -> None:
    """Reject source forms whose Standard semantics the direct backend cannot preserve.

    The runtime-object profile remains available for the complete Standard Core.
    Direct Codegen 0.35 intentionally fails closed instead of silently ignoring
    module initialization, closures, hosted-runtime behavior, or unsupported
    object inheritance/generic semantics.
    """
    for path, unit in units.items():
        for st in unit.program.statements:
            if path != loaded.entry and not isinstance(st, (ast.ModuleDecl, ast.UseStmt, ast.FunctionDecl, ast.EnumDecl, ast.ClassDecl)):
                raise AOTError(
                    f"Native Codegen dependency modules may contain declarations only; module initialization is not yet in ABI 0.35: {path}"
                )


def _abi_payload(unit: ModuleUnit) -> dict[str, object]:
    exports: list[dict[str, object]] = []
    exports.extend(abi.public_record() for abi in unit.function_abis.values() if abi.visibility == "public")
    exports.extend(enum.public_record() for enum in unit.enums.values() if enum.visibility == "public")
    exports.extend(cls.public_record() for cls in unit.classes.values() if cls.visibility == "public")
    exports.sort(key=lambda item: (str(item["kind"]), str(item["name"])))
    payload = {
        "schema": ABI_SCHEMA,
        "abi_version": ABI_VERSION,
        "language_version": LANGUAGE_VERSION,
        "module": unit.module_name,
        "identity": unit.identity,
        "exports": exports,
        "memory_model": "managed-ref-generational-incremental-concurrent-sweep-0.35",
        "dispatch_model": "open-world-registry-stable-slot-type-id-v1",
        "runtime_feature_level": "0.38",
        "gc_features": ["generational", "incremental-major-mark", "incremental-major-sweep", "optional-concurrent-sweep", "low-pause-major-budget"],
        "exception_model": "setjmp-longjmp-managed-root-unwind",
        "owned_text": True,
        "managed_option_result": True,
    }
    return {**payload, "abi_sha256": _sha_bytes(_canonical_bytes(payload))}

def _abi_file(build_dir: Path, unit: ModuleUnit) -> Path:
    return build_dir / "abi" / (_safe_name(unit.virtual_id) + ".nabi.json")


def _abi_header_file(build_dir: Path, unit: ModuleUnit) -> Path:
    return build_dir / "abi" / (_safe_name(unit.virtual_id) + ".nabi.h")


def _abi_header(unit: ModuleUnit) -> str:
    guard = "SAGA_NABI035_" + sha256(unit.identity.encode("utf-8")).hexdigest()[:24].upper()
    lines = [f"#ifndef {guard}", f"#define {guard}", '#include "saga_native_abi035.h"', ""]
    for enum in sorted(unit.enums.values(), key=lambda item: item.name):
        if enum.visibility != "public":
            continue
        lines.append(f"#define SAGA_ENUM_{_symbol_component(enum.identity).upper()}_TYPE UINT64_C(0x{enum.type_id:016x})")
        for tag, variant in enumerate(enum.variants):
            lines.append(f"#define SAGA_ENUM_{_symbol_component(enum.identity + '.' + variant.name).upper()}_TAG UINT32_C({tag})")
        lines.append("")
    for cls in sorted(unit.classes.values(), key=lambda item: item.name):
        if cls.visibility != "public":
            continue
        if not cls.type_params:
            lines.append(f"#define SAGA_TYPE_{_symbol_component(cls.identity).upper()} UINT64_C(0x{cls.type_id:016x})")
            for method in sorted(cls.methods.values(), key=lambda item: item.name):
                lines.append(f"#define SAGA_SLOT_{_symbol_component(cls.identity + '.' + method.name).upper()} UINT64_C(0x{method.dispatch_slot:016x})")
            lines.append("")
        if not cls.abstract and not cls.interface and not cls.type_params:
            params = ", ".join(f"{_ctype(field.type_name)} saga_p_{index}" for index, field in enumerate(cls.fields)) or "void"
            lines.append(f"SagaRef {_constructor_symbol(unit.identity, cls.name)}({params});")
        if not cls.type_params:
            lines.append(f"void {_dispatch_type_register_symbol(cls.identity)}(void);")
            for method in sorted(cls.methods.values(), key=lambda item: item.name):
                args = ["SagaRef saga_self", *[f"{_ctype(t)} saga_p_{i}" for i, t in enumerate(method.params)]]
                lines.append(f"{_ctype(method.result)} {_virtual_symbol(cls.identity, method.name)}({', '.join(args)});")
                if method.declaring_identity == cls.identity and not next((m.abstract for m in cls.declaration.methods if m.name.lexeme == method.name), False):
                    lines.append(f"{_ctype(method.result)} {method.symbol}({', '.join(args)});")
        lines.append("")
    for abi in sorted(unit.function_abis.values(), key=lambda item: item.name):
        if abi.visibility != "public":
            continue
        params = ", ".join(f"{_ctype(t)} saga_p_{i}" for i, t in enumerate(abi.params)) or "void"
        lines.append(f"{_ctype(abi.result)} {abi.symbol}({params});")
    lines.extend(["", f"#endif /* {guard} */", ""])
    return "\n".join(lines)

def _emit_abi(build_dir: Path, unit: ModuleUnit) -> dict[str, object]:
    data = _abi_payload(unit)
    target = _abi_file(build_dir, unit)
    _write_atomic(target, _canonical_bytes(data) + b"\n")
    _write_atomic(_abi_header_file(build_dir, unit), _abi_header(unit).encode("utf-8"))
    return data


def _support_header() -> str:
    return r'''#ifndef SAGA_NATIVE_ABI035_H
#define SAGA_NATIVE_ABI035_H
#include <stdint.h>
#include <stddef.h>
#include <setjmp.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct SagaHeapObject SagaHeapObject;
typedef SagaHeapObject *SagaRef;
typedef struct { const uint8_t *data; uint64_t len; SagaRef owner; } SagaText;
#define SAGA_TAGGED_MAX_PAYLOAD 4
typedef union { int64_t i64; uint8_t boolean; SagaText text; SagaRef ref; } SagaPayload;
typedef struct {
    uint64_t type_id;
    uint32_t tag;
    uint8_t arity;
    uint8_t kinds[SAGA_TAGGED_MAX_PAYLOAD];
    SagaPayload payload[SAGA_TAGGED_MAX_PAYLOAD];
} SagaTagged;
typedef union { int64_t i64; uint8_t boolean; SagaText text; SagaTagged tagged; SagaRef ref; } SagaValue;
typedef struct { uint8_t kind; SagaValue value; } SagaHeapValue;
typedef struct { uint8_t present; SagaValue value; } SagaOption;
typedef struct { uint8_t ok; SagaValue value; } SagaResult;
typedef struct { SagaText kind; SagaText message; } SagaException;
typedef struct SagaExceptionFrame {
    jmp_buf env;
    uint64_t root_mark;
    struct SagaExceptionFrame *previous;
} SagaExceptionFrame;

enum {
    SAGA_HV_I64 = 1,
    SAGA_HV_BOOL = 2,
    SAGA_HV_TEXT = 3,
    SAGA_HV_TAGGED = 4,
    SAGA_HV_REF = 5
};
enum {
    SAGA_HEAP_LIST = 1,
    SAGA_HEAP_MAP = 2,
    SAGA_HEAP_SET = 3,
    SAGA_HEAP_OBJECT = 4,
    SAGA_HEAP_TEXT = 5
};
enum {
    SAGA_GC_IDLE = 0,
    SAGA_GC_MARKING = 1,
    SAGA_GC_SWEEP_PENDING = 2,
    SAGA_GC_SWEEPING = 3,
    SAGA_GC_MINOR_MARKING = 4,
    SAGA_GC_MINOR_SWEEPING = 5
};

typedef void (*SagaDispatchThunk)(SagaRef self, const void *const *args, void *result);
void saga_dispatch_register_type(uint64_t type_id, uint64_t base_type_id);
void saga_dispatch_register_interface(uint64_t type_id, uint64_t interface_id);
void saga_dispatch_register_method(uint64_t type_id, uint64_t slot, SagaDispatchThunk thunk);
uint8_t saga_dispatch_is_a(uint64_t type_id, uint64_t expected_type_id);
void saga_dispatch_invoke(uint64_t type_id, uint64_t expected_type_id, uint64_t slot, SagaRef self, const void *const *args, void *result);
uint64_t saga_dispatch_registered_types(void);
uint64_t saga_dispatch_registered_methods(void);

int64_t saga_abi035_add_i64(int64_t a, int64_t b);
int64_t saga_abi035_sub_i64(int64_t a, int64_t b);
int64_t saga_abi035_mul_i64(int64_t a, int64_t b);
int64_t saga_abi035_neg_i64(int64_t a);
int64_t saga_abi035_abs_i64(int64_t a);
int64_t saga_abi035_mod_i64(int64_t a, int64_t b);
int64_t saga_abi035_machine_q31_from_ratio(int64_t numerator, int64_t denominator);
int64_t saga_abi035_machine_q31_add_sat(int64_t left, int64_t right);
int64_t saga_abi035_machine_q31_sub_sat(int64_t left, int64_t right);
int64_t saga_abi035_machine_q31_mul_sat(int64_t left, int64_t right);
int64_t saga_abi035_machine_q31_mac_sat(int64_t accumulator, int64_t left, int64_t right);
uint8_t saga_abi035_text_equal(SagaText a, SagaText b);
SagaText saga_abi035_text_owned_copy(SagaText value);
SagaText saga_abi035_text_concat(SagaText a, SagaText b);
SagaText saga_abi035_text_from_i64(int64_t value);
SagaText saga_abi035_text_from_bool(uint8_t value);
uint8_t saga_abi035_text_is_owned(SagaText value);
uint8_t saga_abi035_tagged_equal(SagaTagged a, SagaTagged b);
void saga_abi035_print_i64(int64_t value);
void saga_abi035_print_bool(uint8_t value);
void saga_abi035_print_text(SagaText value);
void saga_abi035_print_tagged(SagaTagged value);
void saga_abi035_print_ref(SagaRef value);
void saga_abi035_print_unit(void);

uint64_t saga_gc_root_mark(void);
void saga_gc_root_ref(volatile SagaRef *slot);
void saga_gc_root_text(volatile SagaText *slot);
void saga_gc_root_tagged(volatile SagaTagged *slot);
void saga_gc_root_option(volatile SagaOption *slot, uint8_t payload_kind);
void saga_gc_root_result(volatile SagaResult *slot, uint8_t ok_kind, uint8_t err_kind);
void saga_gc_unwind_roots(uint64_t mark);
void saga_gc_collect(void);
void saga_gc_collect_minor(void);
uint8_t saga_gc_minor_step(uint64_t budget);
uint8_t saga_gc_step(uint64_t budget);
uint8_t saga_gc_phase(void);
uint64_t saga_gc_live_objects(void);
uint64_t saga_gc_young_objects(void);
uint64_t saga_gc_old_objects(void);
uint64_t saga_gc_collections(void);
uint64_t saga_gc_minor_collections(void);
uint64_t saga_gc_major_collections(void);
uint64_t saga_gc_bytes(void);
uint64_t saga_gc_promotions(void);
uint8_t saga_gc_concurrent_sweep_available(void);
uint64_t saga_gc_concurrent_sweeps(void);
void saga_gc_low_pause_enable(uint64_t object_budget);
uint8_t saga_gc_poll(void);
uint64_t saga_gc_pause_budget(void);
uint64_t saga_gc_last_pause_work(void);
uint64_t saga_gc_max_pause_work(void);
uint64_t saga_gc_incremental_sweeps(void);
uint64_t saga_gc_incremental_minor_collections(void);
uint8_t saga_gc_incremental_minor_available(void);
uint64_t saga_allocator_live_bytes(void);
uint64_t saga_allocator_peak_bytes(void);
uint64_t saga_allocator_reserved_bytes(void);
void saga_gc_shutdown(void);

void saga_exception_link(SagaExceptionFrame *frame, uint64_t root_mark);
void saga_exception_leave(SagaExceptionFrame *frame);
SagaException saga_exception_current(void);
void saga_exception_clear(void);
void saga_throw_text(SagaText message);
void saga_throw_i64(int64_t value);
void saga_throw_bool(uint8_t value);
void saga_exception_rethrow(void);
#define saga_exception_enter(frame, mark) (saga_exception_link(&(frame), (mark)), setjmp((frame).env))

SagaRef saga_list_new(uint8_t element_kind, uint64_t reserve);
void saga_list_push(SagaRef list, SagaHeapValue value);
SagaRef saga_list_append(SagaRef list, SagaHeapValue value);
SagaRef saga_list_prepend(SagaRef list, SagaHeapValue value);
SagaRef saga_list_set_at(SagaRef list, int64_t index, SagaHeapValue value);
SagaHeapValue saga_list_get(SagaRef list, int64_t index);
SagaHeapValue saga_list_get_or(SagaRef list, int64_t index, SagaHeapValue fallback);
uint8_t saga_list_contains(SagaRef list, SagaHeapValue value);

SagaRef saga_map_new(uint8_t key_kind, uint8_t value_kind, uint64_t reserve);
SagaRef saga_map_put(SagaRef map, SagaHeapValue key, SagaHeapValue value);
SagaRef saga_map_remove(SagaRef map, SagaHeapValue key);
SagaHeapValue saga_map_get_or(SagaRef map, SagaHeapValue key, SagaHeapValue fallback);
uint8_t saga_map_contains(SagaRef map, SagaHeapValue key);

SagaRef saga_set_new(uint8_t element_kind, uint64_t reserve);
SagaRef saga_set_add(SagaRef set, SagaHeapValue value);
SagaRef saga_set_remove(SagaRef set, SagaHeapValue value);
uint8_t saga_set_contains(SagaRef set, SagaHeapValue value);
SagaRef saga_set_union(SagaRef left, SagaRef right);
SagaRef saga_set_intersection(SagaRef left, SagaRef right);

uint64_t saga_ref_len(SagaRef value);
SagaRef saga_object_new(uint64_t type_id, uint64_t field_count);
uint64_t saga_object_type_id(SagaRef object);
void saga_object_set(SagaRef object, uint64_t index, SagaHeapValue value);
SagaHeapValue saga_object_get(SagaRef object, uint64_t index);

#ifdef __cplusplus
}
#endif
#endif
'''


def _support_c() -> str:
    return r'''#include "saga_native_abi035.h"
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if !defined(__STDC_NO_THREADS__)
#include <threads.h>
#define SAGA_HAS_C11_THREADS 1
#else
#define SAGA_HAS_C11_THREADS 0
#endif

struct SagaHeapObject {
    uint8_t marked;
    uint8_t heap_kind;
    uint8_t key_kind;
    uint8_t value_kind;
    uint8_t generation;
    uint8_t age;
    uint8_t remembered;
    uint8_t reserved_flags;
    uint64_t type_id;
    uint64_t len;
    uint64_t cap;
    uint64_t byte_len;
    SagaHeapValue *items;
    uint8_t *bytes;
    struct SagaHeapObject *next;
};

typedef struct { uint8_t kind; uint8_t a; uint8_t b; volatile void *slot; } SagaRoot;
enum { SAGA_ROOT_REF = 1, SAGA_ROOT_TEXT = 2, SAGA_ROOT_TAGGED = 3, SAGA_ROOT_OPTION = 4, SAGA_ROOT_RESULT = 5 };

static SagaHeapObject *saga_heap_head = NULL;
static SagaRoot *saga_roots = NULL;
static uint64_t saga_roots_len = 0;
static uint64_t saga_roots_cap = 0;
static SagaRef *saga_gray = NULL;
static uint64_t saga_gray_len = 0;
static uint64_t saga_gray_cap = 0;
static uint8_t saga_phase = SAGA_GC_IDLE;
static uint8_t saga_sync_sweep = 0;
static uint64_t saga_live = 0;
static uint64_t saga_bytes_live = 0;
static uint64_t saga_collection_count = 0;
static uint64_t saga_minor_count = 0;
static uint64_t saga_major_count = 0;
static uint64_t saga_promotions = 0;
static uint64_t saga_minor_allocations = 0;
static uint64_t saga_concurrent_sweeps_count = 0;
static uint64_t saga_low_pause_budget = 0;
static uint64_t saga_last_pause_work = 0;
static uint64_t saga_max_pause_work = 0;
static uint64_t saga_incremental_sweeps_count = 0;
static uint64_t saga_incremental_minor_count = 0;
static SagaHeapObject **saga_incremental_sweep_cursor = NULL;
static SagaHeapObject **saga_minor_sweep_cursor = NULL;

static SagaExceptionFrame *saga_exception_top = NULL;
static SagaException saga_current_exception = {{0}, {0}};
static uint8_t saga_exception_present = 0;

typedef struct { uint64_t type_id; uint64_t base_type_id; uint64_t *interfaces; uint64_t interface_len; uint64_t interface_cap; } SagaDispatchType;
typedef struct { uint64_t type_id; uint64_t slot; SagaDispatchThunk thunk; } SagaDispatchMethod;
static SagaDispatchType *saga_dispatch_types = NULL;
static uint64_t saga_dispatch_type_len = 0, saga_dispatch_type_cap = 0;
static SagaDispatchMethod *saga_dispatch_methods = NULL;
static uint64_t saga_dispatch_method_len = 0, saga_dispatch_method_cap = 0;

static void saga_fatal(const char *message, int code) {
    if (saga_exception_top) {
        static const uint8_t kind_bytes[] = "NativeFailure";
        saga_current_exception.kind = (SagaText){kind_bytes, UINT64_C(13), NULL};
        saga_current_exception.message = (SagaText){(const uint8_t*)message, (uint64_t)strlen(message), NULL};
        saga_exception_present = 1;
        SagaExceptionFrame *frame = saga_exception_top;
        saga_exception_top = frame->previous;
        saga_gc_unwind_roots(frame->root_mark);
        longjmp(frame->env, 1);
    }
    fputs(message, stderr); fputc('\n', stderr); exit(code);
}
static void saga_abi035_overflow(void) { saga_fatal("SAGA-R103: native int64 overflow", 70); }
static void saga_abi035_mod_zero(void) { saga_fatal("SAGA-R102: modulo by zero", 71); }

#if SAGA_HAS_C11_THREADS
static once_flag saga_dispatch_lock_once = ONCE_FLAG_INIT;
static mtx_t saga_dispatch_lock;
static void saga_dispatch_lock_init(void){if(mtx_init(&saga_dispatch_lock,mtx_plain)!=thrd_success){fputs("SAGA-R206: dispatch registry lock initialization failed\n",stderr);exit(106);}}
static void saga_dispatch_lock_acquire(void){call_once(&saga_dispatch_lock_once,saga_dispatch_lock_init);if(mtx_lock(&saga_dispatch_lock)!=thrd_success){fputs("SAGA-R207: dispatch registry lock failed\n",stderr);exit(107);}}
static void saga_dispatch_lock_release(void){(void)mtx_unlock(&saga_dispatch_lock);}
#else
static void saga_dispatch_lock_acquire(void){}
static void saga_dispatch_lock_release(void){}
#endif
static SagaDispatchType *saga_dispatch_type_find_unlocked(uint64_t type_id) {
    for(uint64_t i=0;i<saga_dispatch_type_len;++i) if(saga_dispatch_types[i].type_id==type_id) return &saga_dispatch_types[i];
    return NULL;
}
void saga_dispatch_register_type(uint64_t type_id,uint64_t base_type_id){
    if(!type_id) saga_fatal("SAGA-R200: dynamic type id 0 is reserved",100);
    saga_dispatch_lock_acquire();
    SagaDispatchType *existing=saga_dispatch_type_find_unlocked(type_id);
    if(existing){uint8_t conflict=(existing->base_type_id!=base_type_id);saga_dispatch_lock_release();if(conflict)saga_fatal("SAGA-R201: conflicting dynamic type registration",101);return;}
    if(saga_dispatch_type_len==saga_dispatch_type_cap){
        uint64_t cap=saga_dispatch_type_cap?saga_dispatch_type_cap*2:16;
        void *next=realloc(saga_dispatch_types,(size_t)(cap*sizeof(SagaDispatchType)));
        if(!next){saga_dispatch_lock_release();saga_fatal("SAGA-R180: dispatch registry out of memory",80);}
        saga_dispatch_types=(SagaDispatchType*)next;
        memset(saga_dispatch_types+saga_dispatch_type_cap,0,(size_t)((cap-saga_dispatch_type_cap)*sizeof(SagaDispatchType)));
        saga_dispatch_type_cap=cap;
    }
    SagaDispatchType *entry=&saga_dispatch_types[saga_dispatch_type_len++];entry->type_id=type_id;entry->base_type_id=base_type_id;
    saga_dispatch_lock_release();
}
void saga_dispatch_register_interface(uint64_t type_id,uint64_t interface_id){
    if(!interface_id)return;
    saga_dispatch_lock_acquire();
    SagaDispatchType *entry=saga_dispatch_type_find_unlocked(type_id);
    if(!entry){saga_dispatch_lock_release();saga_fatal("SAGA-R202: register type before interface",102);}
    for(uint64_t i=0;i<entry->interface_len;++i)if(entry->interfaces[i]==interface_id){saga_dispatch_lock_release();return;}
    if(entry->interface_len==entry->interface_cap){
        uint64_t cap=entry->interface_cap?entry->interface_cap*2:4;
        void *next=realloc(entry->interfaces,(size_t)(cap*sizeof(uint64_t)));
        if(!next){saga_dispatch_lock_release();saga_fatal("SAGA-R180: dispatch registry out of memory",80);}
        entry->interfaces=(uint64_t*)next;entry->interface_cap=cap;
    }
    entry->interfaces[entry->interface_len++]=interface_id;
    saga_dispatch_lock_release();
}
void saga_dispatch_register_method(uint64_t type_id,uint64_t slot,SagaDispatchThunk thunk){
    if(!thunk||!slot)saga_fatal("SAGA-R203: invalid dynamic method registration",103);
    saga_dispatch_lock_acquire();
    for(uint64_t i=0;i<saga_dispatch_method_len;++i)if(saga_dispatch_methods[i].type_id==type_id&&saga_dispatch_methods[i].slot==slot){
        uint8_t conflict=saga_dispatch_methods[i].thunk!=thunk;saga_dispatch_lock_release();if(conflict)saga_fatal("SAGA-R204: conflicting dynamic method registration",104);return;
    }
    if(saga_dispatch_method_len==saga_dispatch_method_cap){
        uint64_t cap=saga_dispatch_method_cap?saga_dispatch_method_cap*2:32;
        void *next=realloc(saga_dispatch_methods,(size_t)(cap*sizeof(SagaDispatchMethod)));
        if(!next){saga_dispatch_lock_release();saga_fatal("SAGA-R180: dispatch registry out of memory",80);}
        saga_dispatch_methods=(SagaDispatchMethod*)next;saga_dispatch_method_cap=cap;
    }
    saga_dispatch_methods[saga_dispatch_method_len++]=(SagaDispatchMethod){type_id,slot,thunk};
    saga_dispatch_lock_release();
}
static uint8_t saga_dispatch_is_a_depth_unlocked(uint64_t type_id,uint64_t expected,uint32_t depth){
    if(type_id==expected)return 1;if(!type_id||depth>1024)return 0;SagaDispatchType *entry=saga_dispatch_type_find_unlocked(type_id);if(!entry)return 0;
    for(uint64_t i=0;i<entry->interface_len;++i){uint64_t iface=entry->interfaces[i];if(iface==expected||saga_dispatch_is_a_depth_unlocked(iface,expected,depth+1))return 1;}
    return entry->base_type_id?saga_dispatch_is_a_depth_unlocked(entry->base_type_id,expected,depth+1):0;
}
uint8_t saga_dispatch_is_a(uint64_t type_id,uint64_t expected_type_id){saga_dispatch_lock_acquire();uint8_t result=saga_dispatch_is_a_depth_unlocked(type_id,expected_type_id,0);saga_dispatch_lock_release();return result;}
static SagaDispatchThunk saga_dispatch_lookup_unlocked(uint64_t type_id,uint64_t slot){
    uint32_t depth=0;while(type_id&&depth++<1024){for(uint64_t i=0;i<saga_dispatch_method_len;++i)if(saga_dispatch_methods[i].type_id==type_id&&saga_dispatch_methods[i].slot==slot)return saga_dispatch_methods[i].thunk;SagaDispatchType *entry=saga_dispatch_type_find_unlocked(type_id);if(!entry)break;type_id=entry->base_type_id;}return NULL;
}
void saga_dispatch_invoke(uint64_t type_id,uint64_t expected_type_id,uint64_t slot,SagaRef self,const void *const *args,void *result){
    if(!self||saga_object_type_id(self)!=type_id)saga_fatal("SAGA-R190: native method receiver type mismatch",90);
    saga_dispatch_lock_acquire();
    uint8_t assignable=saga_dispatch_is_a_depth_unlocked(type_id,expected_type_id,0);
    SagaDispatchThunk thunk=assignable?saga_dispatch_lookup_unlocked(type_id,slot):NULL;
    saga_dispatch_lock_release();
    if(!assignable)saga_fatal("SAGA-R190: native method receiver type mismatch",90);
    if(!thunk)saga_fatal("SAGA-R193: native virtual dispatch target missing",93);
    thunk(self,args,result);
}
uint64_t saga_dispatch_registered_types(void){saga_dispatch_lock_acquire();uint64_t value=saga_dispatch_type_len;saga_dispatch_lock_release();return value;}
uint64_t saga_dispatch_registered_methods(void){saga_dispatch_lock_acquire();uint64_t value=saga_dispatch_method_len;saga_dispatch_lock_release();return value;}

typedef struct SagaAllocHeader {
    size_t capacity;
    size_t requested;
    struct SagaAllocHeader *next;
} SagaAllocHeader;

#define SAGA_ALLOC_BIN_COUNT 10
static const size_t saga_alloc_bin_sizes[SAGA_ALLOC_BIN_COUNT] = {16,32,64,128,256,512,1024,2048,4096,8192};
static SagaAllocHeader *saga_alloc_bins[SAGA_ALLOC_BIN_COUNT] = {0};
static uint64_t saga_allocator_live = 0;
static uint64_t saga_allocator_peak = 0;
static uint64_t saga_allocator_reserved = 0;

static int saga_alloc_bin_for(size_t size) {
    for (int i=0;i<SAGA_ALLOC_BIN_COUNT;++i) if (size <= saga_alloc_bin_sizes[i]) return i;
    return -1;
}
static void saga_allocator_account_add(size_t requested) {
    saga_allocator_live += (uint64_t)requested;
    if (saga_allocator_live > saga_allocator_peak) saga_allocator_peak = saga_allocator_live;
}
static void *saga_alloc_bytes(size_t requested) {
    size_t size = requested ? requested : 1;
    int bin = saga_alloc_bin_for(size);
    SagaAllocHeader *header = NULL;
    if (bin >= 0 && saga_alloc_bins[bin]) {
        header = saga_alloc_bins[bin];
        saga_alloc_bins[bin] = header->next;
    } else {
        size_t capacity = bin >= 0 ? saga_alloc_bin_sizes[bin] : size;
        header = (SagaAllocHeader*)malloc(sizeof(SagaAllocHeader) + capacity);
        if (!header) saga_fatal("SAGA-R180: Saga allocator out of memory", 80);
        header->capacity = capacity;
        saga_allocator_reserved += (uint64_t)(sizeof(SagaAllocHeader) + capacity);
    }
    header->requested = requested;
    header->next = NULL;
    memset(header + 1, 0, header->capacity);
    saga_allocator_account_add(requested);
    return (void*)(header + 1);
}
static void saga_free_bytes(void *ptr) {
    if (!ptr) return;
    SagaAllocHeader *header = ((SagaAllocHeader*)ptr) - 1;
    if (saga_allocator_live >= header->requested) saga_allocator_live -= (uint64_t)header->requested;
    else saga_allocator_live = 0;
    int bin = saga_alloc_bin_for(header->capacity);
    if (bin >= 0 && saga_alloc_bin_sizes[bin] == header->capacity) {
        header->requested = 0;
        header->next = saga_alloc_bins[bin];
        saga_alloc_bins[bin] = header;
        return;
    }
    saga_allocator_reserved -= (uint64_t)(sizeof(SagaAllocHeader) + header->capacity);
    free(header);
}
static void saga_account_bytes_direct(void *ptr) {
    if (!ptr) return;
    SagaAllocHeader *header = ((SagaAllocHeader*)ptr) - 1;
    if (saga_allocator_live >= header->requested) saga_allocator_live -= (uint64_t)header->requested;
    else saga_allocator_live = 0;
    if (saga_allocator_reserved >= sizeof(SagaAllocHeader) + header->capacity)
        saga_allocator_reserved -= (uint64_t)(sizeof(SagaAllocHeader) + header->capacity);
    header->requested = 0;
}
static void saga_free_bytes_direct(void *ptr) {
    if (!ptr) return;
    SagaAllocHeader *header = ((SagaAllocHeader*)ptr) - 1;
    free(header);
}
static void *saga_resize_bytes(void *ptr, size_t requested) {
    if (!ptr) return saga_alloc_bytes(requested);
    SagaAllocHeader *header = ((SagaAllocHeader*)ptr) - 1;
    size_t size = requested ? requested : 1;
    if (size <= header->capacity && (size > header->capacity/2 || saga_alloc_bin_for(size) == saga_alloc_bin_for(header->capacity))) {
        size_t old = header->requested;
        if (requested > old) memset((unsigned char*)ptr + old, 0, requested - old);
        if (saga_allocator_live >= old) saga_allocator_live -= (uint64_t)old; else saga_allocator_live = 0;
        header->requested = requested; saga_allocator_account_add(requested); return ptr;
    }
    size_t old = header->requested;
    void *next = saga_alloc_bytes(requested);
    memcpy(next, ptr, old < requested ? old : requested);
    saga_free_bytes(ptr);
    return next;
}
uint64_t saga_allocator_live_bytes(void) { return saga_allocator_live; }
uint64_t saga_allocator_peak_bytes(void) { return saga_allocator_peak; }
uint64_t saga_allocator_reserved_bytes(void) { return saga_allocator_reserved; }
static void saga_allocator_shutdown(void) {
    for (int i=0;i<SAGA_ALLOC_BIN_COUNT;++i) {
        SagaAllocHeader *node = saga_alloc_bins[i];
        while (node) {
            SagaAllocHeader *next = node->next;
            if (saga_allocator_reserved >= sizeof(SagaAllocHeader)+node->capacity)
                saga_allocator_reserved -= (uint64_t)(sizeof(SagaAllocHeader)+node->capacity);
            free(node); node = next;
        }
        saga_alloc_bins[i] = NULL;
    }
}

static void saga_gc_collect_sync_internal(void);
static SagaRef saga_heap_new(uint8_t kind, uint64_t type_id, uint8_t key_kind, uint8_t value_kind, uint64_t reserve_slots);

int64_t saga_abi035_add_i64(int64_t a, int64_t b) { int64_t r; if (__builtin_add_overflow(a,b,&r)) saga_abi035_overflow(); return r; }
int64_t saga_abi035_sub_i64(int64_t a, int64_t b) { int64_t r; if (__builtin_sub_overflow(a,b,&r)) saga_abi035_overflow(); return r; }
int64_t saga_abi035_mul_i64(int64_t a, int64_t b) { int64_t r; if (__builtin_mul_overflow(a,b,&r)) saga_abi035_overflow(); return r; }
int64_t saga_abi035_neg_i64(int64_t a) { if (a==INT64_MIN) saga_abi035_overflow(); return -a; }
int64_t saga_abi035_abs_i64(int64_t a) { return a < 0 ? saga_abi035_neg_i64(a) : a; }
int64_t saga_abi035_mod_i64(int64_t a, int64_t b) { if (b==0) saga_abi035_mod_zero(); if (a==INT64_MIN && b==-1) return 0; int64_t r=a%b; if (r!=0 && ((r<0)!=(b<0))) r=saga_abi035_add_i64(r,b); return r; }

static int64_t saga_abi035_q31_require(int64_t value) {
    if (value < (-INT64_C(2147483647) - INT64_C(1)) || value > INT64_C(2147483647))
        saga_fatal("SAGA-R196: Q1.31 operand out of range", 96);
    return value;
}
static int64_t saga_abi035_q31_sat(int64_t value) {
    if (value > INT64_C(2147483647)) return INT64_C(2147483647);
    if (value < (-INT64_C(2147483647) - INT64_C(1))) return (-INT64_C(2147483647) - INT64_C(1));
    return value;
}
int64_t saga_abi035_machine_q31_from_ratio(int64_t numerator, int64_t denominator) {
    saga_abi035_q31_require(numerator);
    if (denominator <= 0 || denominator > INT64_C(2147483647))
        saga_fatal("SAGA-R196: Q1.31 denominator out of range", 96);
    if (numerator >= denominator) return INT64_C(2147483647);
    if (numerator <= -denominator) return (-INT64_C(2147483647) - INT64_C(1));
    return (numerator * INT64_C(2147483648)) / denominator;
}
int64_t saga_abi035_machine_q31_add_sat(int64_t left, int64_t right) {
    return saga_abi035_q31_sat(saga_abi035_q31_require(left) + saga_abi035_q31_require(right));
}
int64_t saga_abi035_machine_q31_sub_sat(int64_t left, int64_t right) {
    return saga_abi035_q31_sat(saga_abi035_q31_require(left) - saga_abi035_q31_require(right));
}
int64_t saga_abi035_machine_q31_mul_sat(int64_t left, int64_t right) {
    int64_t product = saga_abi035_q31_require(left) * saga_abi035_q31_require(right);
    return saga_abi035_q31_sat(product / INT64_C(2147483648));
}
int64_t saga_abi035_machine_q31_mac_sat(int64_t accumulator, int64_t left, int64_t right) {
    int64_t product = saga_abi035_machine_q31_mul_sat(left, right);
    return saga_abi035_machine_q31_add_sat(accumulator, product);
}
uint8_t saga_abi035_text_equal(SagaText a, SagaText b) { return (uint8_t)(a.len==b.len && (a.len==0 || memcmp(a.data,b.data,(size_t)a.len)==0)); }

static SagaText saga_text_owned_bytes(const uint8_t *data, uint64_t len) {
    SagaRef owner = saga_heap_new(SAGA_HEAP_TEXT, 0, 0, 0, 0);
    owner->byte_len = len;
    if (len) {
        owner->bytes = (uint8_t*)saga_alloc_bytes((size_t)len);
        memcpy(owner->bytes, data, (size_t)len);
        saga_bytes_live += len;
    }
    return (SagaText){owner->bytes, len, owner};
}
SagaText saga_abi035_text_owned_copy(SagaText value) { return saga_text_owned_bytes(value.data, value.len); }
SagaText saga_abi035_text_concat(SagaText a, SagaText b) {
    if (a.len > UINT64_MAX - b.len) saga_fatal("SAGA-R193: native text length overflow", 93);
    uint64_t len = a.len + b.len;
    SagaRef owner = saga_heap_new(SAGA_HEAP_TEXT, 0, 0, 0, 0);
    owner->byte_len = len;
    if (len) {
        owner->bytes = (uint8_t*)saga_alloc_bytes((size_t)len);
        if (a.len) memcpy(owner->bytes, a.data, (size_t)a.len);
        if (b.len) memcpy(owner->bytes+a.len, b.data, (size_t)b.len);
        saga_bytes_live += len;
    }
    return (SagaText){owner->bytes, len, owner};
}
SagaText saga_abi035_text_from_i64(int64_t value) {
    char buf[64]; int n = snprintf(buf, sizeof(buf), "%" PRId64, value);
    if (n < 0) saga_fatal("SAGA-R194: native text formatting failed", 94);
    return saga_text_owned_bytes((const uint8_t*)buf, (uint64_t)n);
}
SagaText saga_abi035_text_from_bool(uint8_t value) {
    const char *raw = value ? "true" : "false";
    return saga_text_owned_bytes((const uint8_t*)raw, value ? 4 : 5);
}
uint8_t saga_abi035_text_is_owned(SagaText value) { return (uint8_t)(value.owner != NULL); }

uint8_t saga_abi035_tagged_equal(SagaTagged a, SagaTagged b) {
    if (a.type_id != b.type_id || a.tag != b.tag || a.arity != b.arity) return 0;
    for (uint8_t i=0; i<a.arity; ++i) {
        if (a.kinds[i] != b.kinds[i]) return 0;
        switch (a.kinds[i]) {
            case SAGA_HV_I64: if (a.payload[i].i64 != b.payload[i].i64) return 0; break;
            case SAGA_HV_BOOL: if (a.payload[i].boolean != b.payload[i].boolean) return 0; break;
            case SAGA_HV_TEXT: if (!saga_abi035_text_equal(a.payload[i].text, b.payload[i].text)) return 0; break;
            case SAGA_HV_REF: if (a.payload[i].ref != b.payload[i].ref) return 0; break;
            default: return 0;
        }
    }
    return 1;
}
void saga_abi035_print_i64(int64_t value) { printf("%" PRId64 "\n", value); }
void saga_abi035_print_bool(uint8_t value) { fputs(value ? "true\n" : "false\n", stdout); }
void saga_abi035_print_text(SagaText value) { if (value.len) fwrite(value.data,1,(size_t)value.len,stdout); fputc('\n',stdout); }
void saga_abi035_print_tagged(SagaTagged value) { printf("enum(0x%016" PRIx64 ",%" PRIu32 ")\n", value.type_id, value.tag); }
void saga_abi035_print_unit(void) { fputs("unit\n", stdout); }

static uint8_t saga_heap_value_equal(SagaHeapValue a, SagaHeapValue b) {
    if (a.kind != b.kind) return 0;
    switch (a.kind) {
        case SAGA_HV_I64: return (uint8_t)(a.value.i64 == b.value.i64);
        case SAGA_HV_BOOL: return (uint8_t)(a.value.boolean == b.value.boolean);
        case SAGA_HV_TEXT: return saga_abi035_text_equal(a.value.text, b.value.text);
        case SAGA_HV_TAGGED: return saga_abi035_tagged_equal(a.value.tagged, b.value.tagged);
        case SAGA_HV_REF: return (uint8_t)(a.value.ref == b.value.ref);
        default: return 0;
    }
}

static void saga_gray_push(SagaRef value) {
    if (!value) return;
    if (saga_gray_len == saga_gray_cap) {
        uint64_t next = saga_gray_cap ? saga_gray_cap*2 : 64;
        saga_gray = (SagaRef*)saga_resize_bytes(saga_gray, (size_t)(next*sizeof(SagaRef)));
        saga_gray_cap = next;
    }
    saga_gray[saga_gray_len++] = value;
}
static void saga_mark_ref_major(SagaRef value);
static void saga_mark_text_major(SagaText value) { if (value.owner) saga_mark_ref_major(value.owner); }
static void saga_mark_raw_major(uint8_t kind, SagaValue value);
static void saga_mark_tagged_major(SagaTagged value) {
    for (uint8_t i=0;i<value.arity;++i) {
        SagaValue raw = {0};
        switch(value.kinds[i]) {
            case SAGA_HV_I64: raw.i64=value.payload[i].i64; break;
            case SAGA_HV_BOOL: raw.boolean=value.payload[i].boolean; break;
            case SAGA_HV_TEXT: raw.text=value.payload[i].text; break;
            case SAGA_HV_REF: raw.ref=value.payload[i].ref; break;
            default: continue;
        }
        saga_mark_raw_major(value.kinds[i], raw);
    }
}
static void saga_mark_raw_major(uint8_t kind, SagaValue value) {
    if (kind==SAGA_HV_REF) saga_mark_ref_major(value.ref);
    else if (kind==SAGA_HV_TAGGED) saga_mark_tagged_major(value.tagged);
    else if (kind==SAGA_HV_TEXT) saga_mark_text_major(value.text);
}
static void saga_mark_value_major(SagaHeapValue value) { saga_mark_raw_major(value.kind, value.value); }
static void saga_mark_ref_major(SagaRef value) {
    if (!value || value->marked) return;
    value->marked = 1; saga_gray_push(value);
}
static void saga_scan_major(SagaRef value) {
    if (!value || value->heap_kind==SAGA_HEAP_TEXT) return;
    uint64_t slots = value->heap_kind==SAGA_HEAP_MAP ? value->len*2 : value->len;
    for (uint64_t i=0;i<slots;++i) saga_mark_value_major(value->items[i]);
}
static void saga_mark_value_minor(SagaHeapValue value);
static uint8_t saga_raw_has_young(uint8_t kind, SagaValue value) {
    if (kind==SAGA_HV_REF) return (uint8_t)(value.ref && value.ref->generation==0);
    if (kind==SAGA_HV_TEXT) return (uint8_t)(value.text.owner && value.text.owner->generation==0);
    if (kind==SAGA_HV_TAGGED) {
        for(uint8_t i=0;i<value.tagged.arity;++i){
            SagaValue raw={0}; uint8_t k=value.tagged.kinds[i];
            if(k==SAGA_HV_REF) raw.ref=value.tagged.payload[i].ref;
            else if(k==SAGA_HV_TEXT) raw.text=value.tagged.payload[i].text;
            if((k==SAGA_HV_REF||k==SAGA_HV_TEXT)&&saga_raw_has_young(k,raw)) return 1;
        }
    }
    return 0;
}
static void saga_write_barrier(SagaRef container, SagaHeapValue value) {
    if (!container) return;
    if (container->generation && saga_raw_has_young(value.kind, value.value)) container->remembered=1;
    if (saga_phase==SAGA_GC_MARKING && container->marked) saga_mark_value_major(value);
    if (saga_phase==SAGA_GC_MINOR_MARKING && (container->marked || container->generation)) saga_mark_value_minor(value);
}

static void saga_mark_ref_minor(SagaRef value);
static void saga_mark_text_minor(SagaText value) { if(value.owner) saga_mark_ref_minor(value.owner); }
static void saga_mark_raw_minor(uint8_t kind, SagaValue value) {
    if(kind==SAGA_HV_REF) saga_mark_ref_minor(value.ref);
    else if(kind==SAGA_HV_TEXT) saga_mark_text_minor(value.text);
    else if(kind==SAGA_HV_TAGGED){
        for(uint8_t i=0;i<value.tagged.arity;++i){
            SagaValue raw={0}; uint8_t k=value.tagged.kinds[i];
            if(k==SAGA_HV_REF)raw.ref=value.tagged.payload[i].ref;
            else if(k==SAGA_HV_TEXT)raw.text=value.tagged.payload[i].text;
            saga_mark_raw_minor(k,raw);
        }
    }
}
static void saga_mark_value_minor(SagaHeapValue value){ saga_mark_raw_minor(value.kind,value.value); }
static void saga_scan_minor(SagaRef value){
    if(!value||value->heap_kind==SAGA_HEAP_TEXT)return;
    uint64_t slots=value->heap_kind==SAGA_HEAP_MAP?value->len*2:value->len;
    for(uint64_t i=0;i<slots;++i)saga_mark_value_minor(value->items[i]);
}
static void saga_mark_ref_minor(SagaRef value){
    if(!value || value->generation || value->marked)return;
    value->marked=1; saga_gray_push(value);
}
static void saga_queue_remembered_minor(void){
    for(SagaRef obj=saga_heap_head;obj;obj=obj->next){
        if(obj->generation&&obj->remembered&&!obj->marked){obj->marked=1;saga_gray_push(obj);}
    }
}
static uint8_t saga_object_has_young(SagaRef value){
    if(!value||value->heap_kind==SAGA_HEAP_TEXT)return 0;
    uint64_t slots=value->heap_kind==SAGA_HEAP_MAP?value->len*2:value->len;
    for(uint64_t i=0;i<slots;++i)if(saga_raw_has_young(value->items[i].kind,value->items[i].value))return 1;
    return 0;
}

static void saga_mark_roots_major(void) {
    for(uint64_t i=0;i<saga_roots_len;++i){
        SagaRoot *root=&saga_roots[i]; if(!root->slot)continue;
        if(root->kind==SAGA_ROOT_REF)saga_mark_ref_major(*(volatile SagaRef*)root->slot);
        else if(root->kind==SAGA_ROOT_TEXT)saga_mark_text_major(*(volatile SagaText*)root->slot);
        else if(root->kind==SAGA_ROOT_TAGGED)saga_mark_tagged_major(*(volatile SagaTagged*)root->slot);
        else if(root->kind==SAGA_ROOT_OPTION){SagaOption v=*(volatile SagaOption*)root->slot;if(v.present)saga_mark_raw_major(root->a,v.value);}
        else if(root->kind==SAGA_ROOT_RESULT){SagaResult v=*(volatile SagaResult*)root->slot;saga_mark_raw_major(v.ok?root->a:root->b,v.value);}
    }
    if(saga_exception_present){ saga_mark_text_major(saga_current_exception.kind); saga_mark_text_major(saga_current_exception.message); }
}
static void saga_mark_roots_minor(void) {
    for(uint64_t i=0;i<saga_roots_len;++i){
        SagaRoot *root=&saga_roots[i]; if(!root->slot)continue;
        if(root->kind==SAGA_ROOT_REF)saga_mark_ref_minor(*(volatile SagaRef*)root->slot);
        else if(root->kind==SAGA_ROOT_TEXT)saga_mark_text_minor(*(volatile SagaText*)root->slot);
        else if(root->kind==SAGA_ROOT_TAGGED){SagaTagged v=*(volatile SagaTagged*)root->slot; for(uint8_t j=0;j<v.arity;++j){SagaValue raw={0};uint8_t k=v.kinds[j];if(k==SAGA_HV_REF)raw.ref=v.payload[j].ref;else if(k==SAGA_HV_TEXT)raw.text=v.payload[j].text;saga_mark_raw_minor(k,raw);}}
        else if(root->kind==SAGA_ROOT_OPTION){SagaOption v=*(volatile SagaOption*)root->slot;if(v.present)saga_mark_raw_minor(root->a,v.value);}
        else if(root->kind==SAGA_ROOT_RESULT){SagaResult v=*(volatile SagaResult*)root->slot;saga_mark_raw_minor(v.ok?root->a:root->b,v.value);}
    }
    if(saga_exception_present){saga_mark_text_minor(saga_current_exception.kind);saga_mark_text_minor(saga_current_exception.message);}
}

uint64_t saga_gc_root_mark(void) { return saga_roots_len; }
static void saga_gc_push_root(uint8_t kind,uint8_t a,uint8_t b,volatile void *slot){
    if(saga_roots_len==saga_roots_cap){uint64_t next=saga_roots_cap?saga_roots_cap*2:32;saga_roots=(SagaRoot*)saga_resize_bytes(saga_roots,(size_t)(next*sizeof(SagaRoot)));saga_roots_cap=next;}
    saga_roots[saga_roots_len++]=(SagaRoot){kind,a,b,slot};
}
void saga_gc_root_ref(volatile SagaRef *slot){saga_gc_push_root(SAGA_ROOT_REF,0,0,slot);}
void saga_gc_root_text(volatile SagaText *slot){saga_gc_push_root(SAGA_ROOT_TEXT,0,0,slot);}
void saga_gc_root_tagged(volatile SagaTagged *slot){saga_gc_push_root(SAGA_ROOT_TAGGED,0,0,slot);}
void saga_gc_root_option(volatile SagaOption *slot,uint8_t payload_kind){saga_gc_push_root(SAGA_ROOT_OPTION,payload_kind,0,slot);}
void saga_gc_root_result(volatile SagaResult *slot,uint8_t ok_kind,uint8_t err_kind){saga_gc_push_root(SAGA_ROOT_RESULT,ok_kind,err_kind,slot);}
void saga_gc_unwind_roots(uint64_t mark){if(mark<=saga_roots_len)saga_roots_len=mark;}

static void saga_release_object_sync(SagaRef obj){
    if(obj->items)saga_free_bytes(obj->items);
    if(obj->bytes)saga_free_bytes(obj->bytes);
    saga_free_bytes(obj);
}
static void saga_release_object_direct(SagaRef obj){
    if(obj->items)saga_free_bytes_direct(obj->items);
    if(obj->bytes)saga_free_bytes_direct(obj->bytes);
    saga_free_bytes_direct(obj);
}
#if SAGA_HAS_C11_THREADS
static thrd_t saga_sweep_thread;
static uint8_t saga_sweep_running=0;
static int saga_sweep_worker(void *arg){SagaRef node=(SagaRef)arg;while(node){SagaRef next=node->next;saga_release_object_direct(node);node=next;}return 0;}
static void saga_join_sweep(void){if(saga_sweep_running){int ignored=0;thrd_join(saga_sweep_thread,&ignored);saga_sweep_running=0;saga_phase=SAGA_GC_IDLE;}}
#else
static void saga_join_sweep(void){if(saga_phase==SAGA_GC_SWEEP_PENDING)saga_phase=SAGA_GC_IDLE;}
#endif
static void saga_start_sweep(SagaRef dead,uint8_t concurrent){
    if(!dead){saga_phase=SAGA_GC_IDLE;return;}
#if SAGA_HAS_C11_THREADS
    if(concurrent){
        saga_join_sweep();
        /* Detach allocator accounting on the mutator thread. The worker only
           calls free(), so it never races on counters or reuse bins. */
        for(SagaRef node=dead;node;node=node->next){
            saga_account_bytes_direct(node->items);
            saga_account_bytes_direct(node->bytes);
            saga_account_bytes_direct(node);
        }
        saga_phase=SAGA_GC_SWEEP_PENDING;
        if(thrd_create(&saga_sweep_thread,saga_sweep_worker,dead)==thrd_success){saga_sweep_running=1;++saga_concurrent_sweeps_count;return;}
        (void)saga_sweep_worker(dead);saga_phase=SAGA_GC_IDLE;return;
    }
#else
    (void)concurrent;
#endif
    while(dead){SagaRef next=dead->next;saga_release_object_sync(dead);dead=next;}saga_phase=SAGA_GC_IDLE;
}
static SagaRef saga_detach_dead_major(void){
    SagaRef dead=NULL; SagaHeapObject **cursor=&saga_heap_head;
    while(*cursor){SagaRef obj=*cursor;if(!obj->marked){*cursor=obj->next;obj->next=dead;dead=obj;uint64_t payload=obj->cap*sizeof(SagaHeapValue)+obj->byte_len;if(saga_bytes_live>=sizeof(*obj)+payload)saga_bytes_live-=sizeof(*obj)+payload;else saga_bytes_live=0;--saga_live;}else{obj->marked=0;obj->generation=1;obj->age=2;cursor=&obj->next;}}
    return dead;
}
static uint8_t saga_incremental_sweep_step(uint64_t budget){
    if(!budget){saga_last_pause_work=0;return saga_phase;}
    if(!saga_incremental_sweep_cursor)saga_incremental_sweep_cursor=&saga_heap_head;
    uint64_t work=0;
    while(*saga_incremental_sweep_cursor&&work<budget){
        SagaRef obj=*saga_incremental_sweep_cursor; ++work;
        if(!obj->marked){
            *saga_incremental_sweep_cursor=obj->next;
            uint64_t payload=obj->cap*sizeof(SagaHeapValue)+obj->byte_len;
            if(saga_bytes_live>=sizeof(*obj)+payload)saga_bytes_live-=sizeof(*obj)+payload;else saga_bytes_live=0;
            if(saga_live)--saga_live; saga_release_object_sync(obj);
        }else{
            /* marked==2 means allocated after sweeping started: preserve nursery age. */
            if(obj->marked==1){obj->generation=1;obj->age=2;}
            obj->marked=0; saga_incremental_sweep_cursor=&obj->next;
        }
    }
    saga_last_pause_work=work;if(work>saga_max_pause_work)saga_max_pause_work=work;
    if(!*saga_incremental_sweep_cursor){saga_incremental_sweep_cursor=NULL;saga_phase=SAGA_GC_IDLE;++saga_collection_count;++saga_major_count;++saga_incremental_sweeps_count;}
    return saga_phase;
}
static void saga_begin_minor(void){
    saga_join_sweep();
    for(SagaRef obj=saga_heap_head;obj;obj=obj->next)obj->marked=0;
    saga_gray_len=0;saga_minor_sweep_cursor=NULL;saga_phase=SAGA_GC_MINOR_MARKING;
    saga_mark_roots_minor();saga_queue_remembered_minor();
}
static uint8_t saga_incremental_minor_sweep_step(uint64_t budget){
    if(!budget){saga_last_pause_work=0;return saga_phase;}
    if(!saga_minor_sweep_cursor)saga_minor_sweep_cursor=&saga_heap_head;
    uint64_t work=0;
    while(*saga_minor_sweep_cursor&&work<budget){
        SagaRef obj=*saga_minor_sweep_cursor;++work;
        if(obj->generation){obj->marked=0;saga_minor_sweep_cursor=&obj->next;continue;}
        if(!obj->marked){
            *saga_minor_sweep_cursor=obj->next;uint64_t payload=obj->cap*sizeof(SagaHeapValue)+obj->byte_len;
            if(saga_bytes_live>=sizeof(*obj)+payload)saga_bytes_live-=sizeof(*obj)+payload;else saga_bytes_live=0;
            if(saga_live)--saga_live;saga_release_object_sync(obj);
        }else{
            if(obj->marked==1 && ++obj->age>=2){obj->generation=1;obj->remembered=saga_object_has_young(obj);++saga_promotions;}
            obj->marked=0;saga_minor_sweep_cursor=&obj->next;
        }
    }
    saga_last_pause_work=work;if(work>saga_max_pause_work)saga_max_pause_work=work;
    if(!*saga_minor_sweep_cursor){saga_minor_sweep_cursor=NULL;saga_phase=SAGA_GC_IDLE;++saga_collection_count;++saga_minor_count;++saga_incremental_minor_count;saga_minor_allocations=0;}
    return saga_phase;
}
uint8_t saga_gc_minor_step(uint64_t budget){
    if(saga_phase==SAGA_GC_IDLE)saga_begin_minor();
    if(saga_phase==SAGA_GC_MINOR_SWEEPING)return saga_incremental_minor_sweep_step(budget);
    if(saga_phase!=SAGA_GC_MINOR_MARKING)return saga_phase;
    saga_mark_roots_minor();
    uint64_t work=0;
    while(saga_gray_len&&work<budget){SagaRef obj=saga_gray[--saga_gray_len];saga_scan_minor(obj);++work;}
    saga_last_pause_work=work;if(work>saga_max_pause_work)saga_max_pause_work=work;
    if(!saga_gray_len){saga_phase=SAGA_GC_MINOR_SWEEPING;saga_minor_sweep_cursor=&saga_heap_head;}
    return saga_phase;
}
void saga_gc_collect_minor(void){
    if(saga_phase!=SAGA_GC_IDLE){
        saga_sync_sweep=1;
        while(saga_phase==SAGA_GC_MARKING||saga_phase==SAGA_GC_SWEEPING||saga_phase==SAGA_GC_MINOR_MARKING||saga_phase==SAGA_GC_MINOR_SWEEPING)saga_gc_step(UINT64_MAX/4);
        saga_join_sweep();saga_sync_sweep=0;
    }
    saga_begin_minor();while(saga_phase==SAGA_GC_MINOR_MARKING||saga_phase==SAGA_GC_MINOR_SWEEPING)saga_gc_minor_step(UINT64_MAX/4);
}
static void saga_begin_major(void){
    saga_join_sweep();
    for(SagaRef obj=saga_heap_head;obj;obj=obj->next)obj->marked=0;
    saga_gray_len=0;saga_phase=SAGA_GC_MARKING;saga_mark_roots_major();
}
uint8_t saga_gc_step(uint64_t budget){
    if(saga_phase==SAGA_GC_MINOR_MARKING||saga_phase==SAGA_GC_MINOR_SWEEPING)return saga_gc_minor_step(budget);
    if(saga_phase==SAGA_GC_SWEEP_PENDING){saga_join_sweep();return saga_phase;}
    if(saga_phase==SAGA_GC_SWEEPING)return saga_incremental_sweep_step(budget);
    if(saga_phase==SAGA_GC_IDLE)saga_begin_major();
    saga_mark_roots_major(); /* root rescan is the incremental root write barrier */
    uint64_t work=0;
    while(saga_gray_len&&work<budget){SagaRef obj=saga_gray[--saga_gray_len];saga_scan_major(obj);++work;}
    saga_last_pause_work=work;if(work>saga_max_pause_work)saga_max_pause_work=work;
    if(!saga_gray_len){
        if(saga_low_pause_budget&&!saga_sync_sweep){saga_phase=SAGA_GC_SWEEPING;saga_incremental_sweep_cursor=&saga_heap_head;}
        else {SagaRef dead=saga_detach_dead_major();++saga_collection_count;++saga_major_count;saga_start_sweep(dead,(uint8_t)!saga_sync_sweep);}
    }
    return saga_phase;
}
static void saga_gc_collect_sync_internal(void){saga_sync_sweep=1;if(saga_phase==SAGA_GC_SWEEP_PENDING)saga_join_sweep();while(saga_phase==SAGA_GC_MINOR_MARKING||saga_phase==SAGA_GC_MINOR_SWEEPING)saga_gc_minor_step(UINT64_MAX/4);if(saga_phase==SAGA_GC_IDLE)saga_begin_major();while(saga_phase==SAGA_GC_MARKING||saga_phase==SAGA_GC_SWEEPING)saga_gc_step(UINT64_MAX/4);saga_join_sweep();saga_sync_sweep=0;}
void saga_gc_collect(void){saga_gc_collect_sync_internal();}
uint8_t saga_gc_phase(void){return saga_phase;}
uint64_t saga_gc_live_objects(void){return saga_live;}
uint64_t saga_gc_young_objects(void){uint64_t n=0;for(SagaRef o=saga_heap_head;o;o=o->next)if(!o->generation)++n;return n;}
uint64_t saga_gc_old_objects(void){uint64_t n=0;for(SagaRef o=saga_heap_head;o;o=o->next)if(o->generation)++n;return n;}
uint64_t saga_gc_collections(void){return saga_collection_count;}
uint64_t saga_gc_minor_collections(void){return saga_minor_count;}
uint64_t saga_gc_major_collections(void){return saga_major_count;}
uint64_t saga_gc_bytes(void){return saga_bytes_live;}
uint64_t saga_gc_promotions(void){return saga_promotions;}
uint8_t saga_gc_concurrent_sweep_available(void){return (uint8_t)SAGA_HAS_C11_THREADS;}
uint64_t saga_gc_concurrent_sweeps(void){return saga_concurrent_sweeps_count;}
void saga_gc_low_pause_enable(uint64_t object_budget){saga_low_pause_budget=object_budget;}
uint8_t saga_gc_poll(void){return saga_gc_step(saga_low_pause_budget?saga_low_pause_budget:1);}
uint64_t saga_gc_pause_budget(void){return saga_low_pause_budget;}
uint64_t saga_gc_last_pause_work(void){return saga_last_pause_work;}
uint64_t saga_gc_max_pause_work(void){return saga_max_pause_work;}
uint64_t saga_gc_incremental_sweeps(void){return saga_incremental_sweeps_count;}
uint64_t saga_gc_incremental_minor_collections(void){return saga_incremental_minor_count;}
uint8_t saga_gc_incremental_minor_available(void){return 1;}

static SagaRef saga_heap_new(uint8_t kind,uint64_t type_id,uint8_t key_kind,uint8_t value_kind,uint64_t reserve_slots){
    if(saga_minor_allocations>=64){
        if(saga_low_pause_budget){
            if(saga_phase==SAGA_GC_IDLE)saga_begin_minor();
            if(saga_phase==SAGA_GC_MINOR_MARKING||saga_phase==SAGA_GC_MINOR_SWEEPING)saga_gc_minor_step(saga_low_pause_budget);
            else saga_gc_step(saga_low_pause_budget);
        }else saga_gc_collect_minor();
    }
    SagaRef obj=(SagaRef)saga_alloc_bytes(sizeof(SagaHeapObject));obj->heap_kind=kind;obj->type_id=type_id;obj->key_kind=key_kind;obj->value_kind=value_kind;obj->generation=0;obj->age=0;obj->remembered=0;obj->cap=reserve_slots;
    if(reserve_slots)obj->items=(SagaHeapValue*)saga_alloc_bytes((size_t)(reserve_slots*sizeof(SagaHeapValue)));
    obj->marked=(uint8_t)((saga_phase==SAGA_GC_MARKING||saga_phase==SAGA_GC_MINOR_MARKING)?1:((saga_phase==SAGA_GC_SWEEPING||saga_phase==SAGA_GC_MINOR_SWEEPING)?2:0));obj->next=saga_heap_head;saga_heap_head=obj;++saga_live;++saga_minor_allocations;saga_bytes_live+=sizeof(*obj)+reserve_slots*sizeof(SagaHeapValue);
    if(saga_phase==SAGA_GC_MINOR_MARKING)saga_gray_push(obj);
    return obj;
}
static void saga_require_ref(SagaRef ref,uint8_t kind,const char *message){if(!ref||ref->heap_kind!=kind)saga_fatal(message,81);}
static void saga_reserve_slots(SagaRef ref,uint64_t slots){if(slots<=ref->cap)return;uint64_t next=ref->cap?ref->cap:4;while(next<slots)next*=2;size_t old_bytes=(size_t)(ref->cap*sizeof(SagaHeapValue));size_t new_bytes=(size_t)(next*sizeof(SagaHeapValue));ref->items=(SagaHeapValue*)saga_resize_bytes(ref->items,new_bytes);memset((uint8_t*)ref->items+old_bytes,0,new_bytes-old_bytes);saga_bytes_live+=(uint64_t)(new_bytes-old_bytes);ref->cap=next;}

SagaRef saga_list_new(uint8_t element_kind,uint64_t reserve){return saga_heap_new(SAGA_HEAP_LIST,0,0,element_kind,reserve);}
void saga_list_push(SagaRef list,SagaHeapValue value){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");if(list->value_kind!=value.kind)saga_fatal("SAGA-R182: native list element type mismatch",82);saga_reserve_slots(list,list->len+1);saga_write_barrier(list,value);list->items[list->len++]=value;}
static SagaRef saga_list_clone(SagaRef list,uint64_t extra){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");SagaRef out=saga_list_new(list->value_kind,list->len+extra);out->len=list->len;if(list->len)memcpy(out->items,list->items,(size_t)(list->len*sizeof(SagaHeapValue)));return out;}
SagaRef saga_list_append(SagaRef list,SagaHeapValue value){SagaRef out=saga_list_clone(list,1);saga_list_push(out,value);return out;}
SagaRef saga_list_prepend(SagaRef list,SagaHeapValue value){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");if(list->value_kind!=value.kind)saga_fatal("SAGA-R182: native list element type mismatch",82);SagaRef out=saga_list_new(list->value_kind,list->len+1);out->len=list->len+1;out->items[0]=value;if(list->len)memcpy(out->items+1,list->items,(size_t)(list->len*sizeof(SagaHeapValue)));return out;}
SagaRef saga_list_set_at(SagaRef list,int64_t index,SagaHeapValue value){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");if(index<0||(uint64_t)index>=list->len)saga_fatal("SAGA-R106: list index out of range",83);if(list->value_kind!=value.kind)saga_fatal("SAGA-R182: native list element type mismatch",82);SagaRef out=saga_list_clone(list,0);out->items[index]=value;return out;}
SagaHeapValue saga_list_get(SagaRef list,int64_t index){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");if(index<0||(uint64_t)index>=list->len)saga_fatal("SAGA-R106: list index out of range",83);return list->items[index];}
SagaHeapValue saga_list_get_or(SagaRef list,int64_t index,SagaHeapValue fallback){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");return(index>=0&&(uint64_t)index<list->len)?list->items[index]:fallback;}
uint8_t saga_list_contains(SagaRef list,SagaHeapValue value){saga_require_ref(list,SAGA_HEAP_LIST,"SAGA-R181: expected native list");if(list->value_kind!=value.kind)saga_fatal("SAGA-R182: native list element type mismatch",82);for(uint64_t i=0;i<list->len;++i)if(saga_heap_value_equal(list->items[i],value))return 1;return 0;}

SagaRef saga_map_new(uint8_t key_kind,uint8_t value_kind,uint64_t reserve){return saga_heap_new(SAGA_HEAP_MAP,0,key_kind,value_kind,reserve*2);}
static int64_t saga_map_index(SagaRef map,SagaHeapValue key){saga_require_ref(map,SAGA_HEAP_MAP,"SAGA-R183: expected native map");if(map->key_kind!=key.kind)saga_fatal("SAGA-R184: native map type mismatch",84);for(uint64_t i=0;i<map->len;++i)if(saga_heap_value_equal(map->items[i*2],key))return(int64_t)i;return-1;}
static SagaRef saga_map_clone(SagaRef map,uint64_t extra){saga_require_ref(map,SAGA_HEAP_MAP,"SAGA-R183: expected native map");SagaRef out=saga_map_new(map->key_kind,map->value_kind,map->len+extra);out->len=map->len;if(map->len)memcpy(out->items,map->items,(size_t)(map->len*2*sizeof(SagaHeapValue)));return out;}
SagaRef saga_map_put(SagaRef map,SagaHeapValue key,SagaHeapValue value){saga_require_ref(map,SAGA_HEAP_MAP,"SAGA-R183: expected native map");if(key.kind!=map->key_kind||value.kind!=map->value_kind)saga_fatal("SAGA-R184: native map type mismatch",84);int64_t found=saga_map_index(map,key);SagaRef out=saga_map_clone(map,found<0?1:0);if(found>=0){out->items[found*2+1]=value;return out;}saga_reserve_slots(out,(out->len+1)*2);out->items[out->len*2]=key;out->items[out->len*2+1]=value;++out->len;return out;}
SagaRef saga_map_remove(SagaRef map,SagaHeapValue key){int64_t found=saga_map_index(map,key);SagaRef out=saga_map_new(map->key_kind,map->value_kind,map->len);for(uint64_t i=0;i<map->len;++i){if((int64_t)i==found)continue;saga_reserve_slots(out,(out->len+1)*2);out->items[out->len*2]=map->items[i*2];out->items[out->len*2+1]=map->items[i*2+1];++out->len;}return out;}
SagaHeapValue saga_map_get_or(SagaRef map,SagaHeapValue key,SagaHeapValue fallback){int64_t found=saga_map_index(map,key);return found>=0?map->items[found*2+1]:fallback;}
uint8_t saga_map_contains(SagaRef map,SagaHeapValue key){return(uint8_t)(saga_map_index(map,key)>=0);}

SagaRef saga_set_new(uint8_t element_kind,uint64_t reserve){return saga_heap_new(SAGA_HEAP_SET,0,0,element_kind,reserve);}
uint8_t saga_set_contains(SagaRef set,SagaHeapValue value){saga_require_ref(set,SAGA_HEAP_SET,"SAGA-R185: expected native set");if(set->value_kind!=value.kind)saga_fatal("SAGA-R186: native set type mismatch",86);for(uint64_t i=0;i<set->len;++i)if(saga_heap_value_equal(set->items[i],value))return 1;return 0;}
SagaRef saga_set_add(SagaRef set,SagaHeapValue value){saga_require_ref(set,SAGA_HEAP_SET,"SAGA-R185: expected native set");if(set->value_kind!=value.kind)saga_fatal("SAGA-R186: native set type mismatch",86);SagaRef out=saga_heap_new(SAGA_HEAP_SET,0,0,set->value_kind,set->len+1);out->len=set->len;if(set->len)memcpy(out->items,set->items,(size_t)(set->len*sizeof(SagaHeapValue)));if(!saga_set_contains(set,value))out->items[out->len++]=value;return out;}
SagaRef saga_set_remove(SagaRef set,SagaHeapValue value){saga_require_ref(set,SAGA_HEAP_SET,"SAGA-R185: expected native set");if(set->value_kind!=value.kind)saga_fatal("SAGA-R186: native set type mismatch",86);SagaRef out=saga_set_new(set->value_kind,set->len);for(uint64_t i=0;i<set->len;++i)if(!saga_heap_value_equal(set->items[i],value))out->items[out->len++]=set->items[i];return out;}
SagaRef saga_set_union(SagaRef left,SagaRef right){saga_require_ref(left,SAGA_HEAP_SET,"SAGA-R185: expected native set");saga_require_ref(right,SAGA_HEAP_SET,"SAGA-R185: expected native set");if(left->value_kind!=right->value_kind)saga_fatal("SAGA-R186: native set type mismatch",86);SagaRef out=saga_set_new(left->value_kind,left->len+right->len);for(uint64_t i=0;i<left->len;++i)out->items[out->len++]=left->items[i];for(uint64_t i=0;i<right->len;++i)if(!saga_set_contains(out,right->items[i]))out->items[out->len++]=right->items[i];return out;}
SagaRef saga_set_intersection(SagaRef left,SagaRef right){saga_require_ref(left,SAGA_HEAP_SET,"SAGA-R185: expected native set");saga_require_ref(right,SAGA_HEAP_SET,"SAGA-R185: expected native set");if(left->value_kind!=right->value_kind)saga_fatal("SAGA-R186: native set type mismatch",86);SagaRef out=saga_set_new(left->value_kind,left->len);for(uint64_t i=0;i<left->len;++i)if(saga_set_contains(right,left->items[i]))out->items[out->len++]=left->items[i];return out;}

uint64_t saga_ref_len(SagaRef value){if(!value)return 0;if(value->heap_kind==SAGA_HEAP_MAP||value->heap_kind==SAGA_HEAP_LIST||value->heap_kind==SAGA_HEAP_SET)return value->len;saga_fatal("SAGA-R187: len requires list/map/set",87);return 0;}
SagaRef saga_object_new(uint64_t type_id,uint64_t field_count){SagaRef out=saga_heap_new(SAGA_HEAP_OBJECT,type_id,0,0,field_count);out->len=field_count;return out;}
uint64_t saga_object_type_id(SagaRef object){saga_require_ref(object,SAGA_HEAP_OBJECT,"SAGA-R188: expected native object");return object->type_id;}
void saga_object_set(SagaRef object,uint64_t index,SagaHeapValue value){saga_require_ref(object,SAGA_HEAP_OBJECT,"SAGA-R188: expected native object");if(index>=object->len)saga_fatal("SAGA-R189: invalid native object field",89);saga_write_barrier(object,value);object->items[index]=value;}
SagaHeapValue saga_object_get(SagaRef object,uint64_t index){saga_require_ref(object,SAGA_HEAP_OBJECT,"SAGA-R188: expected native object");if(index>=object->len)saga_fatal("SAGA-R189: invalid native object field",89);return object->items[index];}

void saga_exception_link(SagaExceptionFrame *frame,uint64_t root_mark){if(!frame)saga_fatal("SAGA-R195: invalid exception frame",95);frame->root_mark=root_mark;frame->previous=saga_exception_top;saga_exception_top=frame;}
void saga_exception_leave(SagaExceptionFrame *frame){if(saga_exception_top==frame)saga_exception_top=frame->previous;}
SagaException saga_exception_current(void){return saga_current_exception;}
void saga_exception_clear(void){saga_current_exception=(SagaException){{0},{0}};saga_exception_present=0;}
static void saga_raise_current(void){if(!saga_exception_top){fputs("SAGA-R196: uncaught exception: ",stderr);if(saga_current_exception.message.len)fwrite(saga_current_exception.message.data,1,(size_t)saga_current_exception.message.len,stderr);fputc('\n',stderr);exit(96);}SagaExceptionFrame *frame=saga_exception_top;saga_exception_top=frame->previous;saga_gc_unwind_roots(frame->root_mark);longjmp(frame->env,1);}
void saga_throw_text(SagaText message){saga_current_exception.kind=(SagaText){(const uint8_t*)"Thrown",6,NULL};saga_current_exception.message=saga_abi035_text_owned_copy(message);saga_exception_present=1;saga_raise_current();}
void saga_throw_i64(int64_t value){SagaText t=saga_abi035_text_from_i64(value);saga_current_exception.kind=(SagaText){(const uint8_t*)"Thrown",6,NULL};saga_current_exception.message=t;saga_exception_present=1;saga_raise_current();}
void saga_throw_bool(uint8_t value){SagaText t=saga_abi035_text_from_bool(value);saga_current_exception.kind=(SagaText){(const uint8_t*)"Thrown",6,NULL};saga_current_exception.message=t;saga_exception_present=1;saga_raise_current();}
void saga_exception_rethrow(void){if(!saga_exception_present)saga_fatal("SAGA-R197: rethrow without exception",97);saga_raise_current();}

static void saga_print_heap_value(SagaHeapValue value){switch(value.kind){case SAGA_HV_I64:printf("%" PRId64,value.value.i64);break;case SAGA_HV_BOOL:fputs(value.value.boolean?"true":"false",stdout);break;case SAGA_HV_TEXT:if(value.value.text.len)fwrite(value.value.text.data,1,(size_t)value.value.text.len,stdout);break;case SAGA_HV_TAGGED:printf("enum(0x%016" PRIx64 ",%" PRIu32 ")",value.value.tagged.type_id,value.value.tagged.tag);break;case SAGA_HV_REF:printf("ref(%p)",(void*)value.value.ref);break;default:fputs("?",stdout);break;}}
void saga_abi035_print_ref(SagaRef value){if(!value){fputs("null\n",stdout);return;}if(value->heap_kind==SAGA_HEAP_LIST){fputc('[',stdout);for(uint64_t i=0;i<value->len;++i){if(i)fputs(", ",stdout);saga_print_heap_value(value->items[i]);}fputs("]\n",stdout);return;}if(value->heap_kind==SAGA_HEAP_SET){fputc('{',stdout);for(uint64_t i=0;i<value->len;++i){if(i)fputs(", ",stdout);saga_print_heap_value(value->items[i]);}fputs("}\n",stdout);return;}if(value->heap_kind==SAGA_HEAP_MAP){fputc('{',stdout);for(uint64_t i=0;i<value->len;++i){if(i)fputs(", ",stdout);saga_print_heap_value(value->items[i*2]);fputs(": ",stdout);saga_print_heap_value(value->items[i*2+1]);}fputs("}\n",stdout);return;}if(value->heap_kind==SAGA_HEAP_TEXT){SagaText text={value->bytes,value->byte_len,value};saga_abi035_print_text(text);return;}printf("object(0x%016" PRIx64 ")\n",value->type_id);}

void saga_gc_shutdown(void){saga_join_sweep();saga_incremental_sweep_cursor=NULL;saga_roots_len=0;saga_exception_clear();saga_gc_collect_sync_internal();saga_free_bytes(saga_roots);saga_roots=NULL;saga_roots_cap=0;saga_free_bytes(saga_gray);saga_gray=NULL;saga_gray_cap=0;saga_dispatch_lock_acquire();for(uint64_t i=0;i<saga_dispatch_type_len;++i)free(saga_dispatch_types[i].interfaces);free(saga_dispatch_types);saga_dispatch_types=NULL;saga_dispatch_type_len=saga_dispatch_type_cap=0;free(saga_dispatch_methods);saga_dispatch_methods=NULL;saga_dispatch_method_len=saga_dispatch_method_cap=0;saga_dispatch_lock_release();saga_allocator_shutdown();}
'''


def _block_has_control_transfer(block: ast.Block | None) -> bool:
    if block is None:
        return False
    def visit(stmt: ast.Stmt) -> bool:
        if isinstance(stmt, (ast.ReturnStmt, ast.BreakStmt, ast.ContinueStmt)):
            return True
        if isinstance(stmt, ast.IfStmt):
            return _block_has_control_transfer(stmt.then_branch) or _block_has_control_transfer(stmt.else_branch)
        if isinstance(stmt, (ast.WhileStmt, ast.ForStmt)):
            return _block_has_control_transfer(stmt.body)
        if isinstance(stmt, ast.MatchStmt):
            return any(_block_has_control_transfer(case.body) for case in stmt.cases) or _block_has_control_transfer(stmt.default)
        if isinstance(stmt, ast.TryStmt):
            return (_block_has_control_transfer(stmt.try_block) or _block_has_control_transfer(stmt.catch_block) or _block_has_control_transfer(stmt.finally_block))
        if isinstance(stmt, ast.Block):
            return _block_has_control_transfer(stmt)
        return False
    return any(visit(stmt) for stmt in block.statements if not isinstance(stmt, ast.FunctionDecl))


class ModuleCEmitter:
    def __init__(self, unit: ModuleUnit, units: dict[Path, ModuleUnit], abi_by_path: dict[Path, dict[str, object]]) -> None:
        self.unit = unit
        self.output_unit = unit
        self.units = units
        self.abi_by_path = abi_by_path
        self.lines: list[str] = []
        self.indent = 0
        self.temp = 0
        self.scopes: list[dict[str, str]] = []
        self.loop_stack: list[tuple[str, str, int, int]] = []
        self.gc_scope_marks: list[str] = []
        self.current_result = "unit"
        self.current_class: ClassABI | None = None
        self.exception_frames: list[str] = []
        self.finally_stack: list[tuple[ast.Block, int]] = []
        self.active_type_mapping: dict[str, str] = {}
        self.generic_function_specs: dict[tuple[str, tuple[str, ...]], tuple[ModuleUnit, ast.FunctionDecl, FunctionABI, dict[str, str]]] = {}
        self.generic_class_specs: dict[tuple[str, tuple[str, ...]], tuple[ModuleUnit, ClassABI, ClassABI, dict[str, str]]] = {}
        self.emitted_generic_functions: set[tuple[str, tuple[str, ...]]] = set()
        self.emitted_generic_classes: set[tuple[str, tuple[str, ...]]] = set()
        self.root_mark = "__saga_root_mark"
        self.class_by_identity: dict[str, ClassABI] = {
            cls.identity: cls for module in units.values() for cls in module.classes.values()
        }
        self.enum_by_identity: dict[str, EnumABI] = {
            enum.identity: enum for module in units.values() for enum in module.enums.values()
        }

    def _line(self, text: str = "") -> None:
        self.lines.append("    " * self.indent + text)

    @staticmethod
    def _var(name: str) -> str:
        return "saga_v_" + _symbol_component(name)

    def _root_if_ref(self, c_name: str, type_name: str) -> None:
        if _is_ref_type(type_name):
            self._line(f"saga_gc_root_ref(&{c_name});")
        elif _is_enum_type(type_name):
            self._line(f"saga_gc_root_tagged(&{c_name});")
        elif type_name == "text":
            self._line(f"saga_gc_root_text(&{c_name});")
        elif type_name.startswith("option["):
            inner = _inner_types(type_name)[0]
            self._line(f"saga_gc_root_option(&{c_name}, {_heap_kind(inner)});")
        elif type_name.startswith("result["):
            ok_t, err_t = _inner_types(type_name)
            self._line(f"saga_gc_root_result(&{c_name}, {_heap_kind(ok_t)}, {_heap_kind(err_t)});")
        elif type_name == "error":
            self._line(f"saga_gc_root_text(&{c_name}.kind);")
            self._line(f"saga_gc_root_text(&{c_name}.message);")

    def _new_temp(self, prefix: str, type_name: str) -> str:
        self.temp += 1
        name = f"__saga_{prefix}_{self.temp}"
        init = " = NULL" if _is_ref_type(type_name) else (" = {0}" if (_is_enum_type(type_name) or type_name == "text" or type_name.startswith(("option[", "result[")) or type_name == "error") else "")
        self._line(f"{_ctype(type_name)} {name}{init};")
        self._root_if_ref(name, type_name)
        return name

    def _declare(self, name: str, type_name: str) -> str:
        c = self._var(name)
        self.scopes[-1][name] = type_name
        return c

    def _declare_value(self, name: str, type_name: str, value: str) -> str:
        c = self._declare(name, type_name)
        needs_safe_init = _is_ref_type(type_name) or _is_enum_type(type_name) or type_name == "text" or type_name.startswith(("option[", "result[")) or type_name == "error"
        init = "NULL" if _is_ref_type(type_name) else (f"({_ctype(type_name)}){{0}}" if needs_safe_init else value)
        self._line(f"volatile {_ctype(type_name)} {c} = {init};")
        self._root_if_ref(c, type_name)
        if needs_safe_init:
            self._line(f"{c} = {value};")
        return c

    def _find_type(self, name: str) -> str | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def _lookup_type(self, name: str) -> str:
        found = self._find_type(name)
        if found is None:
            raise AOTError(f"Native Codegen could not resolve local '{name}' in {self.unit.path}")
        return found

    def _return(self, value: str | None = None) -> None:
        for frame in reversed(self.exception_frames):
            self._line(f"saga_exception_leave(&{frame});")
        if value is None:
            self._line(f"saga_gc_unwind_roots({self.root_mark});")
            self._line("return;")
        else:
            self._line(f"saga_gc_unwind_roots({self.root_mark});")
            self._line(f"return {value};")

    def _class_for_type(self, type_name: str) -> ClassABI:
        if not type_name.startswith("object[") or not type_name.endswith("]"):
            raise AOTError(f"expected Native Object ABI type, got {type_name}")
        identity = type_name[7:-1]
        cls = self.class_by_identity.get(identity)
        if cls is None:
            raise AOTError(f"Native Object ABI 0.35 cannot resolve class identity {identity}")
        return cls

    def _class_is_a(self, actual: ClassABI, expected: ClassABI) -> bool:
        if actual.identity == expected.identity:
            return True
        seen: set[str] = set()
        stack = [actual]
        while stack:
            current = stack.pop()
            if current.identity in seen:
                continue
            seen.add(current.identity)
            if current.base_identity:
                base = self.class_by_identity.get(current.base_identity)
                if base is not None:
                    if base.identity == expected.identity:
                        return True
                    stack.append(base)
            for iface_id in current.interface_identities:
                iface = self.class_by_identity.get(iface_id)
                if iface is not None:
                    if iface.identity == expected.identity:
                        return True
                    stack.append(iface)
        return False

    def _assignable(self, expected: str, actual: str) -> bool:
        if expected == actual:
            return True
        if expected.startswith("object[") and actual.startswith("object["):
            try:
                return self._class_is_a(self._class_for_type(actual), self._class_for_type(expected))
            except AOTError:
                return False
        return False

    def _pattern_kind(self, value: Type, mapping: dict[str, str]) -> str:
        if is_typevar(value):
            name = typevar_name(value)
            if name not in mapping:
                raise AOTError(f"unbound native generic type variable {name}")
            return mapping[name]
        if value.name == "int": return "int"
        if value.name == "bool": return "bool"
        if value.name == "text": return "text"
        if value.name == "unit": return "unit"
        if value.name in {"list", "set", "option"} and len(value.args) == 1:
            return f"{value.name}[{self._pattern_kind(value.args[0], mapping)}]"
        if value.name in {"map", "result"} and len(value.args) == 2:
            return f"{value.name}[{self._pattern_kind(value.args[0], mapping)},{self._pattern_kind(value.args[1], mapping)}]"
        if value.name.startswith("object:"):
            # Non-generic nominal components continue through the regular graph.
            if not value.args:
                return _abi_type(value, self.unit, self.units)
            raise AOTError(f"nested generic nominal type requires explicit aggregate specialization: {value}")
        return _abi_type(value, self.unit, self.units)

    def _unify_kind(self, pattern: Type, actual: str, mapping: dict[str, str]) -> bool:
        if is_typevar(pattern):
            name = typevar_name(pattern)
            previous = mapping.get(name)
            if previous is None:
                mapping[name] = actual
                return True
            return previous == actual
        if pattern.name in {"int", "bool", "text", "unit"}:
            return pattern.name == actual
        if pattern.name in {"list", "set", "option"} and len(pattern.args) == 1:
            values = _split_generic(actual, pattern.name)
            return len(values) == 1 and self._unify_kind(pattern.args[0], values[0], mapping)
        if pattern.name in {"map", "result"} and len(pattern.args) == 2:
            values = _split_generic(actual, pattern.name)
            return len(values) == 2 and all(self._unify_kind(p, a, mapping) for p, a in zip(pattern.args, values))
        if pattern.name.startswith("object:") and not pattern.args:
            try:
                return self._assignable(_abi_type(pattern, self.unit, self.units), actual)
            except AOTError:
                return False
        return False

    def _decl_type(self, raw: str | None) -> str:
        if raw is None:
            return "unit"
        if self.active_type_mapping:
            try:
                parsed = parse_type(raw, set(self.active_type_mapping))
                return self._pattern_kind(parsed, self.active_type_mapping)
            except (ValueError, AOTError):
                pass
        parsed = parse_type(raw)
        if parsed.name.startswith("object:") and parsed.args:
            nominal = parsed.name.split(":", 1)[1]
            owner = self.unit
            local_name = nominal
            if "." in nominal:
                alias, local_name = nominal.split(".", 1)
                dep_path = self.unit.imports.get(alias)
                if dep_path is None:
                    raise AOTError(f"Native Codegen ABI 0.35 cannot resolve qualified generic type '{nominal}'")
                owner = self.units[dep_path]
            template = owner.classes.get(local_name)
            if template is None or not template.type_params or (owner is not self.unit and template.visibility != "public"):
                raise AOTError(f"native generic aggregate type is not an accessible generic class: {nominal}")
            if len(parsed.args) != len(template.type_params):
                raise AOTError(f"generic aggregate {nominal} expects {len(template.type_params)} type arguments")
            type_args = tuple(self._pattern_kind(arg, {}) for arg in parsed.args)
            mapping = dict(zip(template.type_params, type_args))
            spec = self._specialize_class_mapping(template, mapping)
            return f"object[{spec.identity}]"
        return _abi_type(parsed, self.unit, self.units)

    def _generic_decl(self, callee: ast.Expr) -> tuple[ast.FunctionDecl, ModuleUnit] | None:
        if isinstance(callee, ast.Variable):
            decl = self.unit.functions.get(callee.name.lexeme)
            return (decl, self.unit) if decl is not None and decl.type_params else None
        if isinstance(callee, ast.Member) and isinstance(callee.target, ast.Variable):
            dep_path = self.unit.imports.get(callee.target.name.lexeme)
            if dep_path is not None:
                owner = self.units[dep_path]
                decl = owner.functions.get(callee.name.lexeme)
                if decl is not None and decl.type_params and decl.visibility == "public":
                    return decl, owner
        return None

    def _specialize_generic_function(self, decl: ast.FunctionDecl, rendered: list[tuple[str, str]], owner: ModuleUnit) -> FunctionABI:
        mapping: dict[str, str] = {}
        if len(rendered) != len(decl.parameters):
            raise AOTError(f"generic argument count mismatch for {decl.name.lexeme}")
        previous_unit = self.unit
        self.unit = owner
        try:
            for parameter, (_, actual) in zip(decl.parameters, rendered):
                pattern = parse_type(parameter.type_name, set(decl.type_params))
                if not self._unify_kind(pattern, actual, mapping):
                    raise AOTError(f"generic type inference failed for {decl.name.lexeme}.{parameter.name.lexeme}: {actual}")
            missing = [name for name in decl.type_params if name not in mapping]
            if missing:
                raise AOTError(f"cannot infer native generic type arguments for {decl.name.lexeme}: {', '.join(missing)}")
            type_args = tuple(mapping[name] for name in decl.type_params)
            key = (f"{owner.identity}.{decl.name.lexeme}", type_args)
            existing = self.generic_function_specs.get(key)
            if existing is not None:
                return existing[2]
            params = tuple(self._pattern_kind(parse_type(p.type_name, set(decl.type_params)), mapping) for p in decl.parameters)
            result = self._pattern_kind(parse_type(decl.return_type or "unit", set(decl.type_params)), mapping)
            # Specializations are emitted into the caller object so two callers
            # never create duplicate global definitions for the same template.
            symbol = native_function_symbol(self.output_unit.identity, f"{owner.identity}.{decl.name.lexeme}") + _specialization_suffix(type_args)
            abi = FunctionABI(decl.name.lexeme, decl.visibility, params, result, symbol, type_args=type_args)
            self.generic_function_specs[key] = (owner, decl, abi, dict(mapping))
            return abi
        finally:
            self.unit = previous_unit

    def _specialize_class_mapping(self, template: ClassABI, mapping: dict[str, str]) -> ClassABI:
        owner = next((u for u in self.units.values() if template.identity in {c.identity for c in u.classes.values()}), None)
        if owner is None:
            raise AOTError(f"generic aggregate owner module not found: {template.identity}")
        decl = template.declaration
        if decl.base_name or decl.interfaces:
            raise AOTError("generic aggregate monomorphization 0.35 does not combine with generic inheritance yet")
        missing = [name for name in decl.type_params if name not in mapping]
        if missing:
            raise AOTError(f"cannot materialize native generic aggregate {template.name}: {', '.join(missing)}")
        type_args = tuple(mapping[name] for name in decl.type_params)
        key = (template.identity, type_args)
        existing = self.generic_class_specs.get(key)
        if existing is not None:
            return existing[2]
        suffix = _specialization_suffix(type_args)
        name = template.name + suffix
        identity = template.identity + "[" + ",".join(type_args) + "]"
        previous_unit = self.unit
        self.unit = owner
        try:
            fields = tuple(
                FieldABI(field.name.lexeme, self._pattern_kind(parse_type(field.type_name, set(decl.type_params)), mapping), field.mutable, field.private, index)
                for index, field in enumerate(decl.fields)
            )
            methods: dict[str, FunctionABI] = {}
            for method in decl.methods:
                if method.type_params or method.abstract:
                    raise AOTError(f"generic aggregate method must be concrete/non-generic: {template.name}.{method.name.lexeme}")
                params = tuple(self._pattern_kind(parse_type(p.type_name, set(decl.type_params)), mapping) for p in method.parameters)
                result = self._pattern_kind(parse_type(method.return_type or "unit", set(decl.type_params)), mapping)
                methods[method.name.lexeme] = FunctionABI(
                    method.name.lexeme, method.visibility, params, result,
                    _method_symbol(self.output_unit.identity, name, method.name.lexeme), owner=name,
                    dispatch_slot=_dispatch_slot(method.name.lexeme, params, result), declaring_identity=identity,
                    type_args=type_args,
                )
        finally:
            self.unit = previous_unit
        spec = ClassABI(name, template.visibility, identity, _type_id(identity), decl, fields, methods,
                        type_params=(), template_identity=template.identity)
        self.generic_class_specs[key] = (owner, template, spec, dict(mapping))
        self.class_by_identity[identity] = spec
        return spec

    def _specialize_class(self, template: ClassABI, rendered: list[tuple[str, str]]) -> ClassABI:
        decl = template.declaration
        if len(rendered) != len(decl.fields):
            raise AOTError(f"generic constructor argument count mismatch for {template.name}")
        mapping: dict[str, str] = {}
        for field, (_, actual) in zip(decl.fields, rendered):
            pattern = parse_type(field.type_name, set(decl.type_params))
            if not self._unify_kind(pattern, actual, mapping):
                raise AOTError(f"generic aggregate inference failed for {template.name}.{field.name.lexeme}: {actual}")
        missing = [name for name in decl.type_params if name not in mapping]
        if missing:
            raise AOTError(f"cannot infer native generic aggregate arguments for {template.name}: {', '.join(missing)}")
        return self._specialize_class_mapping(template, mapping)

    def _emit_callable_prototype(self, abi: FunctionABI) -> None:
        params = ', '.join(f"{_ctype(t)} saga_gp_{i}" for i, t in enumerate(abi.params)) or 'void'
        self._line(f"{_ctype(abi.result)} {abi.symbol}({params});")

    def _emit_method_prototypes(self, cls: ClassABI) -> None:
        for method in cls.methods.values():
            args = ['SagaRef saga_self', *[f"{_ctype(t)} saga_mp_{i}" for i, t in enumerate(method.params)]]
            self._line(f"{_ctype(method.result)} {method.symbol}({', '.join(args)});")

    def _enum_for_type(self, type_name: str) -> EnumABI:
        if not type_name.startswith("enum[") or not type_name.endswith("]"):
            raise AOTError(f"expected Native Enum ABI type, got {type_name}")
        identity = type_name[5:-1]
        enum = self.enum_by_identity.get(identity)
        if enum is None:
            raise AOTError(f"Native Enum ABI 0.35 cannot resolve enum identity {identity}")
        return enum

    def _field(self, type_name: str, name: str) -> FieldABI:
        cls = self._class_for_type(type_name)
        for field in cls.fields:
            if field.name == name:
                return field
        raise AOTError(f"Native Object ABI 0.35 field not found: {cls.name}.{name}")

    def _method(self, type_name: str, name: str) -> FunctionABI:
        cls = self._class_for_type(type_name)
        method = cls.methods.get(name)
        if method is None:
            raise AOTError(f"Native Object ABI 0.35 method not found: {cls.name}.{name}")
        return method

    def _enum_variant(self, expr: ast.Expr) -> tuple[EnumABI, int] | None:
        candidate = expr.callee if isinstance(expr, ast.Call) else expr
        # Local: State.Ready / Result.Ok(...)
        if isinstance(candidate, ast.Member) and isinstance(candidate.target, ast.Variable):
            enum = self.unit.enums.get(candidate.target.name.lexeme)
            if enum:
                for index, variant in enumerate(enum.variants):
                    if variant.name == candidate.name.lexeme:
                        return enum, index
        # Imported: m.State.Ready
        if isinstance(candidate, ast.Member) and isinstance(candidate.target, ast.Member) and isinstance(candidate.target.target, ast.Variable):
            alias = candidate.target.target.name.lexeme
            dep_path = self.unit.imports.get(alias)
            if dep_path is not None:
                enum = self.units[dep_path].enums.get(candidate.target.name.lexeme)
                if enum and enum.visibility == "public":
                    for index, variant in enumerate(enum.variants):
                        if variant.name == candidate.name.lexeme:
                            return enum, index
        return None

    def _constructor(self, callee: ast.Expr) -> ClassABI | None:
        if isinstance(callee, ast.Variable):
            cls = self.unit.classes.get(callee.name.lexeme)
            if cls is not None and (cls.abstract or cls.interface):
                raise AOTError(f"Native constructor cannot instantiate abstract/interface type {cls.name}")
            return cls
        if isinstance(callee, ast.Member) and isinstance(callee.target, ast.Variable):
            dep_path = self.unit.imports.get(callee.target.name.lexeme)
            if dep_path is not None:
                cls = self.units[dep_path].classes.get(callee.name.lexeme)
                if cls and cls.visibility == "public":
                    if cls.abstract or cls.interface:
                        raise AOTError(f"Native constructor cannot instantiate abstract/interface type {cls.name}")
                    return cls
        return None

    def _call_abi(self, callee: ast.Expr) -> FunctionABI:
        if isinstance(callee, ast.Variable):
            name = callee.name.lexeme
            abi = self.unit.function_abis.get(name)
            if abi is None:
                raise AOTError(f"Native Codegen ABI 0.35 supports direct Saga functions only; unknown callable '{name}'")
            return abi
        if isinstance(callee, ast.Member) and isinstance(callee.target, ast.Variable):
            alias = callee.target.name.lexeme
            dep_path = self.unit.imports.get(alias)
            if dep_path is not None:
                dep = self.units[dep_path]
                abi = dep.function_abis.get(callee.name.lexeme)
                if abi is None or abi.visibility != "public":
                    raise AOTError(f"Native Codegen cannot call non-public or missing function '{alias}.{callee.name.lexeme}'")
                return abi
        raise AOTError("Native Codegen ABI 0.35 supports direct local/module function calls only")

    @staticmethod
    def _value_expr(value: str, kind: str) -> str:
        return _heap_value(kind, value)

    def _unpack_heap(self, heap_expr: str, kind: str, prefix: str) -> tuple[str, str]:
        hv = self._new_temp(prefix + "_hv", "int")
        # _new_temp cannot declare SagaHeapValue, so replace the declaration line.
        self.lines.pop()
        self._line(f"SagaHeapValue {hv} = {heap_expr};")
        out = self._new_temp(prefix, kind)
        self._line(f"{out} = {hv}.value.{_value_field(kind)};")
        return out, kind

    def _infer_list_elements(self, elements: list[ast.Expr], expected: str | None) -> tuple[list[tuple[str, str]], str]:
        expected_inner = _inner_types(expected)[0] if expected and expected.startswith("list[") else None
        rendered: list[tuple[str, str]] = []
        item_type = expected_inner
        for expr in elements:
            value, kind = self._expr(expr, expected_inner)
            if value is None or kind == "unit":
                raise AOTError("Native list elements cannot be unit")
            if item_type is None:
                item_type = kind
            if kind != item_type:
                raise AOTError(f"Native list element type mismatch: {item_type} vs {kind}")
            temp = self._new_temp("list_item", kind)
            self._line(f"{temp} = {value};")
            rendered.append((temp, kind))
        if item_type is None:
            if expected_inner is None:
                raise AOTError("empty native list needs a contextual list type")
            item_type = expected_inner
        return rendered, item_type

    def _expr(self, expr: ast.Expr, expected: str | None = None) -> tuple[str | None, str]:
        if isinstance(expr, ast.Literal):
            if isinstance(expr.value, bool):
                return ("1" if expr.value else "0"), "bool"
            if isinstance(expr.value, int) and not isinstance(expr.value, bool):
                if not -(2**63) <= expr.value <= 2**63 - 1:
                    raise AOTError("Native Codegen ABI 0.35 int literal exceeds checked int64 subset")
                return str(expr.value), "int"
            if isinstance(expr.value, str):
                return _text_literal(expr.value), "text"
            if expr.value is None:
                return None, "unit"
            raise AOTError("Native Codegen ABI 0.35 literal is not lowerable")

        if isinstance(expr, ast.Variable):
            return self._var(expr.name.lexeme), self._lookup_type(expr.name.lexeme)

        enum_variant = self._enum_variant(expr)
        if enum_variant is not None:
            enum, tag = enum_variant
            variant = enum.variants[tag]
            arguments = expr.arguments if isinstance(expr, ast.Call) else []
            if len(arguments) != len(variant.payload_types):
                raise AOTError(f"Native Tagged Union ABI 0.35 {enum.name}.{variant.name} expects {len(variant.payload_types)} payload values")
            out_kind = f"enum[{enum.identity}]"
            out = self._new_temp("tagged", out_kind)
            self._line(f"{out}.type_id = UINT64_C(0x{enum.type_id:016x});")
            self._line(f"{out}.tag = UINT32_C({tag});")
            self._line(f"{out}.arity = UINT8_C({len(variant.payload_types)});")
            for index, (argument, expected_type) in enumerate(zip(arguments, variant.payload_types)):
                value, actual_type = self._expr(argument, expected_type)
                if value is None or actual_type != expected_type:
                    raise AOTError(f"Native Tagged Union payload type mismatch for {enum.name}.{variant.name}")
                self._line(f"{out}.kinds[{index}] = {_heap_kind(expected_type)};")
                self._line(f"{out}.payload[{index}].{_value_field(expected_type)} = {value};")
            return out, out_kind

        if isinstance(expr, ast.ListLiteral):
            rendered, item_type = self._infer_list_elements(expr.elements, expected)
            list_type = f"list[{item_type}]"
            out = self._new_temp("list", list_type)
            self._line(f"{out} = saga_list_new({_heap_kind(item_type)}, UINT64_C({len(rendered)}));")
            for value, kind in rendered:
                self._line(f"saga_list_push({out}, {self._value_expr(value, kind)});")
            return out, list_type

        if isinstance(expr, ast.Unary):
            value, kind = self._expr(expr.right)
            if value is None:
                raise AOTError("unit cannot be used with unary operator")
            out_kind = "bool" if expr.operator.kind in {TokenKind.BANG, TokenKind.NOT} else "int"
            tmp = self._new_temp("unary", out_kind)
            if expr.operator.kind in {TokenKind.BANG, TokenKind.NOT}:
                if kind != "bool": raise AOTError("not requires bool")
                self._line(f"{tmp} = (uint8_t)(!({value}));")
            elif expr.operator.kind is TokenKind.MINUS:
                if kind != "int": raise AOTError("unary - requires int")
                self._line(f"{tmp} = saga_abi035_neg_i64({value});")
            else:
                raise AOTError("unsupported Native Codegen unary operator")
            return tmp, out_kind

        if isinstance(expr, ast.Binary):
            kind = expr.operator.kind
            left, left_type = self._expr(expr.left)
            if left is None:
                raise AOTError("unit cannot be used in a binary expression")
            if kind in {TokenKind.AND, TokenKind.OR}:
                if left_type != "bool": raise AOTError("logical operands must be bool")
                result = self._new_temp("logic", "bool")
                self._line(f"{result} = (uint8_t)({left} != 0);")
                condition = result if kind is TokenKind.AND else f"!{result}"
                self._line(f"if ({condition}) {{")
                self.indent += 1
                right, right_type = self._expr(expr.right)
                if right is None or right_type != "bool":
                    raise AOTError("logical operands must be bool in Native Codegen ABI")
                self._line(f"{result} = (uint8_t)({right} != 0);")
                self.indent -= 1
                self._line("}")
                return result, "bool"
            right, right_type = self._expr(expr.right)
            if right is None:
                raise AOTError("unit cannot be used in a binary expression")
            comparisons = {
                TokenKind.EQUAL_EQUAL: "==", TokenKind.BANG_EQUAL: "!=",
                TokenKind.LESS: "<", TokenKind.LESS_EQUAL: "<=",
                TokenKind.GREATER: ">", TokenKind.GREATER_EQUAL: ">=",
            }
            if kind in comparisons:
                tmp = self._new_temp("cmp", "bool")
                negate = kind is TokenKind.BANG_EQUAL
                if left_type == right_type == "text" and kind in {TokenKind.EQUAL_EQUAL, TokenKind.BANG_EQUAL}:
                    test = f"saga_abi035_text_equal({left}, {right})"
                    self._line(f"{tmp} = (uint8_t)({'!' if negate else ''}{test});")
                elif left_type == right_type and _is_enum_type(left_type) and kind in {TokenKind.EQUAL_EQUAL, TokenKind.BANG_EQUAL}:
                    test = f"saga_abi035_tagged_equal({left}, {right})"
                    self._line(f"{tmp} = (uint8_t)({'!' if negate else ''}{test});")
                elif left_type == right_type and _is_ref_type(left_type) and kind in {TokenKind.EQUAL_EQUAL, TokenKind.BANG_EQUAL}:
                    # Saga objects use identity. Collections are structural in the
                    # reference runtime; the support library implements deep
                    # collection equality through contains/get operations only in
                    # 0.35, so direct == on collections remains fail-closed.
                    if left_type.startswith("object["):
                        op = "!=" if negate else "=="
                        self._line(f"{tmp} = (uint8_t)(({left}) {op} ({right}));")
                    else:
                        raise AOTError("Native collection structural == is deferred beyond Aggregate ABI 0.35")
                elif left_type == right_type and left_type in {"int", "bool"}:
                    self._line(f"{tmp} = (uint8_t)(({left}) {comparisons[kind]} ({right}));")
                else:
                    raise AOTError("Native Codegen comparison types are not supported")
                return tmp, "bool"
            if kind is TokenKind.PLUS and left_type == right_type == "text":
                tmp = self._new_temp("text_concat", "text")
                self._line(f"{tmp} = saga_abi035_text_concat({left}, {right});")
                return tmp, "text"
            if left_type != "int" or right_type != "int":
                raise AOTError("Native Codegen arithmetic requires int operands (or text + text)")
            if kind is TokenKind.SLASH:
                raise AOTError("Native Codegen ABI 0.35 does not lower exact rational division")
            helpers = {
                TokenKind.PLUS: "saga_abi035_add_i64",
                TokenKind.MINUS: "saga_abi035_sub_i64",
                TokenKind.STAR: "saga_abi035_mul_i64",
                TokenKind.PERCENT: "saga_abi035_mod_i64",
            }
            helper = helpers.get(kind)
            if helper is None:
                raise AOTError(f"unsupported Native Codegen binary operator: {kind}")
            tmp = self._new_temp("arith", "int")
            self._line(f"{tmp} = {helper}({left}, {right});")
            return tmp, "int"

        if isinstance(expr, ast.Index):
            target, target_type = self._expr(expr.target)
            index, index_type = self._expr(expr.index)
            if target is None or not target_type.startswith("list[") or index is None or index_type != "int":
                raise AOTError("Native indexing currently supports list[int-index]")
            item = _inner_types(target_type)[0]
            return self._unpack_heap(f"saga_list_get({target}, {index})", item, "index")

        if isinstance(expr, ast.Call):
            if (
                isinstance(expr.callee, ast.Member)
                and isinstance(expr.callee.target, ast.Variable)
                and expr.callee.target.name.lexeme == "machine"
                and expr.callee.name.lexeme in {
                    "q31_from_ratio", "q31_add_sat", "q31_sub_sat",
                    "q31_mul_sat", "q31_mac_sat",
                }
            ):
                name = expr.callee.name.lexeme
                arity = 3 if name == "q31_mac_sat" else 2
                if len(expr.arguments) != arity:
                    raise AOTError(f"machine.{name} expects {arity} integer arguments")
                rendered: list[str] = []
                for argument in expr.arguments:
                    value, kind = self._expr(argument, "int")
                    if value is None or kind != "int":
                        raise AOTError(f"machine.{name} requires int arguments in Native Codegen ABI")
                    temp = self._new_temp("q31_arg", "int")
                    self._line(f"{temp} = {value};")
                    rendered.append(temp)
                out = self._new_temp("q31", "int")
                helper = f"saga_abi035_machine_{name}"
                self._line(f"{out} = {helper}({', '.join(rendered)});")
                return out, "int"

            # Tagged Option/Result constructors and observers.
            if isinstance(expr.callee, ast.Variable):
                name = expr.callee.name.lexeme
                if name in {"some", "none", "ok", "err"}:
                    if name in {"some", "none"}:
                        if expected is None or not expected.startswith("option["):
                            raise AOTError(f"{name} needs contextual option type in Native Codegen ABI 0.35")
                        inner = _inner_types(expected)[0]
                        if name == "none":
                            if expr.arguments: raise AOTError("none takes no arguments")
                            return "(SagaOption){0, {0}}", expected
                        if len(expr.arguments) != 1: raise AOTError("some takes one argument")
                        value, actual = self._expr(expr.arguments[0], inner)
                        if value is None or actual != inner: raise AOTError("some payload type mismatch")
                        return f"(SagaOption){{1, {{.{_value_field(inner)} = {value}}}}}", expected
                    if expected is None or not expected.startswith("result["):
                        raise AOTError(f"{name} needs contextual result type in Native Codegen ABI 0.35")
                    ok_t, err_t = _inner_types(expected); payload_t = ok_t if name == "ok" else err_t
                    if len(expr.arguments) != 1: raise AOTError(f"{name} takes one argument")
                    value, actual = self._expr(expr.arguments[0], payload_t)
                    if value is None or actual != payload_t: raise AOTError(f"{name} payload type mismatch")
                    return f"(SagaResult){{{1 if name == 'ok' else 0}, {{.{_value_field(payload_t)} = {value}}}}}", expected

                if name in {"is_some", "is_none", "unwrap", "unwrap_or", "is_ok", "is_err", "unwrap_ok", "unwrap_err", "unwrap_result_or"}:
                    if not expr.arguments: raise AOTError(f"{name} needs an argument")
                    wrapped, wkind = self._expr(expr.arguments[0])
                    if wrapped is None: raise AOTError(f"{name} cannot inspect unit")
                    wtmp = self._new_temp("wrapped", wkind); self._line(f"{wtmp} = {wrapped};")
                    if name in {"is_some", "is_none"}:
                        if not wkind.startswith("option["): raise AOTError(f"{name} expects option")
                        tmp=self._new_temp("pred","bool"); test=f"{wtmp}.present" if name=="is_some" else f"!{wtmp}.present"; self._line(f"{tmp} = (uint8_t)({test});"); return tmp,"bool"
                    if name in {"is_ok", "is_err"}:
                        if not wkind.startswith("result["): raise AOTError(f"{name} expects result")
                        tmp=self._new_temp("pred","bool"); test=f"{wtmp}.ok" if name=="is_ok" else f"!{wtmp}.ok"; self._line(f"{tmp} = (uint8_t)({test});"); return tmp,"bool"
                    if name in {"unwrap", "unwrap_or"}:
                        if not wkind.startswith("option["): raise AOTError(f"{name} expects option")
                        inner=_inner_types(wkind)[0]
                        if name=="unwrap":
                            self._line(f"if (!{wtmp}.present) {{ fputs(\"SAGA-R104: none unwrap\\n\", stderr); exit(72); }}")
                            tmp=self._new_temp("unwrapped",inner); self._line(f"{tmp} = {wtmp}.value.{_value_field(inner)};"); return tmp,inner
                        if len(expr.arguments)!=2: raise AOTError("unwrap_or expects two arguments")
                        fb,ft=self._expr(expr.arguments[1],inner)
                        if fb is None or ft!=inner: raise AOTError("unwrap_or fallback mismatch")
                        tmp=self._new_temp("unwrapped",inner); self._line(f"{tmp} = {wtmp}.present ? {wtmp}.value.{_value_field(inner)} : {fb};"); return tmp,inner
                    if not wkind.startswith("result["): raise AOTError(f"{name} expects result")
                    ok_t,err_t=_inner_types(wkind); want=ok_t if name in {"unwrap_ok","unwrap_result_or"} else err_t
                    if name=="unwrap_ok": self._line(f"if (!{wtmp}.ok) {{ fputs(\"SAGA-R141: err unwrap_ok\\n\", stderr); exit(73); }}")
                    if name=="unwrap_err": self._line(f"if ({wtmp}.ok) {{ fputs(\"SAGA-R142: ok unwrap_err\\n\", stderr); exit(74); }}")
                    if name=="unwrap_result_or":
                        if len(expr.arguments)!=2: raise AOTError("unwrap_result_or expects two arguments")
                        fb,ft=self._expr(expr.arguments[1],ok_t)
                        if fb is None or ft!=ok_t: raise AOTError("unwrap_result_or fallback mismatch")
                        tmp=self._new_temp("unwrapped",ok_t); self._line(f"{tmp} = {wtmp}.ok ? {wtmp}.value.{_value_field(ok_t)} : {fb};"); return tmp,ok_t
                    tmp=self._new_temp("unwrapped",want); self._line(f"{tmp} = {wtmp}.value.{_value_field(want)};"); return tmp,want

                if name == "abs":
                    if len(expr.arguments) != 1: raise AOTError("abs expects one argument")
                    value, kind = self._expr(expr.arguments[0])
                    if value is None or kind != "int": raise AOTError("abs expects int in Native Codegen ABI")
                    tmp = self._new_temp("abs", "int"); self._line(f"{tmp} = saga_abi035_abs_i64({value});"); return tmp, "int"

                if name == "len":
                    if len(expr.arguments) != 1: raise AOTError("len expects one argument")
                    value, kind = self._expr(expr.arguments[0])
                    if value is None: raise AOTError("len cannot inspect unit")
                    tmp=self._new_temp("len","int")
                    if kind == "text": self._line(f"{tmp} = (int64_t){value}.len;")
                    elif kind.startswith(("list[","map[","set[")): self._line(f"{tmp} = (int64_t)saga_ref_len({value});")
                    else: raise AOTError("Native len supports text/list/map/set")
                    return tmp,"int"

                if name in {"append","prepend","set_at","get","contains"}:
                    if not expr.arguments: raise AOTError(f"{name} needs a list")
                    seq, st = self._expr(expr.arguments[0])
                    if seq is None or not st.startswith("list["): raise AOTError(f"{name} expects list")
                    inner=_inner_types(st)[0]
                    if name in {"append","prepend"}:
                        if len(expr.arguments)!=2: raise AOTError(f"{name} expects two arguments")
                        val, vt=self._expr(expr.arguments[1],inner)
                        if val is None or vt!=inner: raise AOTError(f"{name} element mismatch")
                        out=self._new_temp(name,st); self._line(f"{out} = saga_list_{name}({seq}, {_heap_value(inner,val)});"); return out,st
                    if name=="set_at":
                        if len(expr.arguments)!=3: raise AOTError("set_at expects list,index,value")
                        idx,it=self._expr(expr.arguments[1]); val,vt=self._expr(expr.arguments[2],inner)
                        if idx is None or it!="int" or val is None or vt!=inner: raise AOTError("set_at type mismatch")
                        out=self._new_temp("set_at",st); self._line(f"{out} = saga_list_set_at({seq},{idx},{_heap_value(inner,val)});"); return out,st
                    if name=="get":
                        if len(expr.arguments)!=3: raise AOTError("get expects list,index,fallback")
                        idx,it=self._expr(expr.arguments[1]); fb,ft=self._expr(expr.arguments[2],inner)
                        if idx is None or it!="int" or fb is None or ft!=inner: raise AOTError("get type mismatch")
                        return self._unpack_heap(f"saga_list_get_or({seq},{idx},{_heap_value(inner,fb)})",inner,"get")
                    if len(expr.arguments)!=2: raise AOTError("contains expects two arguments")
                    val,vt=self._expr(expr.arguments[1],inner)
                    if val is None or vt!=inner: raise AOTError("contains element mismatch")
                    out=self._new_temp("contains","bool"); self._line(f"{out} = saga_list_contains({seq},{_heap_value(inner,val)});"); return out,"bool"

                if name == "map_of":
                    if len(expr.arguments)%2: raise AOTError("map_of requires key,value pairs")
                    exp = _inner_types(expected) if expected and expected.startswith("map[") else ()
                    key_t = exp[0] if exp else None; val_t = exp[1] if exp else None
                    pairs=[]
                    for i in range(0,len(expr.arguments),2):
                        k,kt=self._expr(expr.arguments[i],key_t); v,vt=self._expr(expr.arguments[i+1],val_t)
                        if k is None or v is None: raise AOTError("map_of cannot store unit")
                        key_t=key_t or kt; val_t=val_t or vt
                        if kt!=key_t or vt!=val_t: raise AOTError("map_of types must be uniform")
                        ktm=self._new_temp("map_key",kt); self._line(f"{ktm} = {k};")
                        vtm=self._new_temp("map_val",vt); self._line(f"{vtm} = {v};")
                        pairs.append((ktm,vtm))
                    if key_t is None or val_t is None: raise AOTError("empty map_of needs contextual map type")
                    mt=f"map[{key_t},{val_t}]"; out=self._new_temp("map",mt); self._line(f"{out}=saga_map_new({_heap_kind(key_t)},{_heap_kind(val_t)},UINT64_C({len(pairs)}));")
                    for k,v in pairs:
                        next_out=self._new_temp("map_put",mt); self._line(f"{next_out}=saga_map_put({out},{_heap_value(key_t,k)},{_heap_value(val_t,v)});"); out=next_out
                    return out,mt

                if name in {"map_get","map_put","map_remove","map_contains"}:
                    if not expr.arguments: raise AOTError(f"{name} needs map")
                    mp,mt=self._expr(expr.arguments[0])
                    if mp is None or not mt.startswith("map["): raise AOTError(f"{name} expects map")
                    kt,vt=_inner_types(mt); key,keyt=self._expr(expr.arguments[1],kt)
                    if key is None or keyt!=kt: raise AOTError("map key mismatch")
                    if name=="map_get":
                        fb,fbt=self._expr(expr.arguments[2],vt)
                        if fb is None or fbt!=vt: raise AOTError("map_get fallback mismatch")
                        return self._unpack_heap(f"saga_map_get_or({mp},{_heap_value(kt,key)},{_heap_value(vt,fb)})",vt,"map_get")
                    if name=="map_put":
                        val,valt=self._expr(expr.arguments[2],vt)
                        if val is None or valt!=vt: raise AOTError("map_put value mismatch")
                        out=self._new_temp("map_put",mt); self._line(f"{out}=saga_map_put({mp},{_heap_value(kt,key)},{_heap_value(vt,val)});"); return out,mt
                    if name=="map_remove":
                        out=self._new_temp("map_remove",mt); self._line(f"{out}=saga_map_remove({mp},{_heap_value(kt,key)});"); return out,mt
                    out=self._new_temp("map_contains","bool"); self._line(f"{out}=saga_map_contains({mp},{_heap_value(kt,key)});"); return out,"bool"

                if name == "set_of":
                    exp=_inner_types(expected)[0] if expected and expected.startswith("set[") else None
                    item_t=exp; vals=[]
                    for arg in expr.arguments:
                        value,kind=self._expr(arg,item_t)
                        if value is None: raise AOTError("set_of cannot store unit")
                        item_t=item_t or kind
                        if kind!=item_t: raise AOTError("set_of types must be uniform")
                        temp=self._new_temp("set_item",kind); self._line(f"{temp}={value};"); vals.append(temp)
                    if item_t is None: raise AOTError("empty set_of needs contextual set type")
                    st=f"set[{item_t}]"; out=self._new_temp("set",st); self._line(f"{out}=saga_set_new({_heap_kind(item_t)},UINT64_C({len(vals)}));")
                    for value in vals:
                        nxt=self._new_temp("set_add",st); self._line(f"{nxt}=saga_set_add({out},{_heap_value(item_t,value)});"); out=nxt
                    return out,st

                if name in {"set_add","set_remove","set_contains","set_union","set_intersection"}:
                    left,st=self._expr(expr.arguments[0])
                    if left is None or not st.startswith("set["): raise AOTError(f"{name} expects set")
                    inner=_inner_types(st)[0]
                    if name in {"set_union","set_intersection"}:
                        right,rt=self._expr(expr.arguments[1],st)
                        if right is None or rt!=st: raise AOTError(f"{name} set mismatch")
                        out=self._new_temp(name,st); self._line(f"{out}=saga_{name}({left},{right});"); return out,st
                    val,vt=self._expr(expr.arguments[1],inner)
                    if val is None or vt!=inner: raise AOTError(f"{name} value mismatch")
                    if name=="set_contains":
                        out=self._new_temp(name,"bool"); self._line(f"{out}=saga_set_contains({left},{_heap_value(inner,val)});"); return out,"bool"
                    out=self._new_temp(name,st); self._line(f"{out}=saga_{name}({left},{_heap_value(inner,val)});"); return out,st

            # Generic functions are monomorphized from the concrete native
            # argument kinds at each calling module.
            generic_info = self._generic_decl(expr.callee)
            if generic_info is not None:
                generic_decl, generic_owner = generic_info
                raw_args: list[tuple[str, str]] = []
                for arg in expr.arguments:
                    value, kind = self._expr(arg)
                    if value is None or kind == "unit":
                        raise AOTError(f"generic function argument cannot be unit: {generic_decl.name.lexeme}")
                    raw_args.append((value, kind))
                abi = self._specialize_generic_function(generic_decl, raw_args, generic_owner)
                rendered: list[str] = []
                for (value, actual), expected_type in zip(raw_args, abi.params):
                    if not self._assignable(expected_type, actual):
                        raise AOTError(f"generic specialization argument mismatch for {abi.name}: {actual} -> {expected_type}")
                    temp=self._new_temp("generic_arg",actual); self._line(f"{temp}={value};"); rendered.append(temp)
                self._emit_callable_prototype(abi)
                call=f"{abi.symbol}({', '.join(rendered)})"
                if abi.result == "unit":
                    self._line(call+";"); return None,"unit"
                out=self._new_temp("generic_call",abi.result); self._line(f"{out}={call};"); return out,abi.result

            # Class constructor. Generic classes are concretized from their
            # constructor field types and receive a unique nominal type id.
            cls = self._constructor(expr.callee)
            if cls is not None:
                if cls.type_params:
                    raw_args: list[tuple[str, str]] = []
                    for arg in expr.arguments:
                        value, kind = self._expr(arg)
                        if value is None or kind == "unit":
                            raise AOTError(f"generic constructor argument cannot be unit: {cls.name}")
                        raw_args.append((value, kind))
                    cls = self._specialize_class(cls, raw_args)
                    rendered=[]
                    for (value,kind),field in zip(raw_args,cls.fields):
                        if not self._assignable(field.type_name, kind):
                            raise AOTError(f"generic constructor field type mismatch: {cls.name}.{field.name}")
                        temp=self._new_temp("ctor_arg",kind); self._line(f"{temp}={value};"); rendered.append(temp)
                    params=', '.join(f"{_ctype(f.type_name)} saga_cp_{i}" for i,f in enumerate(cls.fields)) or 'void'
                    self._line(f"SagaRef {_constructor_symbol(self.unit.identity,cls.name)}({params});")
                    self._emit_method_prototypes(cls)
                else:
                    if len(expr.arguments) != len(cls.fields):
                        raise AOTError(f"Native Object constructor argument count mismatch for {cls.name}")
                    rendered=[]
                    for arg,field in zip(expr.arguments,cls.fields):
                        value,kind=self._expr(arg,field.type_name)
                        if value is None or not self._assignable(field.type_name, kind): raise AOTError(f"constructor field type mismatch: {cls.name}.{field.name}")
                        temp=self._new_temp("ctor_arg",kind); self._line(f"{temp}={value};"); rendered.append(temp)
                owner = self.unit.identity if cls.template_identity else cls.identity.rsplit('.',1)[0]
                call=f"{_constructor_symbol(owner,cls.name)}({', '.join(rendered)})"
                out=self._new_temp("object",f"object[{cls.identity}]"); self._line(f"{out}={call};"); return out,f"object[{cls.identity}]"

            # A qualified module function call (`m.fn(...)`) must be recognized
            # before object-method lowering. The namespace alias is not a runtime
            # value and therefore must never be evaluated as a local variable.
            module_call = (
                isinstance(expr.callee, ast.Member)
                and isinstance(expr.callee.target, ast.Variable)
                and expr.callee.target.name.lexeme in self.unit.imports
            )

            # Direct object method call.
            if isinstance(expr.callee, ast.Member) and not module_call:
                receiver, receiver_type = self._expr(expr.callee.target)
                if receiver is not None and receiver_type.startswith("object["):
                    method=self._method(receiver_type,expr.callee.name.lexeme)
                    if len(expr.arguments)!=len(method.params): raise AOTError(f"method argument count mismatch for {method.name}")
                    args=[]
                    for arg,expected_type in zip(expr.arguments,method.params):
                        value,kind=self._expr(arg,expected_type)
                        if value is None or not self._assignable(expected_type, kind): raise AOTError(f"method argument type mismatch for {method.name}")
                        temp=self._new_temp("method_arg",kind); self._line(f"{temp}={value};"); args.append(temp)
                    static_cls = self._class_for_type(receiver_type)
                    dispatch_symbol = _virtual_symbol(static_cls.identity, method.name)
                    proto_args=['SagaRef saga_self',*[f"{_ctype(t)} saga_dp_{i}" for i,t in enumerate(method.params)]]
                    self._line(f"{_ctype(method.result)} {dispatch_symbol}({', '.join(proto_args)});")
                    call=f"{dispatch_symbol}({receiver}{', ' if args else ''}{', '.join(args)})"
                    if method.result=="unit":
                        self._line(call+";"); return None,"unit"
                    out=self._new_temp("method",method.result); self._line(f"{out}={call};"); return out,method.result

            # Ordinary top-level Saga function call, including qualified module calls.
            abi = self._call_abi(expr.callee)
            if len(expr.arguments) != len(abi.params):
                raise AOTError(f"Native Codegen ABI argument count mismatch for {abi.name}")
            rendered=[]
            for arg, expected_type in zip(expr.arguments, abi.params):
                value, actual = self._expr(arg, expected_type)
                if value is None or not self._assignable(expected_type, actual):
                    raise AOTError(f"Native Codegen ABI argument type mismatch for {abi.name}: expected {expected_type}, got {actual}")
                temp=self._new_temp("arg",expected_type); self._line(f"{temp}={value};"); rendered.append(temp)
            call=f"{abi.symbol}({', '.join(rendered)})"
            if abi.result=="unit": self._line(call+";"); return None,"unit"
            out=self._new_temp("call",abi.result); self._line(f"{out}={call};"); return out,abi.result

        if isinstance(expr, ast.PropagateExpr):
            wrapped, kind = self._expr(expr.value)
            if wrapped is None: raise AOTError("? cannot propagate unit")
            tmp=self._new_temp("propagate",kind); self._line(f"{tmp}={wrapped};")
            if kind.startswith("option["):
                inner=_inner_types(kind)[0]
                if not self.current_result.startswith("option["): raise AOTError("option ? requires option-returning native function")
                self._line(f"if (!{tmp}.present) {{ saga_gc_unwind_roots({self.root_mark}); return (SagaOption){{0, {{0}}}}; }}")
                out=self._new_temp("propagated",inner); self._line(f"{out}={tmp}.value.{_value_field(inner)};"); return out,inner
            if kind.startswith("result["):
                inner,err_t=_inner_types(kind)
                if not self.current_result.startswith("result[") or _inner_types(self.current_result)[1]!=err_t: raise AOTError("result ? requires compatible result error type")
                self._line(f"if (!{tmp}.ok) {{ SagaResult __saga_early={tmp}; saga_gc_unwind_roots({self.root_mark}); return __saga_early; }}")
                out=self._new_temp("propagated",inner); self._line(f"{out}={tmp}.value.{_value_field(inner)};"); return out,inner
            raise AOTError("? requires option or result in Native Codegen ABI 0.35")

        if isinstance(expr, ast.Member):
            receiver, receiver_type = self._expr(expr.target)
            if receiver is not None and receiver_type == "error":
                if expr.name.lexeme == "kind":
                    return f"{receiver}.kind", "text"
                if expr.name.lexeme == "message":
                    return f"{receiver}.message", "text"
                raise AOTError(f"Native error has no member {expr.name.lexeme}")
            if receiver is not None and receiver_type.startswith("object["):
                field=self._field(receiver_type,expr.name.lexeme)
                return self._unpack_heap(f"saga_object_get({receiver},UINT64_C({field.index}))",field.type_name,"field")
            raise AOTError("Native member value is not a supported enum variant/object/error field")

        if isinstance(expr, (ast.ClosureExpr, ast.RangeExpr)):
            raise AOTError(f"Native Codegen ABI 0.35 expression is not yet lowerable: {type(expr).__name__}")
        raise AOTError(f"Native Codegen ABI 0.35 unsupported expression: {type(expr).__name__}")

    def _block(self, block: ast.Block) -> None:
        self.scopes.append(dict(self.scopes[-1]))
        self.temp += 1
        gc_mark = f"__saga_block_root_mark_{self.temp}"
        self._line(f"uint64_t {gc_mark}=saga_gc_root_mark();")
        self.gc_scope_marks.append(gc_mark)
        try:
            for st in block.statements:
                self._stmt(st)
            self._line(f"saga_gc_unwind_roots({gc_mark});")
        finally:
            self.gc_scope_marks.pop()
            self.scopes.pop()

    def _emit_pending_finally(self) -> None:
        if not self.finally_stack:
            return
        saved = list(self.finally_stack)
        try:
            for index in range(len(saved) - 1, -1, -1):
                block, outer_exception_depth = saved[index]
                # A finally executes outside the try/catch frame that protects
                # the corresponding body. An exception raised by cleanup must
                # propagate to an outer handler, not back into the same try.
                for frame in reversed(self.exception_frames[outer_exception_depth:]):
                    self._line(f"saga_exception_leave(&{frame});")
                self.finally_stack = saved[:index]
                self._block(block)
        finally:
            self.finally_stack = saved

    def _unwind_for_loop_exit(self) -> None:
        if not self.loop_stack:
            return
        depth = self.loop_stack[-1][2]
        if len(self.gc_scope_marks) > depth:
            self._line(f"saga_gc_unwind_roots({self.gc_scope_marks[depth]});")
        exc_depth = self.loop_stack[-1][3]
        for frame in reversed(self.exception_frames[exc_depth:]):
            self._line(f"saga_exception_leave(&{frame});")

    def _stmt(self, st: ast.Stmt) -> None:
        if isinstance(st, ast.VarDecl):
            declared = self._decl_type(st.type_name) if st.type_name is not None else None
            if declared == "unit":
                value, actual = self._expr(st.initializer, "unit")
                if value is not None or actual != "unit": raise AOTError("unit binding requires unit expression")
                self.scopes[-1][st.name.lexeme]="unit"; return
            value,inferred=self._expr(st.initializer,declared)
            kind=declared or inferred
            if value is None or kind=="unit" or not self._assignable(kind, inferred): raise AOTError(f"Native local type mismatch for {st.name.lexeme}: expected {kind}, got {inferred}")
            self._declare_value(st.name.lexeme,kind,value); return

        if isinstance(st, ast.Assign):
            # Object receiver is evaluated before RHS, preserving Saga's target-first assignment semantics.
            if isinstance(st.target, ast.Member):
                receiver,receiver_type=self._expr(st.target.target)
                if receiver is None or not receiver_type.startswith("object["): raise AOTError("Native field assignment requires object receiver")
                field=self._field(receiver_type,st.target.name.lexeme)
                if not field.mutable: raise AOTError(f"Native field '{field.name}' is immutable")
                value,kind=self._expr(st.value,field.type_name)
                if value is None or not self._assignable(field.type_name, kind): raise AOTError("Native field assignment type mismatch")
                self._line(f"saga_object_set({receiver},UINT64_C({field.index}),{_heap_value(kind,value)});"); return
            if not isinstance(st.target, ast.Variable): raise AOTError("Native assignment supports local or object field")
            name=st.target.name.lexeme; expected=self._find_type(name); value,actual=self._expr(st.value,expected)
            if value is None or actual=="unit": raise AOTError(f"cannot materialize unit binding '{name}'")
            if expected is None: self._declare_value(name,actual,value); return
            if not self._assignable(expected, actual): raise AOTError(f"assignment type mismatch for {name}")
            self._line(f"{self._var(name)}={value};"); return

        if isinstance(st, ast.ExpressionStmt):
            expr=st.expression
            if isinstance(expr, ast.Call) and isinstance(expr.callee, ast.Variable) and expr.callee.name.lexeme=="print":
                if len(expr.arguments)!=1: raise AOTError("Native Codegen ABI 0.35 currently lowers print with exactly one argument")
                value,kind=self._expr(expr.arguments[0])
                if kind=="unit": self._line("saga_abi035_print_unit();")
                elif kind=="bool": self._line(f"saga_abi035_print_bool({value});")
                elif kind=="int": self._line(f"saga_abi035_print_i64({value});")
                elif kind=="text": self._line(f"saga_abi035_print_text({value});")
                elif _is_enum_type(kind):
                    enum = self._enum_for_type(kind)
                    display = enum.name
                    if enum.identity != f"{self.unit.identity}.{enum.name}":
                        for alias, dep_path in self.unit.imports.items():
                            dep = self.units[dep_path]
                            if enum.identity == f"{dep.identity}.{enum.name}":
                                display = f"{alias}.{enum.name}"
                                break
                    tagged = self._new_temp("print_enum", kind)
                    self._line(f"{tagged}={value};")
                    self._line(f"if ({tagged}.type_id != UINT64_C(0x{enum.type_id:016x})) {{ fputs(\"SAGA-R191: native enum type mismatch\\n\", stderr); exit(91); }}")
                    self._line(f"switch ({tagged}.tag) {{")
                    self.indent += 1
                    for tag, variant in enumerate(enum.variants):
                        literal = (display + "." + variant.name).replace('\\', '\\\\').replace('"', '\\"')
                        self._line(f"case UINT32_C({tag}): fputs(\"{literal}\\n\", stdout); break;")
                    self._line('default: fputs("SAGA-R192: invalid native enum tag\\n", stderr); exit(92);')
                    self.indent -= 1
                    self._line("}")
                elif _is_ref_type(kind):
                    safe_print = False
                    if kind.startswith("list["):
                        safe_print = _inner_types(kind)[0] in {"int", "bool", "text"}
                    elif kind.startswith("map["):
                        key_t, value_t = _inner_types(kind)
                        safe_print = key_t in {"int", "bool", "text"} and value_t in {"int", "bool", "text"}
                    # Set display has a stable sorted representation in the
                    # reference runtime; object display exposes public field
                    # names. The generic heap printer cannot preserve either
                    # contract yet, so those forms fail closed instead of
                    # silently producing a different observable string.
                    if not safe_print:
                        raise AOTError(f"Native Aggregate ABI 0.35 has not stabilized display semantics for {kind}")
                    self._line(f"saga_abi035_print_ref({value});")
                else: raise AOTError("Native print type unsupported")
                return
            self._expr(expr); return

        if isinstance(st, ast.ThrowStmt):
            value, kind = self._expr(st.value)
            if value is None:
                raise AOTError("cannot throw unit in Native Exception ABI 0.35")
            tmp = self._new_temp("throw", kind)
            self._line(f"{tmp}={value};")
            if kind == "text":
                self._line(f"saga_throw_text({tmp});")
            elif kind == "int":
                self._line(f"saga_throw_i64({tmp});")
            elif kind == "bool":
                self._line(f"saga_throw_bool({tmp});")
            else:
                raise AOTError(f"Native Exception ABI 0.35 cannot throw {kind}; throw text/int/bool")
            return

        if isinstance(st, ast.TryStmt):
            self.temp += 1
            serial = self.temp
            frame = f"__saga_exc_frame_{serial}"
            mark = f"__saga_exc_mark_{serial}"
            raised = f"__saga_exc_raised_{serial}"
            self._line(f"uint64_t {mark}=saga_gc_root_mark();")
            self._line(f"SagaExceptionFrame {frame};")
            self._line(f"int {raised}=saga_exception_enter({frame},{mark});")
            self._line(f"if (!{raised}) {{")
            self.indent += 1
            outer_exception_depth = len(self.exception_frames)
            self.exception_frames.append(frame)
            if st.finally_block is not None:
                self.finally_stack.append((st.finally_block, outer_exception_depth))
            try:
                self._block(st.try_block)
            finally:
                if st.finally_block is not None:
                    self.finally_stack.pop()
                self.exception_frames.pop()
            self._line(f"saga_exception_leave(&{frame});")
            self.indent -= 1
            self._line("}")

            if st.catch_block is not None and st.catch_name is not None:
                self._line(f"if ({raised}) {{")
                self.indent += 1
                self.temp += 1
                catch_mark=f"__saga_catch_mark_{self.temp}"
                self._line(f"uint64_t {catch_mark}=saga_gc_root_mark();")
                self.scopes.append(dict(self.scopes[-1]))
                try:
                    cname=self._declare(st.catch_name.lexeme,"error")
                    self._line(f"SagaException {cname}=saga_exception_current();")
                    self._root_if_ref(cname,"error")
                    self._line("saga_exception_clear();")
                    if st.finally_block is not None:
                        self.temp += 1
                        cleanup=f"__saga_cleanup_frame_{self.temp}"
                        cleanup_mark=f"__saga_cleanup_mark_{self.temp}"
                        cleanup_raised=f"__saga_cleanup_raised_{self.temp}"
                        self._line(f"uint64_t {cleanup_mark}=saga_gc_root_mark();")
                        self._line(f"SagaExceptionFrame {cleanup};")
                        self._line(f"int {cleanup_raised}=saga_exception_enter({cleanup},{cleanup_mark});")
                        self._line(f"if (!{cleanup_raised}) {{")
                        self.indent += 1
                        cleanup_outer_depth = len(self.exception_frames)
                        self.exception_frames.append(cleanup)
                        self.finally_stack.append((st.finally_block, cleanup_outer_depth))
                        try:
                            self._block(st.catch_block)
                        finally:
                            self.finally_stack.pop()
                            self.exception_frames.pop()
                        self._line(f"saga_exception_leave(&{cleanup});")
                        self._block(st.finally_block)
                        self.indent -= 1
                        self._line("} else {")
                        self.indent += 1
                        self._block(st.finally_block)
                        self._line("saga_exception_rethrow();")
                        self.indent -= 1
                        self._line("}")
                    else:
                        self._block(st.catch_block)
                    self._line(f"saga_gc_unwind_roots({catch_mark});")
                finally:
                    self.scopes.pop()
                self.indent -= 1
                self._line("}")
                if st.finally_block is not None:
                    self._line(f"else {{")
                    self.indent += 1
                    self._block(st.finally_block)
                    self.indent -= 1
                    self._line("}")
            else:
                if st.finally_block is not None:
                    self._line(f"if ({raised}) {{")
                    self.indent += 1
                    self._block(st.finally_block)
                    self._line("saga_exception_rethrow();")
                    self.indent -= 1
                    self._line("} else {")
                    self.indent += 1
                    self._block(st.finally_block)
                    self.indent -= 1
                    self._line("}")
                else:
                    self._line(f"if ({raised}) saga_exception_rethrow();")
            return

        if isinstance(st, ast.ReturnStmt):
            if self.current_result=="unit":
                if st.value is not None:
                    value,kind=self._expr(st.value)
                    if value is not None or kind!="unit": raise AOTError("unit function cannot return value")
                self._emit_pending_finally(); self._return(); return
            if st.value is None: raise AOTError("non-unit native function must return value")
            value,kind=self._expr(st.value,self.current_result)
            if value is None or not self._assignable(self.current_result, kind): raise AOTError("Native return type mismatch")
            # Materialize before cleanup. A pending finally may allocate or
            # throw, so the return value is rooted before cleanup runs.
            tmp=self._new_temp("return",kind); self._line(f"{tmp}={value};"); self._emit_pending_finally(); self._return(tmp); return

        if isinstance(st, ast.IfStmt):
            cond,kind=self._expr(st.condition)
            if cond is None or kind!="bool": raise AOTError("if condition must be bool")
            self._line(f"if ({cond}) {{"); self.indent+=1; self._block(st.then_branch); self.indent-=1
            if st.else_branch is None: self._line("}")
            else: self._line("} else {"); self.indent+=1; self._block(st.else_branch); self.indent-=1; self._line("}")
            return

        if isinstance(st, ast.MatchStmt):
            value,kind=self._expr(st.value)
            if value is None or not _is_enum_type(kind): raise AOTError("Native match 0.35 currently requires enum value")
            enum=self._enum_for_type(kind)
            temp=self._new_temp("match",kind); self._line(f"{temp}={value};")
            for index,case in enumerate(st.cases):
                variant_info=self._enum_variant(case.pattern)
                if variant_info is None or variant_info[0].identity!=enum.identity: raise AOTError("Native match pattern enum mismatch")
                tag=variant_info[1]; variant=enum.variants[tag]; prefix="if" if index==0 else "else if"
                pattern_args = case.pattern.arguments if isinstance(case.pattern, ast.Call) else []
                if len(pattern_args) != len(variant.payload_types):
                    raise AOTError(f"Native match payload arity mismatch for {enum.name}.{variant.name}")
                self._line(f"{prefix} ({temp}.type_id==UINT64_C(0x{enum.type_id:016x}) && {temp}.tag==UINT32_C({tag})) {{")
                self.indent += 1
                self.temp += 1
                case_mark=f"__saga_match_root_mark_{self.temp}"
                self._line(f"uint64_t {case_mark}=saga_gc_root_mark();")
                self.gc_scope_marks.append(case_mark)
                self.scopes.append(dict(self.scopes[-1]))
                try:
                    for payload_index, (arg, payload_type) in enumerate(zip(pattern_args, variant.payload_types)):
                        if not isinstance(arg, ast.Variable):
                            raise AOTError("Native match payload pattern must be a variable or _")
                        if arg.name.lexeme == "_":
                            continue
                        c_name=self._declare(arg.name.lexeme,payload_type)
                        self._line(f"{_ctype(payload_type)} {c_name} = {temp}.payload[{payload_index}].{_value_field(payload_type)};")
                        self._root_if_ref(c_name,payload_type)
                    self._block(case.body)
                    self._line(f"saga_gc_unwind_roots({case_mark});")
                finally:
                    self.scopes.pop(); self.gc_scope_marks.pop()
                self.indent -= 1; self._line("}")
            if st.default is not None:
                self._line("else {"); self.indent+=1; self._block(st.default); self.indent-=1; self._line("}")
            return

        if isinstance(st, ast.WhileStmt):
            self._line("while (1) {"); self.indent+=1
            cond,kind=self._expr(st.condition)
            if cond is None or kind!="bool": raise AOTError("while condition must be bool")
            self._line(f"if (!({cond})) break;"); self.loop_stack.append(("break;","continue;",len(self.gc_scope_marks),len(self.exception_frames))); self._block(st.body); self.loop_stack.pop(); self.indent-=1; self._line("}"); return

        if isinstance(st, ast.ForStmt):
            if not isinstance(st.iterable, ast.RangeExpr): raise AOTError("Native for-loop currently requires inclusive int range")
            start,stype=self._expr(st.iterable.start); end,etype=self._expr(st.iterable.end)
            if start is None or end is None or stype!="int" or etype!="int": raise AOTError("range endpoints must be int")
            start_tmp=self._new_temp("range_start","int"); end_tmp=self._new_temp("range_end","int"); self._line(f"{start_tmp}={start};"); self._line(f"{end_tmp}={end};")
            cvar=self._var(st.name.lexeme); self.scopes.append(dict(self.scopes[-1])); self.scopes[-1][st.name.lexeme]="int"
            continue_label=f"__saga_for_continue_{self.temp+1}"; self.temp+=1
            self._line(f"for (int64_t {cvar}={start_tmp};;) {{"); self.indent+=1
            self._line(f"if (({start_tmp}<={end_tmp} && {cvar}>{end_tmp}) || ({start_tmp}>{end_tmp} && {cvar}<{end_tmp})) break;")
            self.loop_stack.append(("break;",f"goto {continue_label};",len(self.gc_scope_marks),len(self.exception_frames))); self._block(st.body); self.loop_stack.pop(); self._line(f"{continue_label}: ;"); self._line(f"if ({cvar}=={end_tmp}) break;"); self._line(f"{cvar}=({cvar}<{end_tmp}?saga_abi035_add_i64({cvar},1):saga_abi035_sub_i64({cvar},1));"); self.indent-=1; self._line("}"); self.scopes.pop(); return

        if isinstance(st, ast.BreakStmt):
            if not self.loop_stack: raise AOTError("break outside loop")
            self._emit_pending_finally()
            self._unwind_for_loop_exit()
            self._line(self.loop_stack[-1][0]); return
        if isinstance(st, ast.ContinueStmt):
            if not self.loop_stack: raise AOTError("continue outside loop")
            self._emit_pending_finally()
            self._unwind_for_loop_exit()
            self._line(self.loop_stack[-1][1]); return
        if isinstance(st, ast.Block):
            self._line("{"); self.indent+=1; self._block(st); self.indent-=1; self._line("}"); return
        if isinstance(st,(ast.FunctionDecl,ast.ModuleDecl,ast.UseStmt,ast.EnumDecl,ast.ClassDecl)): return
        raise AOTError(f"Native Codegen ABI 0.35 statement not yet lowerable: {type(st).__name__}")

    def _emit_function_body(self, label: str, params: list[tuple[str,str]], result_type: str, body: ast.Block | None, expression_body: ast.Expr | None, *, class_abi: ClassABI | None = None) -> None:
        self.indent += 1
        self.current_result=result_type; self.current_class=class_abi; self.scopes=[{}]; self.gc_scope_marks=[]; self.exception_frames=[]; self.finally_stack=[]
        self._line(f"uint64_t {self.root_mark}=saga_gc_root_mark();")
        for name,kind in params:
            self.scopes[-1][name]=kind
            self._root_if_ref(self._var(name),kind)
        if class_abi is not None:
            self.scopes[-1]["self"]=f"object[{class_abi.identity}]"
            self._line(f"{_dispatch_type_register_symbol(class_abi.identity)}();")
            self._line(f"if (!saga_dispatch_is_a(saga_object_type_id({self._var('self')}), UINT64_C(0x{class_abi.type_id:016x}))) {{ fputs(\"SAGA-R190: native method receiver type mismatch\\n\", stderr); exit(90); }}")
        if expression_body is not None:
            value,kind=self._expr(expression_body,result_type)
            if result_type=="unit":
                if value is not None or kind!="unit": raise AOTError(f"unit function '{label}' has non-unit expression body")
                self._return()
            else:
                if value is None or not self._assignable(result_type, kind): raise AOTError(f"return type mismatch in native function '{label}'")
                tmp=self._new_temp("return",kind); self._line(f"{tmp}={value};"); self._return(tmp)
        elif body is not None:
            for st in body.statements:
                if isinstance(st,ast.FunctionDecl): raise AOTError("Native Codegen ABI 0.35 does not lower lexical functions/closures")
                self._stmt(st)
            if result_type=="unit": self._return()
        else: raise AOTError(f"function '{label}' has no body for native codegen")
        self.current_class=None; self.indent -= 1

    def _concrete_decl_for_method(self, cls: ClassABI, method_name: str) -> ast.FunctionDecl | None:
        for decl in cls.declaration.methods:
            if decl.name.lexeme == method_name and not decl.abstract and (decl.body is not None or decl.expression_body is not None):
                return decl
        return None

    def _emit_dispatch_thunks_for_class(self, cls: ClassABI) -> None:
        if cls.interface or cls.type_params:
            return
        for method in sorted(cls.methods.values(), key=lambda item: item.name):
            if method.declaring_identity != cls.identity or self._concrete_decl_for_method(cls, method.name) is None:
                continue
            thunk = _dispatch_thunk_symbol(cls.identity, method.name)
            self.lines.append(f"static void {thunk}(SagaRef saga_self, const void *const *saga_args, void *saga_result) {{")
            self.indent = 1
            rendered = [f"*(({_ctype(t)} const*)saga_args[{i}])" for i, t in enumerate(method.params)]
            call = f"{method.symbol}(saga_self{', ' if rendered else ''}{', '.join(rendered)})"
            if method.result == "unit":
                self._line(call + ";")
                self._line("(void)saga_result;")
            else:
                self._line('if (!saga_result) { fputs("SAGA-R205: dynamic dispatch result storage missing\\n", stderr); exit(105); }')
                self._line(f"*(({_ctype(method.result)}*)saga_result) = {call};")
            self.indent = 0
            self.lines.append("}")
            self.lines.append("")

    def _emit_dispatch_registration_for_class(self, cls: ClassABI) -> None:
        if cls.type_params:
            return
        symbol = _dispatch_type_register_symbol(cls.identity)
        self.lines.append(f"void {symbol}(void) {{")
        self.indent = 1
        if cls.base_identity:
            self._line(f"{_dispatch_type_register_symbol(cls.base_identity)}();")
        for iface in cls.interface_identities:
            self._line(f"{_dispatch_type_register_symbol(iface)}();")
        base_id = self.class_by_identity[cls.base_identity].type_id if cls.base_identity and cls.base_identity in self.class_by_identity else 0
        self._line(f"saga_dispatch_register_type(UINT64_C(0x{cls.type_id:016x}), UINT64_C(0x{base_id:016x}));")
        for iface in cls.interface_identities:
            target = self.class_by_identity.get(iface)
            if target is not None:
                self._line(f"saga_dispatch_register_interface(UINT64_C(0x{cls.type_id:016x}), UINT64_C(0x{target.type_id:016x}));")
        if not cls.interface:
            for method in sorted(cls.methods.values(), key=lambda item: item.name):
                if method.declaring_identity != cls.identity or self._concrete_decl_for_method(cls, method.name) is None:
                    continue
                self._line(f"saga_dispatch_register_method(UINT64_C(0x{cls.type_id:016x}), UINT64_C(0x{method.dispatch_slot:016x}), {_dispatch_thunk_symbol(cls.identity, method.name)});")
        self.indent = 0
        self.lines.append("}")
        self.lines.append("")

    def _emit_virtual_wrappers_for_class(self, cls: ClassABI) -> None:
        if cls.type_params:
            return
        for method in sorted(cls.methods.values(), key=lambda item: item.name):
            args_decl=['SagaRef saga_self',*[f"{_ctype(t)} saga_p_{i}" for i,t in enumerate(method.params)]]
            symbol=_virtual_symbol(cls.identity, method.name)
            self.lines.append(f"{_ctype(method.result)} {symbol}({', '.join(args_decl)}) {{")
            self.indent=1
            self._line(f"{_dispatch_type_register_symbol(cls.identity)}();")
            if method.params:
                self._line(f"const void *saga_dispatch_args[{len(method.params)}] = {{{', '.join(f'&saga_p_{i}' for i in range(len(method.params)))}}};")
                argv = "saga_dispatch_args"
            else:
                argv = "NULL"
            if method.result == "unit":
                self._line(f"saga_dispatch_invoke(saga_object_type_id(saga_self), UINT64_C(0x{cls.type_id:016x}), UINT64_C(0x{method.dispatch_slot:016x}), saga_self, {argv}, NULL);")
            else:
                self._line(f"{_ctype(method.result)} saga_dispatch_result = ({_ctype(method.result)}){{0}};")
                self._line(f"saga_dispatch_invoke(saga_object_type_id(saga_self), UINT64_C(0x{cls.type_id:016x}), UINT64_C(0x{method.dispatch_slot:016x}), saga_self, {argv}, &saga_dispatch_result);")
                self._line("return saga_dispatch_result;")
            self.indent=0
            self.lines.append("}")
            self.lines.append("")

    def _emit_pending_specializations(self) -> None:
        # Emitting one specialization can discover another generic call, so run
        # to a fixed point. All definitions live in the calling translation unit.
        while True:
            progressed = False
            for key, (owner, decl, abi, mapping) in list(self.generic_function_specs.items()):
                if key in self.emitted_generic_functions:
                    continue
                self.emitted_generic_functions.add(key); progressed = True
                params_text=', '.join(f"{_ctype(kind)} {self._var(param.name.lexeme)}" for param,kind in zip(decl.parameters,abi.params)) or 'void'
                self.lines.append(f"{_ctype(abi.result)} {abi.symbol}({params_text}) {{")
                previous = self.active_type_mapping
                previous_unit = self.unit
                self.active_type_mapping = dict(mapping)
                self.unit = owner
                try:
                    params=[(param.name.lexeme,kind) for param,kind in zip(decl.parameters,abi.params)]
                    self._emit_function_body(decl.name.lexeme+str(abi.type_args),params,abi.result,decl.body,decl.expression_body)
                finally:
                    self.unit = previous_unit
                    self.active_type_mapping = previous
                self.lines.append('}'); self.lines.append('')

            for key, (owner, template, cls, mapping) in list(self.generic_class_specs.items()):
                if key in self.emitted_generic_classes:
                    continue
                self.emitted_generic_classes.add(key); progressed = True
                params=', '.join(f"{_ctype(field.type_name)} {self._var(field.name)}" for field in cls.fields) or 'void'
                self.lines.append(f"void {_dispatch_type_register_symbol(cls.identity)}(void);")
                self.lines.append(f"SagaRef {_constructor_symbol(self.output_unit.identity,cls.name)}({params}) {{")
                self.indent=1; self.scopes=[{field.name:field.type_name for field in cls.fields}]; self.current_result=f"object[{cls.identity}]"; self.current_class=cls
                self._line(f"uint64_t {self.root_mark}=saga_gc_root_mark();")
                self._line(f"{_dispatch_type_register_symbol(cls.identity)}();")
                for field in cls.fields: self._root_if_ref(self._var(field.name),field.type_name)
                obj=self._new_temp('object',f"object[{cls.identity}]"); self._line(f"{obj}=saga_object_new(UINT64_C(0x{cls.type_id:016x}),UINT64_C({len(cls.fields)}));")
                for field in cls.fields: self._line(f"saga_object_set({obj},UINT64_C({field.index}),{_heap_value(field.type_name,self._var(field.name))});")
                self._return(obj); self.indent=0; self.lines.append('}'); self.lines.append(''); self.current_class=None

                previous = self.active_type_mapping
                previous_unit = self.unit
                self.active_type_mapping = dict(mapping)
                self.unit = owner
                try:
                    for method_decl in template.declaration.methods:
                        if method_decl.abstract or (method_decl.body is None and method_decl.expression_body is None):
                            continue
                        abi=cls.methods[method_decl.name.lexeme]
                        args=[f"SagaRef {self._var('self')}",*[f"{_ctype(t)} {self._var(p.name.lexeme)}" for p,t in zip(method_decl.parameters,abi.params)]]
                        self.lines.append(f"{_ctype(abi.result)} {abi.symbol}({', '.join(args)}) {{")
                        params2=[('self',f"object[{cls.identity}]"),*[(p.name.lexeme,t) for p,t in zip(method_decl.parameters,abi.params)]]
                        self._emit_function_body(f"{cls.name}.{abi.name}",params2,abi.result,method_decl.body,method_decl.expression_body,class_abi=cls)
                        self.lines.append('}'); self.lines.append('')
                    self._emit_dispatch_thunks_for_class(cls)
                    self._emit_dispatch_registration_for_class(cls)
                    self._emit_virtual_wrappers_for_class(cls)
                finally:
                    self.unit = previous_unit
                    self.active_type_mapping = previous
            if not progressed:
                break

    def emit(self) -> str:
        self.lines=['#include "saga_native_abi035.h"','#include <stdint.h>','#include <stdio.h>','#include <stdlib.h>','']
        # Registration entry points are stable linker symbols. A separately
        # loaded module can register a subtype without recompiling this unit.
        for graph_unit in self.units.values():
            for graph_cls in graph_unit.classes.values():
                if not graph_cls.type_params:
                    self.lines.append(f"extern void {_dispatch_type_register_symbol(graph_cls.identity)}(void);")
        self.lines.append('')
        # Prototypes for top-level functions.
        for abi in sorted(self.unit.function_abis.values(),key=lambda x:x.name):
            params=', '.join(f"{_ctype(t)} saga_p_{i}" for i,t in enumerate(abi.params)) or 'void'; self.lines.append(f"{_ctype(abi.result)} {abi.symbol}({params});")
        # Class constructor and method prototypes.
        for cls in sorted(self.unit.classes.values(),key=lambda x:x.name):
            if not cls.abstract and not cls.interface and not cls.type_params:
                params=', '.join(f"{_ctype(f.type_name)} saga_p_{i}" for i,f in enumerate(cls.fields)) or 'void'; self.lines.append(f"SagaRef {_constructor_symbol(self.unit.identity,cls.name)}({params});")
            for method in sorted(cls.methods.values(),key=lambda x:x.name):
                args=['SagaRef saga_v_73656c66',*[f"{_ctype(t)} saga_p_{i}" for i,t in enumerate(method.params)]]; self.lines.append(f"{_ctype(method.result)} {method.symbol}({', '.join(args)});")
        for alias,dep_path in sorted(self.unit.imports.items()):
            dep=self.units[dep_path]
            for abi in sorted(dep.function_abis.values(),key=lambda x:x.name):
                if abi.visibility=='public':
                    params=', '.join(f"{_ctype(t)} saga_p_{i}" for i,t in enumerate(abi.params)) or 'void'; self.lines.append(f"extern {_ctype(abi.result)} {abi.symbol}({params});")
            for cls in sorted(dep.classes.values(),key=lambda x:x.name):
                if cls.visibility!='public': continue
                params=', '.join(f"{_ctype(f.type_name)} saga_p_{i}" for i,f in enumerate(cls.fields)) or 'void'; self.lines.append(f"extern SagaRef {_constructor_symbol(dep.identity,cls.name)}({params});")
                for method in sorted(cls.methods.values(),key=lambda x:x.name):
                    args=['SagaRef saga_self',*[f"{_ctype(t)} saga_p_{i}" for i,t in enumerate(method.params)]]; self.lines.append(f"extern {_ctype(method.result)} {method.symbol}({', '.join(args)});")
                    self.lines.append(f"extern {_ctype(method.result)} {_virtual_symbol(cls.identity,method.name)}({', '.join(args)});")
        # Graph-visible method prototypes keep separately compiled method symbols
        # linkable. Dynamic subtype discovery itself is handled by the runtime
        # dispatch registry and is not closed over this compilation graph.
        for graph_unit in self.units.values():
            if graph_unit is self.unit:
                continue
            for cls in graph_unit.classes.values():
                for method in cls.methods.values():
                    args=['SagaRef saga_self',*[f"{_ctype(t)} saga_p_{i}" for i,t in enumerate(method.params)]]
                    self.lines.append(f"extern {_ctype(method.result)} {method.symbol}({', '.join(args)});")
        self.lines.append('')

        # Open-world dispatch uses per-type registration and uniform thunks.
        for cls in self.unit.classes.values():
            self._emit_dispatch_thunks_for_class(cls)
        for cls in self.unit.classes.values():
            self._emit_dispatch_registration_for_class(cls)
        for cls in self.unit.classes.values():
            self._emit_virtual_wrappers_for_class(cls)

        # Constructors.
        for cls in self.unit.classes.values():
            if cls.abstract or cls.interface or cls.type_params:
                continue
            params=', '.join(f"{_ctype(field.type_name)} {self._var(field.name)}" for field in cls.fields) or 'void'
            self.lines.append(f"SagaRef {_constructor_symbol(self.unit.identity,cls.name)}({params}) {{")
            self.indent=1; self.scopes=[{field.name:field.type_name for field in cls.fields}]; self.current_result=f"object[{cls.identity}]"; self.current_class=cls
            self._line(f"uint64_t {self.root_mark}=saga_gc_root_mark();")
            self._line(f"{_dispatch_type_register_symbol(cls.identity)}();")
            for field in cls.fields: self._root_if_ref(self._var(field.name),field.type_name)
            obj=self._new_temp('object',f"object[{cls.identity}]"); self._line(f"{obj}=saga_object_new(UINT64_C(0x{cls.type_id:016x}),UINT64_C({len(cls.fields)}));")
            for field in cls.fields: self._line(f"saga_object_set({obj},UINT64_C({field.index}),{_heap_value(field.type_name,self._var(field.name))});")
            self._return(obj); self.indent=0; self.lines.append('}'); self.lines.append(''); self.current_class=None

        # Methods.
        for cls in self.unit.classes.values():
            if cls.type_params:
                continue
            for method_decl in cls.declaration.methods:
                if method_decl.abstract or (method_decl.body is None and method_decl.expression_body is None):
                    continue
                abi=cls.methods[method_decl.name.lexeme]
                args=[f"SagaRef {self._var('self')}",*[f"{_ctype(t)} {self._var(p.name.lexeme)}" for p,t in zip(method_decl.parameters,abi.params)]]
                self.lines.append(f"{_ctype(abi.result)} {abi.symbol}({', '.join(args)}) {{")
                params=[('self',f"object[{cls.identity}]"),*[(p.name.lexeme,t) for p,t in zip(method_decl.parameters,abi.params)]]
                self._emit_function_body(f"{cls.name}.{abi.name}",params,abi.result,method_decl.body,method_decl.expression_body,class_abi=cls)
                self.lines.append('}'); self.lines.append('')

        # Top-level functions.
        for fn_name,fn in self.unit.functions.items():
            if fn.type_params:
                continue
            abi=self.unit.function_abis[fn_name]
            params_text=', '.join(f"{_ctype(kind)} {self._var(param.name.lexeme)}" for param,kind in zip(fn.parameters,abi.params)) or 'void'
            self.lines.append(f"{_ctype(abi.result)} {abi.symbol}({params_text}) {{")
            params=[(param.name.lexeme,kind) for param,kind in zip(fn.parameters,abi.params)]
            self._emit_function_body(fn_name,params,abi.result,fn.body,fn.expression_body)
            self.lines.append('}'); self.lines.append('')
        self._emit_pending_specializations()
        return '\n'.join(self.lines)

class EntryCEmitter(ModuleCEmitter):
    def emit_entry(self) -> str:
        # Emit top-level entry statements in a dedicated native symbol. Functions,
        # enums and class bodies are emitted by the normal module emitter.
        base = self.emit()
        self.lines = [base, f"int {_entry_symbol(self.unit.virtual_id)}(void) {{"]
        self.indent = 1
        self.current_result = "unit"
        self.current_class = None
        self.scopes = [{}]
        self.gc_scope_marks = []
        self._line(f"uint64_t {self.root_mark}=saga_gc_root_mark();")
        for st in self.unit.program.statements:
            if isinstance(st, (ast.ModuleDecl, ast.UseStmt, ast.FunctionDecl, ast.EnumDecl, ast.ClassDecl)):
                continue
            self._stmt(st)
        self._line(f"saga_gc_unwind_roots({self.root_mark});")
        self._line("saga_gc_collect();")
        self._line("saga_gc_shutdown();")
        self._line("return 0;")
        self.indent = 0
        self.lines.append("}")
        self.lines.append("")
        self._emit_pending_specializations()
        return "\n".join(self.lines)


def _compile_c(cc: str, source: Path, output: Path, include_dir: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    command = [cc, "-O2", "-std=c11", "-fno-ident", "-fdata-sections", "-ffunction-sections"]
    if os.name != "nt":
        command.append("-pthread")
    command.extend(["-I", str(include_dir), "-c", str(source), "-o", str(tmp)])
    proc = subprocess.run(command, text=True, capture_output=True)
    if proc.returncode:
        tmp.unlink(missing_ok=True)
        raise AOTError(proc.stderr.strip() or "Native Codegen C compilation failed")
    os.replace(tmp, output)


def _support_key(cc: str) -> str:
    return _sha_bytes((ABI_VERSION + "\n" + _target_triple() + "\n" + _command_identity(cc, "--version") + "\n" + _support_header() + "\n" + _support_c()).encode("utf-8"))


def _ensure_support(build_dir: Path, cc: str) -> tuple[Path, Path, bool, str]:
    key = _support_key(cc)
    support_dir = build_dir / "support" / key[:20]
    header = support_dir / "saga_native_abi035.h"
    source = support_dir / "saga_native_abi035.c"
    obj = support_dir / ("saga_native_abi035.obj" if os.name == "nt" else "saga_native_abi035.o")
    manifest = support_dir / "support.json"

    def valid() -> bool:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data.get("key") == key and obj.is_file() and data.get("object_sha256") == _sha_file(obj)
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    if valid():
        return obj, header, False, key
    support_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(support_dir / ".build.lock"):
        if valid():
            return obj, header, False, key
        _write_atomic(header, _support_header().encode("utf-8"))
        _write_atomic(source, _support_c().encode("utf-8"))
        _compile_c(cc, source, obj, support_dir)
        _write_atomic(manifest, _canonical_bytes({"schema": 1, "key": key, "object_sha256": _sha_file(obj)}) + b"\n")
    return obj, header, True, key


def _object_valid(manifest: Path, obj: Path, key: str) -> bool:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data.get("schema") == OBJECT_SCHEMA and data.get("object_key") == key and obj.is_file() and data.get("object_sha256") == _sha_file(obj)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _emit_startup(entry_symbol: str) -> str:
    return f"extern int {entry_symbol}(void);\nint main(void) {{ return {entry_symbol}(); }}\n"


def _build_impl(source: str | Path, output: str | Path | None, *, build_dir: str | Path | None, force: bool) -> NativeCodegenBuildResult:
    source_input = Path(source).expanduser()
    # load_program performs the full reference parse/type check first. Direct
    # codegen therefore never bypasses the Natural Core checker.
    loaded = load_program(source_input)
    units, ids = _resolve_graph(loaded)
    _validate_codegen_graph(loaded, units)
    cc = _cc()
    target = _target_triple()
    root = loaded.root
    if build_dir is None:
        build_root = root / ".saga-build" / "native-codegen" / target
    else:
        raw = Path(build_dir).expanduser()
        bad = _lexical_symlink_component(raw)
        if bad is not None:
            raise AOTError(f"native codegen build directory may not use a symbolic link: {bad}")
        build_root = raw.absolute()
    build_root.mkdir(parents=True, exist_ok=True)
    generated = build_root / "generated"
    objects_dir = build_root / "objects"
    generated.mkdir(parents=True, exist_ok=True)
    objects_dir.mkdir(parents=True, exist_ok=True)

    support_obj, support_header, support_rebuilt, support_key = _ensure_support(build_root, cc)
    header_copy = generated / support_header.name
    if not header_copy.is_file() or _sha_file(header_copy) != _sha_file(support_header):
        shutil.copy2(support_header, header_copy)

    abi_by_path = {path: _emit_abi(build_root, unit) for path, unit in units.items()}
    dispatch_graph = []
    for graph_unit in sorted(units.values(), key=lambda item: item.identity):
        for cls in sorted(graph_unit.classes.values(), key=lambda item: item.identity):
            dispatch_graph.append({
                "identity": cls.identity, "type_id": cls.type_id, "base": cls.base_identity,
                "interfaces": list(cls.interface_identities), "abstract": cls.abstract, "interface": cls.interface,
                "methods": [
                    {"name": method.name, "slot": method.dispatch_slot, "symbol": method.symbol, "params": list(method.params), "result": method.result}
                    for method in sorted(cls.methods.values(), key=lambda item: item.name)
                ],
            })
    dispatch_graph_sha256 = _sha_bytes(_canonical_bytes(dispatch_graph))
    cc_identity = _command_identity(cc, "--version")
    compiled: list[str] = []
    reused: list[str] = []
    object_paths: list[Path] = []
    records: list[dict[str, object]] = []

    for path in sorted(loaded.files, key=lambda p: ids[p]):
        unit = units[path]
        dep_abis = {alias: abi_by_path[dep]["abi_sha256"] for alias, dep in sorted(unit.imports.items())}
        key_payload = {
            "schema": OBJECT_SCHEMA,
            "abi_version": ABI_VERSION,
            "language_version": LANGUAGE_VERSION,
            "implementation_version": IMPLEMENTATION_VERSION,
            "target": target,
            "virtual_id": unit.virtual_id,
            "module": unit.module_name,
            "source_sha256": _sha_bytes(unit.source.encode("utf-8")),
            "native_abi_sha256": abi_by_path[path]["abi_sha256"],
            "dependency_native_abis": dep_abis,
            "dispatch_graph_sha256": dispatch_graph_sha256,
            "compiler": cc_identity,
        }
        object_key = _sha_bytes(_canonical_bytes(key_payload))
        safe = _safe_name(unit.virtual_id)
        obj = objects_dir / (safe + (".obj" if os.name == "nt" else ".o"))
        manifest = objects_dir / (safe + ".codegen.json")
        if force or not _object_valid(manifest, obj, object_key):
            emitter = EntryCEmitter(unit, units, abi_by_path) if path == loaded.entry else ModuleCEmitter(unit, units, abi_by_path)
            c_text = emitter.emit_entry() if path == loaded.entry else emitter.emit()
            c_path = generated / (safe + ".c")
            _write_atomic(c_path, c_text.encode("utf-8"))
            _compile_c(cc, c_path, obj, generated)
            compiled.append(unit.virtual_id)
            record = {**key_payload, "object_key": object_key, "object_sha256": _sha_file(obj)}
            _write_atomic(manifest, _canonical_bytes(record) + b"\n")
        else:
            reused.append(unit.virtual_id)
            record = json.loads(manifest.read_text(encoding="utf-8"))
        object_paths.append(obj)
        records.append(record)

    entry_unit = units[loaded.entry]
    entry_sym = _entry_symbol(entry_unit.virtual_id)
    startup_text = _emit_startup(entry_sym)
    startup_key = _sha_bytes((startup_text + support_key + cc_identity).encode("utf-8"))
    startup_obj = objects_dir / ("startup.obj" if os.name == "nt" else "startup.o")
    startup_manifest = objects_dir / "startup.codegen.json"
    startup_rebuilt = force
    if not force:
        try:
            data = json.loads(startup_manifest.read_text(encoding="utf-8"))
            startup_rebuilt = not (data.get("startup_key") == startup_key and startup_obj.is_file() and data.get("object_sha256") == _sha_file(startup_obj))
        except (OSError, ValueError, json.JSONDecodeError):
            startup_rebuilt = True
    if startup_rebuilt:
        startup_c = generated / "startup.c"
        _write_atomic(startup_c, startup_text.encode("utf-8"))
        _compile_c(cc, startup_c, startup_obj, generated)
        _write_atomic(startup_manifest, _canonical_bytes({"schema": OBJECT_SCHEMA, "startup_key": startup_key, "object_sha256": _sha_file(startup_obj)}) + b"\n")

    out = Path(output).expanduser() if output is not None else loaded.entry.parent / (loaded.entry.stem + (".exe" if os.name == "nt" else ""))
    _reject_symlink_output(out)
    out = out.absolute()
    _reject_output_collision(loaded.entry, out, extra_inputs=(Path(cc).resolve(), support_obj.resolve(), startup_obj.resolve(), *tuple(p.resolve() for p in object_paths)))
    out.parent.mkdir(parents=True, exist_ok=True)

    link_key = _sha_bytes(_canonical_bytes({
        "schema": STATE_SCHEMA,
        "abi_version": ABI_VERSION,
        "target": target,
        "entry": entry_unit.virtual_id,
        "support_sha256": _sha_file(support_obj),
        "startup_sha256": _sha_file(startup_obj),
        "objects": [record["object_sha256"] for record in records],
        "linker": cc_identity,
    }))
    state_path = build_root / "state.json"
    linked = True
    if not force and state_path.is_file() and out.is_file():
        try:
            old = json.loads(state_path.read_text(encoding="utf-8"))
            linked = not (old.get("schema") == STATE_SCHEMA and old.get("link_key") == link_key and old.get("output_sha256") == _sha_file(out))
        except (OSError, ValueError, json.JSONDecodeError):
            linked = True
    if linked:
        tmp = _compiler_temp_output(out)
        try:
            cmd = [cc, str(startup_obj), *map(str, object_paths), str(support_obj), "-o", str(tmp)]
            system = platform.system().lower()
            if system == "linux":
                cmd.extend(["-Wl,--gc-sections", "-pthread", "-lm"])
            elif system == "darwin":
                cmd.extend(["-Wl,-dead_strip", "-pthread", "-lm"])
            elif os.name != "nt":
                cmd.extend(["-pthread", "-lm"])
            proc = subprocess.run(cmd, text=True, capture_output=True)
            if proc.returncode:
                raise AOTError(proc.stderr.strip() or "Native Codegen linker failed")
            os.replace(tmp, out)
            if os.name != "nt":
                out.chmod(out.stat().st_mode | 0o111)
        finally:
            tmp.unlink(missing_ok=True)

    state = {
        "schema": STATE_SCHEMA,
        "abi_version": ABI_VERSION,
        "language_version": LANGUAGE_VERSION,
        "implementation_version": IMPLEMENTATION_VERSION,
        "target": target,
        "entry": entry_unit.virtual_id,
        "support_key": support_key,
        "objects": records,
        "link_key": link_key,
        "output_sha256": _sha_file(out),
    }
    _write_atomic(state_path, _canonical_bytes(state) + b"\n")
    report_path = build_root / "last-build.json"
    report = {
        "schema": 1,
        "profile": "Native Codegen ABI 0.35",
        "language_version": LANGUAGE_VERSION,
        "target": target,
        "compiled_objects": compiled,
        "reused_objects": reused,
        "support_rebuilt": support_rebuilt,
        "startup_rebuilt": startup_rebuilt,
        "linked": linked,
        "go_runtime_linked": False,
        "output": str(out),
        "output_sha256": state["output_sha256"],
    }
    _write_atomic(report_path, json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    return NativeCodegenBuildResult(out, build_root, state_path, report_path, tuple(object_paths), tuple(compiled), tuple(reused), support_rebuilt, startup_rebuilt, linked)


def build_native_codegen(source: str | Path, output: str | Path | None = None, *, build_dir: str | Path | None = None, force: bool = False) -> NativeCodegenBuildResult:
    source_input = Path(source).expanduser()
    loaded = load_program(source_input)
    target = _target_triple()
    if build_dir is None:
        root = loaded.root / ".saga-build" / "native-codegen" / target
    else:
        raw = Path(build_dir).expanduser()
        bad = _lexical_symlink_component(raw)
        if bad is not None:
            raise AOTError(f"native codegen build directory may not use a symbolic link: {bad}")
        root = raw.absolute()
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(root / ".incremental-build.lock"):
        return _build_impl(source_input, output, build_dir=root, force=force)
