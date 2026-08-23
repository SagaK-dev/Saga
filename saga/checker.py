from __future__ import annotations

from dataclasses import dataclass, field
import copy
import difflib

from . import ast_nodes as ast
from .control_profile import validate_control_tick, validate_control_program
from .errors import TypeCheckError
from .stdlib import MODULES
from .tokens import Token, TokenKind
from .typesys import (
    ANY, BOOL, BYTES, CLASS_VALUE, DATETIME, DECIMAL, DURATION, ERROR, FUNCTION, FUTURE, INT, LIST,
    MAP, MODULE, OPTION, RANGE, RATIONAL, RESULT, SET, TEXT, UNIT, Type, common_numeric,
    is_assignable, is_numeric, is_typevar, parse_type, substitute, typevar_name, TYPECTOR, TYPEVAR,
)


@dataclass(slots=True)
class VariableInfo:
    type: Type
    mutable: bool
    moved: bool = False


@dataclass(slots=True)
class FunctionInfo:
    params: list[Type]
    return_type: Type | None
    declaration: ast.FunctionDecl | None = None
    type_params: list[str] = field(default_factory=list)
    owner: str | None = None
    abstract: bool = False

    def function_type(self) -> Type:
        result = self.return_type or ANY
        if self.declaration is not None and self.declaration.async_:
            result = FUTURE(result)
        return FUNCTION(self.params, result)


@dataclass(slots=True)
class FieldInfo:
    type: Type
    mutable: bool
    private: bool
    owner: str


@dataclass(slots=True)
class ClassInfo:
    name: str
    declaration: ast.ClassDecl
    type_params: list[str]
    base: Type | None
    interfaces: list[Type]
    abstract: bool
    interface: bool
    own_fields: dict[str, FieldInfo] = field(default_factory=dict)
    fields: dict[str, FieldInfo] = field(default_factory=dict)
    methods: dict[str, FunctionInfo] = field(default_factory=dict)
    own_methods: dict[str, FunctionInfo] = field(default_factory=dict)


@dataclass(slots=True)
class SourceModuleInfo:
    name: str
    members: dict[str, Type]


class PendingReturnType(Exception): pass


BUILTINS = {
    "print", "len", "text", "decimal", "ratio", "abs", "sqrt", "round",
    "min", "max", "sum", "mean", "append", "prepend", "get", "contains",
    "assert", "precision", "floor", "ceil", "slice", "reverse", "sort",
    "unique", "transform", "filter", "reduce", "find", "any", "all",
    "split", "join", "trim", "upper", "lower", "replace", "starts_with",
    "ends_with", "find_text", "substring", "map_of", "map_get", "map_put",
    "map_remove", "map_keys", "map_values", "map_contains", "set_of",
    "set_add", "set_remove", "set_contains", "set_union", "set_intersection",
    "int", "repeat", "set_at", "some", "none", "is_some", "is_none",
    "unwrap", "unwrap_or", "ok", "err", "is_ok", "is_err", "unwrap_ok", "unwrap_err", "unwrap_result_or",
}
BUILTINS.update({"Option", "Result"})


class TypeChecker:
    def __init__(self, filename: str = "<input>") -> None:
        self.filename = filename
        self.scopes: list[dict[str, VariableInfo]] = [{}]
        self.functions: dict[str, FunctionInfo] = {}
        self.classes: dict[str, ClassInfo] = {}
        self.enums: dict[str, set[str]] = {}
        self.enum_payloads: dict[str, dict[str, tuple[Type, ...]]] = {}
        self.enum_type_params: dict[str, list[str]] = {}
        self.current_return_type: Type | None = None
        self.current_function: ast.FunctionDecl | None = None
        self.current_class: str | None = None
        self.loop_depth = 0
        self.taskgroup_depth = 0
        self.resolving_inference = False
        self.local_function_infos: dict[int, FunctionInfo] = {}
        # Return statements inside closures belong to the closure, not to an
        # enclosing function. Each active closure gets its own collection of
        # observed return types so result inference remains lexical.
        self.closure_returns: list[list[Type]] = []
        self.closure_expected_returns: list[Type | None] = []
        # Lexically visible generic parameters. Local declarations inside a
        # generic function/class must resolve T as a type variable, not as a
        # nominal class named "T".
        self.active_type_vars: list[set[str]] = [set()]
        self.source_modules: dict[str, SourceModuleInfo] = {}

        # Option/Result are intrinsic Generic ADTs. Their runtime representation
        # stays compatible with the long-standing some/none/ok/err helpers while
        # the type checker exposes the same constructor/match model as user ADTs.
        self.enums.update({"Option": {"Some", "None"}, "Result": {"Ok", "Err"}})
        self.enum_payloads.update({
            "Option": {"Some": (TYPEVAR("T"),), "None": ()},
            "Result": {"Ok": (TYPEVAR("T"),), "Err": (TYPEVAR("E"),)},
        })
        self.enum_type_params.update({"Option": ["T"], "Result": ["T", "E"]})
        self.scopes[0]["Option"] = VariableInfo(Type("enumtype:Option"), False)
        self.scopes[0]["Result"] = VariableInfo(Type("enumtype:Result"), False)

    def check(self, program: ast.Program) -> None:
        # Module interfaces are name-resolution inputs, not ordinary executable
        # statements. Import their public surfaces before declaring local classes
        # so `class Child extends m.Base` and signatures using `m.Type` work.
        for stmt in program.statements:
            if isinstance(stmt, ast.SourceModuleStmt):
                self._check_source_module(stmt)
        new_classes: list[ClassInfo] = []
        for stmt in program.statements:
            if isinstance(stmt, ast.EnumDecl):
                self._declare_enum(stmt)
            elif isinstance(stmt, ast.ClassDecl):
                self._declare_class_shell(stmt)
                new_classes.append(self.classes[stmt.name.lexeme])
            elif isinstance(stmt, ast.FunctionDecl):
                self._declare_function_signature(stmt)
        # In a SagaSession the checker already contains members from earlier
        # submissions. Re-registering every class on each incremental check
        # turns valid fields/methods into false duplicate declarations.
        for info in new_classes:
            self._declare_class_members(info)
        self._validate_declared_types(program)
        self._resolve_inheritance()
        self._refresh_source_module_constructors()
        self._validate_class_contracts()
        self._resolve_inferred_expression_functions()
        for stmt in program.statements:
            self._check_stmt(stmt)
        for violation in validate_control_program(program):
            self._error(violation.token, violation.message, violation.hint, diagnostic_id=violation.code)
        if any(isinstance(stmt, ast.ModuleDecl) for stmt in program.statements):
            self._validate_module_public_surface(program)

    def _declare_enum(self, stmt: ast.EnumDecl) -> None:
        name = stmt.name.lexeme
        if name in self.enums or name in self.classes or name in self.functions or name in BUILTINS or name in MODULES:
            self._error(stmt.name, f"名前 '{name}' はすでに使われています", diagnostic_id="SAGA-T108")
        variants = [variant.name.lexeme for variant in stmt.variants]
        if len(set(variants)) != len(variants):
            self._error(stmt.name, f"enum '{name}' のvariantが重複しています", diagnostic_id="SAGA-T108")
        if len(set(stmt.type_params)) != len(stmt.type_params):
            self._error(stmt.name, f"enum '{name}' の型引数が重複しています", diagnostic_id="SAGA-T108")
        type_vars = set(stmt.type_params)
        payloads: dict[str, tuple[Type, ...]] = {}
        for variant in stmt.variants:
            try:
                payloads[variant.name.lexeme] = tuple(parse_type(text, type_vars) for text in variant.payload_types)
            except ValueError as exc:
                self._error(variant.name, str(exc), diagnostic_id="SAGA-T106")
        self.enums[name] = set(variants)
        self.enum_payloads[name] = payloads
        self.enum_type_params[name] = list(stmt.type_params)
        self.scopes[-1][name] = VariableInfo(Type(f"enumtype:{name}"), False)

    def _declare_class_shell(self, stmt: ast.ClassDecl) -> None:
        name = stmt.name.lexeme
        if name in self.classes or name in self.enums or name in self.functions or name in BUILTINS or name in MODULES:
            self._error(stmt.name, f"名前 '{name}' はすでに使われています")
        type_vars = set(stmt.type_params)
        try:
            base = parse_type(stmt.base_name, type_vars) if stmt.base_name else None
            interfaces = [parse_type(value, type_vars) for value in stmt.interfaces]
        except ValueError as exc:
            self._error(stmt.name, str(exc))
        self.classes[name] = ClassInfo(
            name, stmt, list(stmt.type_params), base, interfaces, stmt.abstract, stmt.interface,
        )

    def _declare_function_signature(self, stmt: ast.FunctionDecl, owner: str | None = None) -> FunctionInfo:
        name = stmt.name.lexeme
        if owner is None and (name in BUILTINS or name in MODULES or name in self.functions or name in self.classes or name in self.enums):
            self._error(stmt.name, f"関数 '{name}' はすでに定義されています")
        type_vars = set(stmt.type_params)
        if owner:
            type_vars.update(self.classes[owner].type_params)
        try:
            params = [parse_type(param.type_name, type_vars) for param in stmt.parameters]
            return_type = parse_type(stmt.return_type, type_vars) if stmt.return_type else None
        except ValueError as exc: self._error(stmt.name, str(exc))
        if stmt.body is not None and return_type is None: return_type = UNIT
        if stmt.abstract and return_type is None: self._error(stmt.name, "抽象メソッドには戻り値型が必要です")
        info = FunctionInfo(params, return_type, stmt, list(stmt.type_params), owner, stmt.abstract)
        if owner is None: self.functions[name] = info
        return info

    def _declare_class_members(self, info: ClassInfo) -> None:
        type_vars = set(info.type_params)
        for field in info.declaration.fields:
            if field.name.lexeme in info.own_fields:
                self._error(field.name, f"フィールド '{field.name.lexeme}' が重複しています")
            try: field_type = parse_type(field.type_name, type_vars)
            except ValueError as exc: self._error(field.name, str(exc))
            info.own_fields[field.name.lexeme] = FieldInfo(field_type, field.mutable, field.private, info.name)
        for method in info.declaration.methods:
            if method.name.lexeme in info.own_methods:
                self._error(method.name, f"メソッド '{method.name.lexeme}' が重複しています")
            method_info = self._declare_function_signature(method, info.name)
            info.own_methods[method.name.lexeme] = method_info
            # Keep the declared surface available before inheritance resolution
            # (notably for interface relations in the same compilation unit).
            info.methods[method.name.lexeme] = method_info

    def _validate_type_reference(self, value: Type, token: Token) -> None:
        if is_typevar(value) or value == ANY or value.name.startswith("typector:"):
            return
        if value.name == "typeapply":
            if not value.args or not is_typevar(value.args[0]):
                self._error(token, "higher-kinded application requires a type-constructor variable", diagnostic_id="SAGA-T103")
            for argument in value.args[1:]:
                self._validate_type_reference(argument, token)
            return
        if value.name == "fn":
            for param in value.args:
                self._validate_type_reference(param, token)
            if value.result is not None:
                self._validate_type_reference(value.result, token)
            return
        for argument in value.args:
            self._validate_type_reference(argument, token)
        if value.name.startswith("object:"):
            name = value.name.split(":", 1)[1]
            if name in self.enums:
                params = self.enum_type_params.get(name, [])
                if len(value.args) != len(params):
                    self._error(token, f"enum型 '{name}' には {len(params)} 個の型引数が必要です", diagnostic_id="SAGA-T103")
                return
            info = self.classes.get(name)
            if info is None:
                self._error(token, f"型 '{name}' が見つかりません")
            if len(value.args) != len(info.type_params):
                self._error(token, f"型 '{name}' には {len(info.type_params)} 個の型引数が必要です")

    def _validate_function_types(self, info: FunctionInfo, token: Token) -> None:
        for param in info.params:
            self._validate_type_reference(param, token)
        if info.return_type is not None:
            self._validate_type_reference(info.return_type, token)

    def _validate_declared_types(self, program: ast.Program) -> None:
        # Validation is intentionally delayed until every class shell in the
        # submission exists, preserving forward references while rejecting
        # misspelled/unknown nominal types before execution.
        for stmt in program.statements:
            if isinstance(stmt, ast.FunctionDecl):
                self._validate_function_types(self.functions[stmt.name.lexeme], stmt.name)
            elif isinstance(stmt, ast.ClassDecl):
                info = self.classes[stmt.name.lexeme]
                for field in info.own_fields.values():
                    self._validate_type_reference(field.type, stmt.name)
                for method in info.own_methods.values():
                    self._validate_function_types(method, stmt.name)

    @staticmethod
    def _object_name(value: Type | None) -> str | None:
        if value is None or not value.name.startswith("object:"):
            return None
        return value.name.split(":", 1)[1]

    @staticmethod
    def _specialize_field(field: FieldInfo, mapping: dict[str, Type]) -> FieldInfo:
        return FieldInfo(substitute(field.type, mapping), field.mutable, field.private, field.owner)

    @staticmethod
    def _specialize_function(fn: FunctionInfo, mapping: dict[str, Type]) -> FunctionInfo:
        return FunctionInfo(
            [substitute(value, mapping) for value in fn.params],
            substitute(fn.return_type, mapping) if fn.return_type else None,
            fn.declaration, list(fn.type_params), fn.owner, fn.abstract,
        )

    def _relation_info(self, relation: Type, token: Token, *, interface: bool) -> tuple[ClassInfo, dict[str, Type]]:
        name = self._object_name(relation)
        if name is None or name not in self.classes:
            self._error(token, f"型 '{relation}' が見つかりません")
        target = self.classes[name]
        if target.interface != interface:
            expected = "interface" if interface else "class"
            self._error(token, f"'{name}' は{expected}ではありません")
        if len(relation.args) != len(target.type_params):
            self._error(token, f"'{name}' には {len(target.type_params)} 個の型引数が必要です")
        return target, dict(zip(target.type_params, relation.args))

    def _resolve_inheritance(self) -> None:
        visiting: set[str] = set(); resolved: set[str] = set()
        def resolve(name: str) -> None:
            if name in resolved: return
            if name in visiting: self._error(self.classes[name].declaration.name, "クラス継承が循環しています")
            visiting.add(name); info = self.classes[name]
            inherited_fields: dict[str, FieldInfo] = {}; inherited_methods: dict[str, FunctionInfo] = {}
            if info.base:
                base, mapping = self._relation_info(info.base, info.declaration.name, interface=False)
                resolve(base.name)
                inherited_fields.update({key: self._specialize_field(value, mapping) for key, value in base.fields.items()})
                inherited_methods.update({key: self._specialize_function(value, mapping) for key, value in base.methods.items()})
            for field_name in info.own_fields:
                if field_name in inherited_fields: self._error(info.declaration.name, f"継承フィールド '{field_name}' を再定義できません")
            info.fields = {**inherited_fields, **info.own_fields}
            for method_name, method in info.own_methods.items():
                inherited = inherited_methods.get(method_name)
                if inherited is not None:
                    if method.declaration is not None and not method.declaration.override:
                        self._error(method.declaration.name, f"メソッド '{method_name}' は親メソッドを上書きするため override が必要です", diagnostic_id="SAGA-T110")
                    self._require_override_compatible(inherited, method, method.declaration.name)
                elif method.declaration is not None and method.declaration.override:
                    interface_match = False
                    for relation in info.interfaces:
                        iface, mapping = self._relation_info(relation, info.declaration.name, interface=True)
                        required = iface.methods.get(method_name)
                        if required and self._specialize_function(required, mapping):
                            interface_match = True
                            break
                    if not interface_match:
                        self._error(method.declaration.name, f"override fn '{method_name}' に対応する親メソッドまたはinterfaceメソッドがありません", diagnostic_id="SAGA-T110")
            info.methods = {**inherited_methods, **info.own_methods}
            visiting.remove(name); resolved.add(name)
        for name in self.classes: resolve(name)

    def _refresh_source_module_constructors(self) -> None:
        for bind, module in self.source_modules.items():
            for name in list(module.members):
                info = self.classes.get(f"{bind}.{name}")
                if info is None:
                    continue
                params = [field.type for field in info.fields.values()]
                ret_args = tuple(TYPEVAR(n) for n in info.type_params)
                module.members[name] = FUNCTION(params, Type(f"object:{info.name}", ret_args))

    def _validate_class_contracts(self) -> None:
        for info in self.classes.values():
            for relation in info.interfaces:
                iface, mapping = self._relation_info(relation, info.declaration.name, interface=True)
                for name, raw_required in iface.methods.items():
                    required = self._specialize_function(raw_required, mapping)
                    actual = info.methods.get(name)
                    if actual is None: self._error(info.declaration.name, f"interface '{iface.name}' のメソッド '{name}' が必要です")
                    if actual.owner == info.name and actual.declaration is not None and not actual.declaration.override:
                        self._error(actual.declaration.name, f"interface '{iface.name}' のメソッド '{name}' を実装するには override が必要です", diagnostic_id="SAGA-T110")
                    self._require_override_compatible(required, actual, actual.declaration.name)
            if not info.abstract and not info.interface:
                missing = [name for name, method in info.methods.items() if method.abstract]
                if missing: self._error(info.declaration.name, f"抽象メソッドを実装してください: {', '.join(missing)}")

    def _require_override_compatible(self, parent: FunctionInfo, child: FunctionInfo, token: Token) -> None:
        if len(parent.type_params) != len(child.type_params):
            self._error(token, "オーバーライドするgeneric methodの型パラメータ数を親と揃えてください")
        # Generic method parameters are alpha-equivalent: an interface may use
        # U while its implementation uses V. Normalize the implementation's
        # method-local variables to the contract names before comparing types.
        alpha = {
            child_name: TYPEVAR(parent_name)
            for parent_name, child_name in zip(parent.type_params, child.type_params)
        }
        child_params = [substitute(value, alpha) for value in child.params]
        child_return = substitute(child.return_type, alpha) if child.return_type else None
        if len(parent.params) != len(child_params) or any(a != b for a, b in zip(parent.params, child_params)):
            self._error(token, "オーバーライドするメソッドの引数型を親と揃えてください")
        if parent.return_type and child_return and not self._is_assignable(parent.return_type, child_return):
            self._error(token, "オーバーライドするメソッドの戻り値型が親と互換ではありません")

    def _resolve_inferred_expression_functions(self) -> None:
        pending = [info for info in self.functions.values() if info.return_type is None]
        self.resolving_inference = True
        try:
            for _ in range(len(pending) + 1):
                progress = False
                for info in pending[:]:
                    stmt = info.declaration; assert stmt and stmt.expression_body is not None
                    self.scopes.append({})
                    try:
                        for param, param_type in zip(stmt.parameters, info.params): self.scopes[-1][param.name.lexeme] = VariableInfo(param_type, False)
                        inferred = self._check_expr(stmt.expression_body)
                    except (PendingReturnType, TypeCheckError): continue
                    finally: self.scopes.pop()
                    info.return_type = inferred; pending.remove(info); progress = True
                if not pending or not progress: break
        finally: self.resolving_inference = False

    @staticmethod
    def _qualify_module_type(value: Type, bind: str, public_classes: set[str]) -> Type:
        if value.name == "fn":
            return FUNCTION(
                [TypeChecker._qualify_module_type(v, bind, public_classes) for v in value.args],
                TypeChecker._qualify_module_type(value.result or UNIT, bind, public_classes),
            )
        args = tuple(TypeChecker._qualify_module_type(v, bind, public_classes) for v in value.args)
        if value.name.startswith("object:"):
            name = value.name.split(":", 1)[1]
            if name in public_classes:
                return Type(f"object:{bind}.{name}", args)
        return Type(value.name, args, value.result)

    def _clone_module_class(self, info: ClassInfo, bind: str, public_classes: set[str]) -> ClassInfo:
        clone = copy.deepcopy(info)
        qualified = f"{bind}.{info.name}"
        clone.name = qualified
        clone.base = self._qualify_module_type(clone.base, bind, public_classes) if clone.base else None
        clone.interfaces = [self._qualify_module_type(v, bind, public_classes) for v in clone.interfaces]
        for table in (clone.own_fields, clone.fields):
            for name, item in list(table.items()):
                item.type = self._qualify_module_type(item.type, bind, public_classes)
                if item.owner == info.name:
                    item.owner = qualified
        for table in (clone.own_methods, clone.methods):
            for name, item in list(table.items()):
                item.params = [self._qualify_module_type(v, bind, public_classes) for v in item.params]
                if item.return_type is not None:
                    item.return_type = self._qualify_module_type(item.return_type, bind, public_classes)
                if item.owner == info.name:
                    item.owner = qualified
        return clone

    def _public_type_is_exportable(self, value: Type, public_classes: set[str], token: Token) -> None:
        if value.name == "fn":
            for p in value.args:
                self._public_type_is_exportable(p, public_classes, token)
            if value.result is not None:
                self._public_type_is_exportable(value.result, public_classes, token)
            return
        for arg in value.args:
            self._public_type_is_exportable(arg, public_classes, token)
        if value.name.startswith("object:"):
            name = value.name.split(":", 1)[1]
            if "." in name:
                self._error(
                    token,
                    f"public API が依存module型 '{name}' を直接公開しています",
                    "0.30では再公開は行わず、自moduleのpublic型で包むか将来の明示的re-exportを使用してください",
                    "SAGA-T118",
                )
            if name not in public_classes:
                self._error(token, f"public API が internal 型 '{name}' を公開しています", "型を public にするか、public signatureから外してください", "SAGA-T118")

    def _validate_module_public_surface(self, program: ast.Program) -> None:
        public_classes = {
            stmt.name.lexeme for stmt in program.statements
            if isinstance(stmt, (ast.ClassDecl, ast.EnumDecl)) and stmt.visibility == "public"
        }
        for stmt in program.statements:
            if getattr(stmt, "visibility", "internal") != "public":
                continue
            if isinstance(stmt, ast.VarDecl):
                info = self._find_var(stmt.name.lexeme)
                if info is not None:
                    self._public_type_is_exportable(info.type, public_classes, stmt.name)
            elif isinstance(stmt, ast.FunctionDecl):
                info = self.functions.get(stmt.name.lexeme)
                if info is not None:
                    self._public_type_is_exportable(info.function_type(), public_classes, stmt.name)
            elif isinstance(stmt, ast.ClassDecl):
                info = self.classes[stmt.name.lexeme]
                if info.base is not None:
                    self._public_type_is_exportable(info.base, public_classes, stmt.name)
                for relation in info.interfaces:
                    self._public_type_is_exportable(relation, public_classes, stmt.name)
                for field in info.own_fields.values():
                    self._public_type_is_exportable(field.type, public_classes, stmt.name)
                for method in info.own_methods.values():
                    self._public_type_is_exportable(method.function_type(), public_classes, stmt.name)

    @staticmethod
    def _interface_type(text: str, type_vars: set[str] | None = None) -> Type:
        raw = text.strip()
        if raw.startswith("object:"):
            raw = raw.split(":", 1)[1]
        return parse_type(raw, type_vars)

    def _check_source_module_interface(self, stmt: ast.SourceModuleStmt) -> None:
        data = stmt.interface or {}
        bind = stmt.bind_name or stmt.name
        exports = data.get("exports", [])
        public_classes = {item.get("name") for item in exports if item.get("kind") in {"class", "interface", "enum"}}
        members: dict[str, Type] = {}
        for item in exports:
            if item.get("kind") == "enum":
                name = str(item["name"]); qualified = f"{bind}.{name}"
                type_params = [str(v) for v in item.get("type_params", [])]
                vars_ = set(type_params)
                raw_variants = item.get("variants", [])
                variants: set[str] = set()
                payloads: dict[str, tuple[Type, ...]] = {}
                for raw_variant in raw_variants:
                    if isinstance(raw_variant, dict):
                        vname = str(raw_variant.get("name", ""))
                        payload_text = [str(v) for v in raw_variant.get("payload", [])]
                    else:
                        vname = str(raw_variant); payload_text = []
                    if not vname:
                        continue
                    variants.add(vname)
                    parsed = tuple(self._qualify_module_type(self._interface_type(t, vars_), bind, public_classes) for t in payload_text)
                    payloads[vname] = parsed
                self.enums[qualified] = variants
                self.enum_payloads[qualified] = payloads
                self.enum_type_params[qualified] = type_params
                members[name] = Type(f"enumtype:{qualified}")
        # Class shells first so public signatures can refer forward to other
        # exported classes without re-checking the implementation body.
        class_items = [item for item in exports if item.get("kind") in {"class", "interface"}]
        for item in class_items:
            name = str(item["name"]); qualified = f"{bind}.{name}"
            type_params = [str(v) for v in item.get("type_params", [])]
            vars_ = set(type_params)
            base = self._interface_type(item["base"], vars_) if item.get("base") else None
            interfaces = [self._interface_type(v, vars_) for v in item.get("interfaces", [])]
            base = self._qualify_module_type(base, bind, public_classes) if base else None
            interfaces = [self._qualify_module_type(v, bind, public_classes) for v in interfaces]
            name_token = Token(TokenKind.IDENT, name, None, stmt.token.line, stmt.token.column, stmt.token.filename)
            decl = ast.ClassDecl(stmt.token, name_token, [], [], type_params, None, [], [], bool(item.get("abstract")), item.get("kind") == "interface", "public")
            info = ClassInfo(qualified, decl, type_params, base, interfaces, bool(item.get("abstract")), item.get("kind") == "interface")
            self.classes[qualified] = info
        for item in class_items:
            name = str(item["name"]); qualified = f"{bind}.{name}"; info = self.classes[qualified]
            vars_ = set(info.type_params)
            for f in item.get("fields", []):
                typ = self._qualify_module_type(self._interface_type(str(f["type"]), vars_), bind, public_classes)
                fi = FieldInfo(typ, bool(f.get("mutable")), bool(f.get("private")), qualified)
                info.own_fields[str(f["name"])] = fi; info.fields[str(f["name"])] = fi
            for m in item.get("methods", []):
                method_vars = vars_ | {str(v) for v in m.get("type_params", [])}
                params = [self._qualify_module_type(self._interface_type(str(v), method_vars), bind, public_classes) for v in m.get("params", [])]
                ret = self._qualify_module_type(self._interface_type(str(m.get("return", "unit")), method_vars), bind, public_classes)
                mi = FunctionInfo(params, ret, None, [str(v) for v in m.get("type_params", [])], qualified, bool(m.get("abstract")))
                info.own_methods[str(m["name"])] = mi; info.methods[str(m["name"])] = mi
            ctor = [f.type for f in info.fields.values()]
            ret_args = tuple(TYPEVAR(n) for n in info.type_params)
            members[name] = FUNCTION(ctor, Type(f"object:{qualified}", ret_args))
        for item in exports:
            kind, name = item.get("kind"), str(item.get("name"))
            if kind == "var":
                t = self._qualify_module_type(self._interface_type(str(item["type"])), bind, public_classes)
                members[name] = t
            elif kind == "fn":
                vars_ = {str(v) for v in item.get("type_params", [])}
                params = [self._qualify_module_type(self._interface_type(str(v), vars_), bind, public_classes) for v in item.get("params", [])]
                ret = self._qualify_module_type(self._interface_type(str(item.get("return", "unit")), vars_), bind, public_classes)
                members[name] = FUNCTION(params, ret)
        self.source_modules[bind] = SourceModuleInfo(stmt.name, members)
        self.scopes[-1][bind] = VariableInfo(Type(f"srcmodule:{bind}"), False)

    def _check_source_module(self, stmt: ast.SourceModuleStmt) -> None:
        bind = stmt.bind_name or stmt.name
        if self._find_var(bind) is not None or bind in self.functions or bind in self.classes:
            self._error(stmt.token, f"module alias '{bind}' はすでに使われています")
        if stmt.interface is not None:
            self._check_source_module_interface(stmt)
            return
        child = TypeChecker(stmt.token.filename or self.filename)
        child_program = ast.Program(stmt.statements)
        child.check(child_program)
        # The loader strips the nested module directive from the body because
        # SourceModuleStmt already carries that identity. Validate the ABI
        # surface explicitly so source checking and `.smi.json` compilation
        # enforce the same public/internal boundary.
        child._validate_module_public_surface(child_program)
        public_classes = {
            d.name.lexeme for d in stmt.statements
            if isinstance(d, (ast.ClassDecl, ast.EnumDecl)) and d.visibility == "public"
        }
        members: dict[str, Type] = {}
        for d in stmt.statements:
            if isinstance(d, ast.VarDecl) and d.visibility == "public":
                info = child._find_var(d.name.lexeme)
                if info is not None:
                    self._public_type_is_exportable(info.type, public_classes, d.name)
                    members[d.name.lexeme] = self._qualify_module_type(info.type, bind, public_classes)
            elif isinstance(d, ast.FunctionDecl) and d.visibility == "public":
                info = child.functions[d.name.lexeme]
                ft = info.function_type()
                self._public_type_is_exportable(ft, public_classes, d.name)
                members[d.name.lexeme] = self._qualify_module_type(ft, bind, public_classes)
            elif isinstance(d, ast.EnumDecl) and d.visibility == "public":
                qualified = f"{bind}.{d.name.lexeme}"
                self.enums[qualified] = set(child.enums[d.name.lexeme])
                public_names = {x.name.lexeme for x in stmt.statements if isinstance(x, (ast.ClassDecl, ast.EnumDecl)) and x.visibility == "public"}
                self.enum_payloads[qualified] = {
                    variant: tuple(self._qualify_module_type(t, bind, public_names) for t in payload)
                    for variant, payload in child.enum_payloads.get(d.name.lexeme, {}).items()
                }
                self.enum_type_params[qualified] = list(child.enum_type_params.get(d.name.lexeme, []))
                members[d.name.lexeme] = Type(f"enumtype:{qualified}")
            elif isinstance(d, ast.ClassDecl) and d.visibility == "public":
                ci = child.classes[d.name.lexeme]
                clone = self._clone_module_class(ci, bind, public_classes)
                self.classes[clone.name] = clone
                ctor = [clone.fields[name].type for name in clone.fields]
                ret_args = tuple(Type("typevar", (Type(n),)) for n in clone.type_params)
                members[d.name.lexeme] = FUNCTION(ctor, Type(f"object:{clone.name}", ret_args))
        self.source_modules[bind] = SourceModuleInfo(stmt.name, members)
        self.scopes[-1][bind] = VariableInfo(Type(f"srcmodule:{bind}"), False)

    def _enum_identity(self, value: Type | None) -> tuple[str, tuple[Type, ...]] | None:
        if value is None:
            return None
        if value.name == "option" and len(value.args) == 1:
            return "Option", value.args
        if value.name == "result" and len(value.args) == 2:
            return "Result", value.args
        if value.name.startswith("object:"):
            name = value.name.split(":", 1)[1]
            if name in self.enums:
                return name, value.args
        return None

    def _enum_match_pattern(self, expr: ast.Expr, enum_type: Type | None) -> tuple[str, dict[str, VariableInfo]] | None:
        identity = self._enum_identity(enum_type)
        if identity is None:
            return None
        enum_name, enum_args = identity
        callee: ast.Expr = expr.callee if isinstance(expr, ast.Call) else expr
        qname = self._qualified_expr_name(callee)
        if not qname or "." not in qname:
            return None
        owner, variant = qname.rsplit(".", 1)
        if owner != enum_name or variant not in self.enums.get(enum_name, set()):
            return None
        params = self.enum_type_params.get(enum_name, [])
        mapping = {name: arg for name, arg in zip(params, enum_args)}
        payload = tuple(substitute(t, mapping) for t in self.enum_payloads.get(enum_name, {}).get(variant, ()))
        args = expr.arguments if isinstance(expr, ast.Call) else []
        if len(args) != len(payload):
            token = getattr(expr, "paren", None) or getattr(expr, "name", None) or getattr(callee, "name", None)
            self._error(token, f"enum variant '{enum_name}.{variant}' は {len(payload)} 個のpayloadを必要とします", diagnostic_id="SAGA-T103")
        bindings: dict[str, VariableInfo] = {}
        for arg, typ in zip(args, payload):
            if not isinstance(arg, ast.Variable):
                token = getattr(arg, "name", None) or getattr(expr, "paren", None)
                self._error(token, "matchのpayload patternには変数名または '_' を書いてください", diagnostic_id="SAGA-T103")
            name = arg.name.lexeme
            if name == "_":
                continue
            if name in bindings:
                self._error(arg.name, f"match payload変数 '{name}' が重複しています", diagnostic_id="SAGA-T108")
            bindings[name] = VariableInfo(typ, False)
        return variant, bindings

    def _check_stmt(self, stmt: ast.Stmt) -> None:
        if isinstance(stmt, (ast.ModuleDecl, ast.EnumDecl)):
            return
        if isinstance(stmt, ast.SourceModuleStmt):
            bind = stmt.bind_name or stmt.name
            if bind not in self.source_modules:
                self._check_source_module(stmt)
        elif isinstance(stmt, ast.UseStmt):
            if stmt.source_path is not None:
                self._error(
                    stmt.module,
                    "ソース単位のuseが展開されていません",
                    "ファイルは saga run/check または compile_file で読み込んでください",
                )
            name = stmt.module.lexeme
            if name not in MODULES: self._error(stmt.module, f"標準モジュール '{name}' はありません")
            bind = stmt.alias.lexeme if stmt.alias is not None else name
            scope = self.scopes[-1]
            if bind in scope: self._error(stmt.alias or stmt.module, f"'{bind}' はすでに読み込まれています")
            scope[bind] = VariableInfo(MODULE(name), False)
        elif isinstance(stmt, ast.VarDecl): self._check_var(stmt)
        elif isinstance(stmt, ast.Assign): self._check_assign(stmt)
        elif isinstance(stmt, ast.ExpressionStmt): self._check_expr(stmt.expression)
        elif isinstance(stmt, ast.DeferStmt): self._check_expr(stmt.value)
        elif isinstance(stmt, ast.UsingStmt):
            resource_type = self._check_expr(stmt.initializer)
            if not self._is_resource_type(resource_type) and resource_type != ANY:
                self._error(stmt.keyword, "using には deterministic close を持つ資源が必要です", diagnostic_id="SAGA-T174")
            self.scopes.append({stmt.name.lexeme: VariableInfo(resource_type, False)})
            try:
                self._predeclare_local_functions(stmt.body.statements)
                for child in stmt.body.statements: self._check_stmt(child)
            finally:
                self.scopes.pop()
        elif isinstance(stmt, ast.TaskGroupStmt):
            self.taskgroup_depth += 1
            try: self._check_block(stmt.body)
            finally: self.taskgroup_depth -= 1
        elif isinstance(stmt, ast.Block): self._check_block(stmt)
        elif isinstance(stmt, ast.IfStmt):
            self._require(self._check_expr(stmt.condition) == BOOL, stmt.keyword, "if の条件は bool 型である必要があります", diagnostic_id="SAGA-T104")
            self._check_block(stmt.then_branch)
            if stmt.else_branch: self._check_block(stmt.else_branch)
        elif isinstance(stmt, ast.MatchStmt):
            value_type = self._check_expr(stmt.value)
            enum_identity = self._enum_identity(value_type)
            enum_name = enum_identity[0] if enum_identity is not None else None
            seen: set[str] = set()
            covered: set[str] = set()
            for case in stmt.cases:
                enum_pattern = self._enum_match_pattern(case.pattern, value_type if enum_name is not None else None)
                if enum_pattern is not None:
                    variant, bindings = enum_pattern
                    key = f"{enum_name}.{variant}"
                    if key in seen:
                        self._error(case.keyword, "match case が重複しています", diagnostic_id="SAGA-T108")
                    seen.add(key); covered.add(variant)
                    self.scopes.append(bindings)
                    try:
                        self._predeclare_local_functions(case.body.statements)
                        for child in case.body.statements:
                            self._check_stmt(child)
                    finally:
                        self.scopes.pop()
                    continue
                pattern_type = self._check_expr(case.pattern, value_type)
                self._require(self._is_assignable(value_type, pattern_type) or self._is_assignable(pattern_type, value_type), case.keyword, "match case の型をmatch値と揃えてください", diagnostic_id="SAGA-T103")
                qname = self._qualified_expr_name(case.pattern)
                key = qname or repr(case.pattern)
                if key in seen:
                    self._error(case.keyword, "match case が重複しています", diagnostic_id="SAGA-T108")
                seen.add(key)
                if enum_name and qname and qname.startswith(enum_name + "."):
                    covered.add(qname[len(enum_name)+1:])
                self._check_block(case.body)
            if stmt.default is not None:
                self._check_block(stmt.default)
            elif enum_name:
                missing = sorted(self.enums[enum_name] - covered)
                if missing:
                    self._error(stmt.keyword, f"match が網羅的ではありません。未処理: {', '.join(enum_name + '.' + v for v in missing)}", diagnostic_id="SAGA-T112")
        elif isinstance(stmt, ast.WhileStmt):
            self._require(self._check_expr(stmt.condition) == BOOL, stmt.keyword, "while の条件は bool 型である必要があります", diagnostic_id="SAGA-T104")
            self.loop_depth += 1
            try: self._check_block(stmt.body)
            finally: self.loop_depth -= 1
        elif isinstance(stmt, ast.ForStmt):
            iterable = self._check_expr(stmt.iterable)
            if iterable == RANGE: item_type = INT
            elif iterable.name == "list": item_type = iterable.args[0]
            elif iterable == TEXT: item_type = TEXT
            elif iterable.name == "set": item_type = iterable.args[0]
            else: self._error(stmt.keyword, "for で繰り返せるのは範囲、リスト、セット、文字列です")
            self.scopes.append({stmt.name.lexeme: VariableInfo(item_type, False)}); self.loop_depth += 1
            try:
                self._predeclare_local_functions(stmt.body.statements)
                for child in stmt.body.statements: self._check_stmt(child)
            finally: self.loop_depth -= 1; self.scopes.pop()
        elif isinstance(stmt, ast.BreakStmt): self._require(self.loop_depth > 0, stmt.keyword, "break はループの中でのみ使えます")
        elif isinstance(stmt, ast.ContinueStmt): self._require(self.loop_depth > 0, stmt.keyword, "continue はループの中でのみ使えます")
        elif isinstance(stmt, ast.ReturnStmt):
            if self.current_function is not None:
                expected = self.current_return_type or UNIT
                actual = UNIT if stmt.value is None else self._check_expr(stmt.value, expected)
                self._require_assignable(expected, actual, stmt.keyword)
            elif self.closure_returns:
                expected = self.closure_expected_returns[-1]
                actual = UNIT if stmt.value is None else self._check_expr(
                    stmt.value,
                    expected if expected not in {None, ANY} else None,
                )
                if expected not in {None, ANY}:
                    self._require_assignable(expected, actual, stmt.keyword)
                self.closure_returns[-1].append(actual)
            else:
                self._error(stmt.keyword, "return は関数またはクロージャの中でのみ使えます")
        elif isinstance(stmt, ast.ThrowStmt): self._check_expr(stmt.value)
        elif isinstance(stmt, ast.TryStmt):
            self._check_block(stmt.try_block)
            if stmt.catch_block and stmt.catch_name:
                self.scopes.append({stmt.catch_name.lexeme: VariableInfo(ERROR, False)})
                try:
                    self._predeclare_local_functions(stmt.catch_block.statements)
                    for child in stmt.catch_block.statements: self._check_stmt(child)
                finally: self.scopes.pop()
            if stmt.finally_block: self._check_block(stmt.finally_block)
        elif isinstance(stmt, ast.FunctionDecl):
            if len(self.scopes) > 1:
                info = self.local_function_infos.get(id(stmt))
                if info is None:
                    info = self._declare_local_function(stmt)
                self._check_function(stmt, info)
            else:
                self._check_function(stmt, self.functions[stmt.name.lexeme])
        elif isinstance(stmt, ast.ClassDecl): self._check_class(stmt)
        else: raise AssertionError(f"unknown statement: {stmt!r}")

    def _check_annotations(self, annotations: list[ast.Annotation]) -> None:
        seen: set[str] = set()
        for annotation in annotations:
            if annotation.name.lexeme in seen:
                self._error(annotation.name, f"アノテーション '@{annotation.name.lexeme}' が重複しています", diagnostic_id="SAGA-T108")
            seen.add(annotation.name.lexeme)
            for arg in annotation.arguments:
                self._require_annotation_literal(arg, annotation.name)

    def _check_var(self, stmt: ast.VarDecl) -> None:
        self._check_annotations(stmt.annotations)
        scope = self.scopes[-1]; name = stmt.name.lexeme
        if name in scope: self._error(stmt.name, f"変数 '{name}' はこの範囲ですでに宣言されています")
        if name in self.functions or name in self.classes or name in BUILTINS:
            self._error(stmt.name, f"'{name}' は別の名前として使われているため変数名にできません")
        declared = None
        if stmt.type_name:
            try: declared = parse_type(stmt.type_name, self.active_type_vars[-1])
            except ValueError as exc: self._error(stmt.name, str(exc))
            self._validate_type_reference(declared, stmt.name)
        actual = self._check_expr(stmt.initializer, declared)
        final_type = declared or actual; self._require_assignable(final_type, actual, stmt.name)
        scope[name] = VariableInfo(final_type, stmt.mutable)

    def _check_assign(self, stmt: ast.Assign) -> None:
        if isinstance(stmt.target, ast.Variable):
            info = self._find_var(stmt.target.name.lexeme)
            if info is None:
                name = stmt.target.name.lexeme
                # Bare first assignment is semantically the inferred form of
                # ``let``.  Keep its shadowing rules identical to _check_var;
                # standard module names only become local bindings after an
                # explicit ``use`` statement and may otherwise be shadowed.
                if name in self.functions or name in self.classes or name in BUILTINS:
                    self._error(stmt.target.name, f"'{name}' は別の名前として使われているため変数名にできません")
                actual = self._check_expr(stmt.value)
                # A bare first assignment is a natural spelling of an immutable
                # local binding. Mutation remains explicit through ``var``.
                self.scopes[-1][name] = VariableInfo(actual, False)
                return
            if not info.mutable: self._error(stmt.target.name, f"'{stmt.target.name.lexeme}' は let なので変更できません", "変更が必要なら let を var に変えてください", "SAGA-T101")
            actual = self._check_expr(stmt.value, info.type); self._require_assignable(info.type, actual, stmt.equals); info.moved = False; return
        if isinstance(stmt.target, ast.Member):
            target_type = self._check_expr(stmt.target.target)
            field = self._resolve_field(target_type, stmt.target.name)
            if not field.mutable: self._error(stmt.target.name, f"フィールド '{stmt.target.name.lexeme}' は let なので変更できません", diagnostic_id="SAGA-T101")
            self._check_private(field, stmt.target.name)
            actual = self._check_expr(stmt.value, field.type); self._require_assignable(field.type, actual, stmt.equals); return
        self._error(stmt.equals, "代入先が正しくありません")

    def _check_class(self, stmt: ast.ClassDecl) -> None:
        info = self.classes[stmt.name.lexeme]; self._check_annotations(stmt.annotations)
        previous_class = self.current_class; self.current_class = info.name
        try:
            for method_name, method_info in info.methods.items():
                if method_info.owner == info.name and method_info.declaration is not None:
                    self._check_function(method_info.declaration, method_info)
        finally: self.current_class = previous_class

    def _check_function(self, stmt: ast.FunctionDecl, info: FunctionInfo) -> None:
        self._check_annotations(stmt.annotations)
        for violation in validate_control_tick(stmt):
            self._error(violation.token, violation.message, violation.hint, diagnostic_id=violation.code)
        if stmt.abstract: return
        saved_return, saved_function = self.current_return_type, self.current_function
        saved_loop_depth = self.loop_depth
        visible_type_vars = set(self.active_type_vars[-1])
        visible_type_vars.update(info.type_params)
        if info.owner:
            visible_type_vars.update(self.classes[info.owner].type_params)
        self.active_type_vars.append(visible_type_vars)
        # A function is a control-flow boundary. break/continue in a nested
        # function must never target a loop in the enclosing lexical scope.
        self.loop_depth = 0
        self.scopes.append({})
        try:
            if info.owner:
                owner_type = Type(f"object:{info.owner}", tuple(Type("typevar", (Type(n),)) for n in self.classes[info.owner].type_params))
                self.scopes[-1]["self"] = VariableInfo(owner_type, False)
            for param, param_type in zip(stmt.parameters, info.params):
                if param.name.lexeme in self.scopes[-1]: self._error(param.name, f"引数 '{param.name.lexeme}' が重複しています")
                self.scopes[-1][param.name.lexeme] = VariableInfo(param_type, False)
            if info.return_type is None:
                assert stmt.expression_body is not None
                previous_inference = self.resolving_inference
                self.resolving_inference = True
                try:
                    info.return_type = self._check_expr(stmt.expression_body)
                except PendingReturnType:
                    self._error(
                        stmt.name,
                        f"関数 '{stmt.name.lexeme}' の戻り値型を推論できません",
                        "再帰または相互参照する関数には -> int など戻り値型を明示してください",
                    )
                finally:
                    self.resolving_inference = previous_inference
            self.current_return_type, self.current_function = info.return_type, stmt
            if stmt.expression_body is not None:
                actual = self._check_expr(stmt.expression_body, info.return_type); self._require_assignable(info.return_type, actual, stmt.name)
            else:
                assert stmt.body is not None
                self._predeclare_local_functions(stmt.body.statements)
                for child in stmt.body.statements: self._check_stmt(child)
                if info.return_type != UNIT and not self._guarantees_return(stmt.body):
                    self._error(stmt.name, f"関数 '{stmt.name.lexeme}' はすべての経路で {info.return_type} を返す必要があります", diagnostic_id="SAGA-T109")
        finally:
            self.scopes.pop()
            self.active_type_vars.pop()
            self.current_return_type, self.current_function = saved_return, saved_function
            self.loop_depth = saved_loop_depth

    def _guarantees_return(self, block: ast.Block) -> bool:
        for stmt in block.statements:
            if isinstance(stmt, (ast.ReturnStmt, ast.ThrowStmt)):
                return True
            if isinstance(stmt, ast.Block) and self._guarantees_return(stmt):
                return True
            if (
                isinstance(stmt, ast.IfStmt)
                and stmt.else_branch
                and self._guarantees_return(stmt.then_branch)
                and self._guarantees_return(stmt.else_branch)
            ):
                return True
            if isinstance(stmt, ast.TryStmt):
                # A returning/throwing finally dominates every prior path.  If
                # finally falls through, a try/finally without catch preserves
                # the try block's guaranteed return.  With catch, both normal
                # exceptional branches must return.
                if stmt.finally_block and self._guarantees_return(stmt.finally_block):
                    return True
                if stmt.catch_block:
                    if self._guarantees_return(stmt.try_block) and self._guarantees_return(stmt.catch_block):
                        return True
                elif self._guarantees_return(stmt.try_block):
                    return True
        return False

    def _declare_local_function(self, stmt: ast.FunctionDecl) -> FunctionInfo:
        self._check_annotations(stmt.annotations)
        if stmt.abstract or stmt.override:
            self._error(stmt.name, "ネスト関数に abstract / override は使えません")
        scope = self.scopes[-1]
        name = stmt.name.lexeme
        if name in scope:
            self._error(stmt.name, f"名前 '{name}' はこのスコープですでに宣言されています", diagnostic_id="SAGA-T108")
        type_vars = set(self.active_type_vars[-1])
        type_vars.update(stmt.type_params)
        try:
            params = [parse_type(param.type_name, type_vars) for param in stmt.parameters]
            if stmt.return_type:
                ret = parse_type(stmt.return_type, type_vars)
            elif stmt.body is not None:
                ret = UNIT
            else:
                self._error(stmt.name, "ネストした式関数には戻り値型を明示してください", "例: fn add(x: int) -> int = x + 1")
        except ValueError as exc:
            self._error(stmt.name, str(exc))
        info = FunctionInfo(params, ret, stmt, list(stmt.type_params), None, False)
        self._validate_function_types(info, stmt.name)
        self.local_function_infos[id(stmt)] = info
        scope[name] = VariableInfo(info.function_type(), False)
        return info

    def _predeclare_local_functions(self, statements: list[ast.Stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, ast.FunctionDecl) and id(stmt) not in self.local_function_infos:
                self._declare_local_function(stmt)

    def _check_block(self, block: ast.Block) -> None:
        self.scopes.append({})
        try:
            self._predeclare_local_functions(block.statements)
            for stmt in block.statements: self._check_stmt(stmt)
        finally: self.scopes.pop()

    def _check_expr(self, expr: ast.Expr, expected: Type | None = None) -> Type:
        if isinstance(expr, ast.Literal):
            if isinstance(expr.value, bool): return BOOL
            if isinstance(expr.value, int): return INT
            from decimal import Decimal
            if isinstance(expr.value, Decimal): return DECIMAL
            if isinstance(expr.value, str): return TEXT
            raise AssertionError("unsupported literal")
        if isinstance(expr, ast.Variable):
            name = expr.name.lexeme
            info = self._find_var(name)
            if info:
                if info.moved:
                    self._error(expr.name, f"move 済みの資源 '{name}' は使用できません", diagnostic_id="SAGA-T180")
                return info.type
            if name in self.functions:
                function = self.functions[name]
                if self.resolving_inference and function.return_type is None:
                    raise PendingReturnType()
                return function.function_type()
            if name in self.classes:
                cls = self.classes[name]
                params = [field.type for field in cls.fields.values()]
                result = Type(f"object:{name}", tuple(Type("typevar", (Type(n),)) for n in cls.type_params))
                return FUNCTION(params, result)
            if name in BUILTINS: return Type("builtin")
            candidate = self._closest(name, self._name_candidates())
            self._error(expr.name, f"名前 '{name}' は宣言されていません", f"candidate:{candidate}" if candidate else None, "SAGA-T102")
        if isinstance(expr, ast.ListLiteral):
            if not expr.elements:
                if expected and expected.name == "list": return expected
                self._error(expr.token, "空のリストは型を推測できません", "例: let items: list[int] = []")
            expected_element = expected.args[0] if expected and expected.name == "list" else None
            element_types = [self._check_expr(element, expected_element) for element in expr.elements]
            if expected_element:
                for typ in element_types: self._require_assignable(expected_element, typ, expr.token)
                return expected
            if all(is_numeric(t) for t in element_types):
                element_type = element_types[0]
                for current in element_types[1:]: element_type = common_numeric(element_type, current)
                return LIST(element_type)
            first = element_types[0]
            for current in element_types[1:]:
                if current != first: self._error(expr.token, f"リストの要素型を統一してください: {first} と {current} が混在しています")
            return LIST(first)
        if isinstance(expr, ast.AwaitExpr):
            value_type = self._check_expr(expr.value)
            if value_type.name != "future" or len(value_type.args) != 1:
                self._error(expr.keyword, "await には future[T] が必要です", diagnostic_id="SAGA-T175")
            return value_type.args[0]
        if isinstance(expr, ast.MoveExpr):
            if not isinstance(expr.value, ast.Variable):
                self._error(expr.keyword, "move には名前付き資源が必要です", diagnostic_id="SAGA-T176")
            info = self._find_var(expr.value.name.lexeme)
            if info is None:
                self._error(expr.value.name, f"名前 '{expr.value.name.lexeme}' は宣言されていません", diagnostic_id="SAGA-T102")
            if info.moved:
                self._error(expr.value.name, f"資源 '{expr.value.name.lexeme}' はすでに move 済みです", diagnostic_id="SAGA-T180")
            if not self._is_resource_type(info.type):
                self._error(expr.keyword, "move は move-only 資源にのみ使えます", diagnostic_id="SAGA-T176")
            info.moved = True
            return info.type
        if isinstance(expr, ast.Unary):
            right = self._check_expr(expr.right)
            if expr.operator.kind in {TokenKind.BANG, TokenKind.NOT}:
                self._require(right == BOOL, expr.operator, "not は bool 型にのみ使えます"); return BOOL
            self._require(is_numeric(right), expr.operator, "単項 '-' は数値にのみ使えます"); return right
        if isinstance(expr, ast.Binary): return self._check_binary(expr)
        if isinstance(expr, ast.RangeExpr):
            self._require(self._check_expr(expr.start) == INT and self._check_expr(expr.end) == INT, expr.operator, "範囲の両端は int 型にしてください"); return RANGE
        if isinstance(expr, ast.PropagateExpr): return self._check_propagate(expr)
        if isinstance(expr, ast.ClosureExpr): return self._check_closure(expr, expected)
        if isinstance(expr, ast.Call): return self._check_call(expr, expected)
        if isinstance(expr, ast.Index):
            target = self._check_expr(expr.target); self._require(self._check_expr(expr.index) == INT, expr.bracket, "添字は int 型にしてください")
            if target.name == "list": return target.args[0]
            if target == TEXT: return TEXT
            self._error(expr.bracket, "[] で取り出せるのはリストまたは文字列です")
        if isinstance(expr, ast.Member): return self._check_member(expr, expected)
        raise AssertionError(f"unknown expression: {expr!r}")


    def _check_propagate(self, expr: ast.PropagateExpr) -> Type:
        wrapped = self._check_expr(expr.value)
        target = self.current_return_type
        if target is None and self.closure_expected_returns:
            target = self.closure_expected_returns[-1]
        if wrapped.name == "result" and len(wrapped.args) == 2:
            if target is None or target.name != "result" or len(target.args) != 2:
                self._error(expr.question, "result の ? は result を返す関数または型が明確なクロージャ内で使ってください")
            self._require_assignable(target.args[1], wrapped.args[1], expr.question)
            return wrapped.args[0]
        if wrapped.name == "option" and len(wrapped.args) == 1:
            if target is None or target.name != "option" or len(target.args) != 1:
                self._error(expr.question, "option の ? は option を返す関数または型が明確なクロージャ内で使ってください")
            return wrapped.args[0]
        self._error(expr.question, "? は result または option にのみ使えます")

    def _check_closure(self, expr: ast.ClosureExpr, expected: Type | None = None) -> Type:
        expected_fn = expected if expected is not None and expected.name == "fn" else None
        if expr.parameters:
            if expected_fn and len(expr.parameters) != len(expected_fn.args):
                self._error(expr.brace, f"このクロージャの引数は {len(expected_fn.args)} 個必要です")
            parameter_types = list(expected_fn.args) if expected_fn else [ANY] * len(expr.parameters)
            bindings = {token.lexeme: VariableInfo(parameter_types[index], False) for index, token in enumerate(expr.parameters)}
        else:
            if expected_fn and len(expected_fn.args) > 1:
                self._error(expr.brace, "引数が2個以上のクロージャでは { left, right -> ... } のように名前を書いてください")
            # Omitted parameters are context-sensitive. Without an expected
            # callable type the closure is zero-argument; a one-argument API
            # such as map/filter supplies the implicit `it` context.
            parameter_types = list(expected_fn.args) if expected_fn else []
            bindings = {}
            if parameter_types:
                bindings["it"] = VariableInfo(parameter_types[0], False)

        self.scopes.append(bindings)
        expected_result = expected_fn.result if expected_fn else None
        self.closure_returns.append([])
        self.closure_expected_returns.append(expected_result)
        saved_return, saved_function = self.current_return_type, self.current_function
        saved_loop_depth = self.loop_depth
        # Closures are callable boundaries just like named functions. Their
        # return statements belong to the closure and loop control cannot jump
        # into an enclosing caller's loop.
        self.current_return_type = None
        self.current_function = None
        self.loop_depth = 0
        try:
            self._predeclare_local_functions(expr.body.statements)
            result = UNIT
            for index, stmt in enumerate(expr.body.statements):
                last = index == len(expr.body.statements) - 1
                if last and isinstance(stmt, ast.ExpressionStmt):
                    contextual_result = expected_result if expected_result not in {None, ANY} else None
                    result = self._check_expr(stmt.expression, contextual_result)
                else:
                    self._check_stmt(stmt)
            returns = self.closure_returns[-1]

            if returns:
                inferred = returns[0]
                for current in returns[1:]:
                    if is_numeric(inferred) and is_numeric(current):
                        inferred = common_numeric(inferred, current)
                    elif self._is_assignable(inferred, current):
                        pass
                    elif self._is_assignable(current, inferred):
                        inferred = current
                    else:
                        self._error(expr.brace, f"クロージャの return 型を揃えてください: {inferred} と {current}")

                if result != UNIT:
                    if is_numeric(inferred) and is_numeric(result):
                        inferred = common_numeric(inferred, result)
                    elif self._is_assignable(inferred, result):
                        pass
                    elif self._is_assignable(result, inferred):
                        inferred = result
                    else:
                        self._error(expr.brace, f"クロージャの戻り値型を揃えてください: {inferred} と {result}")
                    result = inferred
                elif self._guarantees_return(expr.body):
                    result = inferred
                elif inferred != UNIT:
                    self._error(expr.brace, "値を返す経路と値を返さない経路が混在しています")

            if expected_result not in {None, ANY}:
                self._require_assignable(expected_result, result, expr.brace)
            return FUNCTION(parameter_types, result)
        finally:
            self.current_return_type, self.current_function = saved_return, saved_function
            self.loop_depth = saved_loop_depth
            self.closure_expected_returns.pop()
            self.closure_returns.pop()
            self.scopes.pop()

    @staticmethod
    def _qualified_expr_name(expr: ast.Expr) -> str | None:
        if isinstance(expr, ast.Variable):
            return expr.name.lexeme
        if isinstance(expr, ast.Member):
            base = TypeChecker._qualified_expr_name(expr.target)
            return f"{base}.{expr.name.lexeme}" if base else None
        return None

    def _check_member(self, expr: ast.Member, expected: Type | None = None) -> Type:
        target = self._check_expr(expr.target)
        if target.name.startswith("enumtype:"):
            enum_name = target.name.split(":", 1)[1]
            variants = self.enums.get(enum_name)
            if variants is None or expr.name.lexeme not in variants:
                self._error(expr.name, f"enum variant '{enum_name}.{expr.name.lexeme}' が見つかりません", diagnostic_id="SAGA-T106")
            payload = self.enum_payloads.get(enum_name, {}).get(expr.name.lexeme, ())
            params = self.enum_type_params.get(enum_name, [])
            if enum_name == "Option":
                result = OPTION(TYPEVAR("T"))
            elif enum_name == "Result":
                result = RESULT(TYPEVAR("T"), TYPEVAR("E"))
            else:
                result = Type(f"object:{enum_name}", tuple(TYPEVAR(name) for name in params))
            if payload:
                return FUNCTION(list(payload), result)
            if not params:
                return result
            if expected is not None and expected.name == result.name and len(expected.args) == len(params):
                return expected
            self._error(
                expr.name,
                f"generic enum variant '{enum_name}.{expr.name.lexeme}' の型引数を推論できません",
                f"例: let value: {enum_name}[int] = {enum_name}.{expr.name.lexeme}",
                "SAGA-T113",
            )
        if target.name.startswith("srcmodule:"):
            bind = target.name.split(":", 1)[1]
            module = self.source_modules.get(bind)
            if module is None:
                self._error(expr.name, f"module '{bind}' が見つかりません", diagnostic_id="SAGA-T106")
            if expr.name.lexeme not in module.members:
                candidate = self._closest(expr.name.lexeme, set(module.members))
                self._error(expr.name, f"module member '{bind}.{expr.name.lexeme}' はpublicではないか存在しません", f"candidate:{candidate}" if candidate else None, "SAGA-T106")
            return module.members[expr.name.lexeme]
        if target.name.startswith("module:"):
            module_name = target.name.split(":", 1)[1]; module = MODULES[module_name]
            if expr.name.lexeme not in module.functions:
                candidate = self._closest(expr.name.lexeme, set(module.functions))
                self._error(expr.name, f"モジュール '{module_name}' に '{expr.name.lexeme}' はありません", f"candidate:{candidate}" if candidate else None, "SAGA-T106")
            sig = module.functions[expr.name.lexeme].signature
            return FUNCTION(list(sig.params), sig.returns)
        if target == ERROR:
            if expr.name.lexeme in {"message", "kind"}: return TEXT
            self._error(expr.name, "error で使えるのは message と kind です")
        if target == ANY: return ANY
        if target.name.startswith("object:"):
            class_name = target.name.split(":", 1)[1]; info = self.classes.get(class_name)
            if not info: self._error(expr.name, f"クラス '{class_name}' が見つかりません")
            mapping = {name: arg for name, arg in zip(info.type_params, target.args)}
            if expr.name.lexeme in info.fields:
                field = info.fields[expr.name.lexeme]; self._check_private(field, expr.name); return substitute(field.type, mapping)
            if expr.name.lexeme in info.methods:
                method = info.methods[expr.name.lexeme]
                return FUNCTION([substitute(p, mapping) for p in method.params], substitute(method.return_type or ANY, mapping))
            candidates = set(info.fields) | set(info.methods)
            candidate = self._closest(expr.name.lexeme, candidates)
            self._error(expr.name, f"'{class_name}' に '{expr.name.lexeme}' はありません", f"candidate:{candidate}" if candidate else None, "SAGA-T106")
        self._error(expr.name, f"{target} 型にはメンバーアクセスできません")

    @staticmethod
    def _is_resource_type(value: Type) -> bool:
        return value.name in {
            "db_connection", "socket", "task_pool", "native:task_pool", "window", "gamepad", "renderer", "renderer2d",
            "shader", "audio_device", "native_resource", "native:machine_i2c", "native:machine_spi",
            "native:machine_uart", "native:machine_can", "native:machine_pwm", "native:machine_servo",
            "native:machine_motor", "native:machine_modbus_rtu", "native:machine_modbus_tcp", "native:machine_ethercat",
        }

    def _resolve_field(self, target: Type, token: Token) -> FieldInfo:
        if not target.name.startswith("object:"): self._error(token, "フィールド代入できるのはクラスのオブジェクトだけです")
        class_name = target.name.split(":", 1)[1]; info = self.classes[class_name]
        if token.lexeme not in info.fields:
            candidate = self._closest(token.lexeme, set(info.fields))
            self._error(token, f"フィールド '{token.lexeme}' がありません", f"candidate:{candidate}" if candidate else None)
        field = info.fields[token.lexeme]
        mapping = {name: arg for name, arg in zip(info.type_params, target.args)}
        return FieldInfo(substitute(field.type, mapping), field.mutable, field.private, field.owner)

    def _check_private(self, field: FieldInfo, token: Token) -> None:
        if field.private and self.current_class != field.owner: self._error(token, f"private フィールド '{token.lexeme}' にはクラス外からアクセスできません", diagnostic_id="SAGA-T107")

    def _check_binary(self, expr: ast.Binary) -> Type:
        left, right, kind = self._check_expr(expr.left), self._check_expr(expr.right), expr.operator.kind
        if kind in {TokenKind.PLUS, TokenKind.MINUS, TokenKind.STAR, TokenKind.SLASH, TokenKind.PERCENT, TokenKind.POWER}:
            if kind is TokenKind.PLUS and left == TEXT and right == TEXT: return TEXT
            self._require(is_numeric(left) and is_numeric(right), expr.operator, "算術演算は数値同士に使います", "文字列化は text(value) を使うか、print に複数の値を渡してください")
            if kind is TokenKind.PERCENT:
                self._require(left == INT and right == INT, expr.operator, "% は int 同士にのみ使えます"); return INT
            if kind is TokenKind.POWER:
                self._require(is_numeric(right), expr.operator, "指数は整数値として表せる数値型にしてください")
                return DECIMAL if left == DECIMAL else RATIONAL
            if kind is TokenKind.SLASH: return DECIMAL if DECIMAL in {left, right} else RATIONAL
            return common_numeric(left, right)
        if kind in {TokenKind.LESS, TokenKind.LESS_EQUAL, TokenKind.GREATER, TokenKind.GREATER_EQUAL}:
            self._require((is_numeric(left) and is_numeric(right)) or (left == TEXT and right == TEXT), expr.operator, "大小比較は同種の数値または文字列に使います"); return BOOL
        if kind in {TokenKind.EQUAL_EQUAL, TokenKind.BANG_EQUAL}:
            self._require((is_numeric(left) and is_numeric(right)) or self._is_assignable(left, right) or self._is_assignable(right, left), expr.operator, f"比較する型を揃えてください: {left} と {right}"); return BOOL
        if kind in {TokenKind.AND, TokenKind.OR}:
            self._require(left == BOOL and right == BOOL, expr.operator, "and / or は bool 同士に使います"); return BOOL
        raise AssertionError("unknown binary operator")

    def _check_call(self, expr: ast.Call, expected: Type | None = None) -> Type:
        if isinstance(expr.callee, ast.Member):
            target_type = self._check_expr(expr.callee.target)
            extension = self._check_extension_call(target_type, expr.callee.name.lexeme, expr.arguments, expr.paren)
            if extension is not None:
                return extension
        if isinstance(expr.callee, ast.Variable) and expr.callee.name.lexeme in BUILTINS:
            return self._check_builtin(expr.callee.name.lexeme, expr.arguments, expr.paren, expected)
        if isinstance(expr.callee, ast.Variable) and expr.callee.name.lexeme in self.classes:
            class_info = self.classes[expr.callee.name.lexeme]
            if class_info.interface or class_info.abstract:
                self._error(expr.callee.name, f"'{class_info.name}' は直接作成できません", diagnostic_id="SAGA-T111")
        callee_type = self._check_expr(expr.callee)
        if callee_type == ANY:
            for arg in expr.arguments: self._check_expr(arg)
            return ANY
        if callee_type.name != "fn": self._error(expr.paren, f"{callee_type} 型は呼び出せません", diagnostic_id="SAGA-T105")
        arg_types = [
            self._check_expr(arg, callee_type.args[index] if index < len(callee_type.args) else None)
            for index, arg in enumerate(expr.arguments)
        ]
        # Native variadic functions are recognized from their member expression.
        variadic = False; min_args = None; native_contract = False
        if isinstance(expr.callee, ast.Member):
            target_type = self._check_expr(expr.callee.target)
            if target_type.name.startswith("module:"):
                module = MODULES[target_type.name.split(":", 1)[1]]
                sig = module.functions[expr.callee.name.lexeme].signature
                variadic, min_args = sig.variadic, sig.min_args
                native_contract = True
        if variadic:
            if len(arg_types) < (min_args or len(callee_type.args)): self._error(expr.paren, f"引数は最低 {min_args or len(callee_type.args)} 個必要です", diagnostic_id="SAGA-T105")
        elif len(arg_types) != len(callee_type.args): self._error(expr.paren, f"引数は {len(callee_type.args)} 個必要です", diagnostic_id="SAGA-T105")
        mapping: dict[str, Type] = {}
        for parameter_type, actual in zip(callee_type.args, arg_types):
            matcher = self._unify_native_contract if native_contract else self._unify
            if not matcher(parameter_type, actual, mapping): self._error(expr.paren, f"引数の型が一致しません。必要: {parameter_type}、実際: {actual}", diagnostic_id="SAGA-T105")
        enum_constructor = None
        if isinstance(expr.callee, ast.Member):
            target_type = self._check_expr(expr.callee.target)
            if target_type.name.startswith("enumtype:"):
                enum_constructor = target_type.name.split(":", 1)[1]
        raw_result = callee_type.result or ANY
        if enum_constructor is not None and expected is not None and expected.name == raw_result.name:
            self._unify(raw_result, expected, mapping)
        resolved = substitute(raw_result, mapping)
        if enum_constructor is not None and self._contains_typevar(resolved):
            self._error(
                expr.paren,
                f"generic enum constructor '{enum_constructor}.{expr.callee.name.lexeme}' の型引数を完全に推論できません",
                f"変数または戻り値に {enum_constructor}[...] の型注釈を追加してください",
                "SAGA-T113",
            )
        return resolved

    @staticmethod
    def _contains_typevar(value: Type) -> bool:
        if is_typevar(value):
            return True
        if any(TypeChecker._contains_typevar(arg) for arg in value.args):
            return True
        return value.result is not None and TypeChecker._contains_typevar(value.result)

    def _check_extension_call(self, target: Type, name: str, args: list[ast.Expr], token: Token) -> Type | None:
        if target.name == "list":
            item = target.args[0]
            if name == "map":
                self._arity_expr(name, args, 1, token)
                fn = self._check_callback(args[0], [item], ANY, token, "map")
                return LIST(fn.result or ANY)
            if name == "filter":
                self._arity_expr(name, args, 1, token)
                self._check_callback(args[0], [item], BOOL, token, "filter"); return target
            if name == "each":
                self._arity_expr(name, args, 1, token)
                self._check_callback(args[0], [item], ANY, token, "each"); return UNIT
            if name in {"reduce", "fold"}:
                self._arity_expr(name, args, 2, token)
                initial = self._check_expr(args[0])
                self._check_callback(args[1], [initial, item], initial, token, name); return initial
            if name == "find":
                self._arity_expr(name, args, 1, token)
                self._check_callback(args[0], [item], BOOL, token, "find"); return OPTION(item)
            if name in {"any", "all", "none"}:
                self._arity_expr(name, args, 1, token)
                self._check_callback(args[0], [item], BOOL, token, name); return BOOL
            if name in {"sorted", "distinct"}:
                self._arity_expr(name, args, 0, token); return target
            if name == "sortedBy":
                self._arity_expr(name, args, 1, token)
                self._check_callback(args[0], [item], ANY, token, "sortedBy"); return target
            if name in {"take", "skip"}:
                self._arity_expr(name, args, 1, token)
                self._require(self._check_expr(args[0]) == INT, token, f"{name} の個数は int にしてください"); return target
            if name == "zip":
                self._arity_expr(name, args, 1, token); other = self._check_expr(args[0])
                self._require(other.name == "list", token, "zip にはリストを渡してください")
                # Tuple syntax is intentionally not invented here; the runtime
                # pair is exposed conservatively until the tuple core is unified.
                return LIST(ANY)
            if name == "flatten":
                self._arity_expr(name, args, 0, token)
                self._require(item.name == "list", token, "flatten はリストのリストに使います")
                return LIST(item.args[0])
            if name == "flatMap":
                self._arity_expr(name, args, 1, token)
                fn = self._check_callback(args[0], [item], ANY, token, "flatMap")
                result = fn.result or ANY
                self._require(result.name == "list" or result == ANY, token, "flatMap のブロックはリストを返してください")
                return result if result.name == "list" else LIST(ANY)
            if name in {"chunk", "window"}:
                self._arity_expr(name, args, 1, token)
                self._require(self._check_expr(args[0]) == INT, token, f"{name} のサイズは int にしてください")
                return LIST(target)
            if name == "group":
                self._arity_expr(name, args, 0, token)
                self._require(self._is_hashable(item), token, "group の要素はハッシュ可能である必要があります")
                return MAP(item, target)
            if name == "groupBy":
                self._arity_expr(name, args, 1, token)
                fn = self._check_callback(args[0], [item], ANY, token, "groupBy"); key = fn.result or ANY
                self._require(key == ANY or self._is_hashable(key), token, "groupBy のキーはハッシュ可能である必要があります")
                return MAP(key, target)
            if name == "sum":
                self._arity_expr(name, args, 0, token)
                self._require(is_numeric(item), token, "sum は数値リストに使います"); return item
            if name == "contains":
                self._arity_expr(name, args, 1, token); actual = self._check_expr(args[0], item)
                self._require_assignable(item, actual, token); return BOOL
            return None

        if target == TEXT:
            if name in {"trim", "upper", "lower"}:
                self._arity_expr(name, args, 0, token); return TEXT
            if name == "split":
                self._arity_expr(name, args, 1, token); self._require(self._check_expr(args[0]) == TEXT, token, "split の区切りは text です"); return LIST(TEXT)
            if name in {"startsWith", "endsWith", "contains"}:
                self._arity_expr(name, args, 1, token); self._require(self._check_expr(args[0]) == TEXT, token, f"{name} には text を渡してください"); return BOOL
            if name == "length": self._arity_expr(name, args, 0, token); return INT
            return None

        if target.name == "map":
            key, value = target.args
            if name == "keys": self._arity_expr(name, args, 0, token); return LIST(key)
            if name == "values": self._arity_expr(name, args, 0, token); return LIST(value)
            if name == "containsKey":
                self._arity_expr(name, args, 1, token); actual = self._check_expr(args[0], key); self._require_assignable(key, actual, token); return BOOL
            if name == "get":
                if len(args) not in {1, 2}: self._error(token, "map.get の引数は1個または2個必要です")
                actual = self._check_expr(args[0], key); self._require_assignable(key, actual, token)
                if len(args) == 2:
                    fallback = self._check_expr(args[1], value); self._require_assignable(value, fallback, token); return value
                return OPTION(value)
            return None

        if target.name == "set":
            item = target.args[0]
            if name == "contains":
                self._arity_expr(name, args, 1, token); actual = self._check_expr(args[0], item); self._require_assignable(item, actual, token); return BOOL
            if name == "toList": self._arity_expr(name, args, 0, token); return LIST(item)
            return None
        return None

    def _arity_expr(self, name: str, args: list[ast.Expr], count: int, token: Token) -> None:
        if len(args) != count:
            self._error(token, f"{name} の引数は {count} 個必要です", diagnostic_id="SAGA-T105")

    def _check_callback(self, expr: ast.Expr, params: list[Type], result: Type, token: Token, label: str) -> Type:
        fn = self._check_expr(expr, FUNCTION(params, result))
        self._require(fn.name == "fn" or fn == ANY, token, f"{label} にはクロージャまたは関数を渡してください")
        if fn == ANY:
            return fn
        self._require(len(fn.args) == len(params), token, f"{label} のコールバック引数は {len(params)} 個必要です")
        for accepted, supplied in zip(fn.args, params):
            # A callback parameter must accept every value the collection can
            # supply.  This preserves the pre-0.29 static HOF contract.
            self._require_assignable(accepted, supplied, token)
        if result != ANY:
            self._require_assignable(result, fn.result or UNIT, token)
        return fn

    def _check_builtin(self, name: str, args: list[ast.Expr], token: Token, expected: Type | None = None) -> Type:
        if name == "repeat" and len(args) == 2 and isinstance(args[1], ast.ClosureExpr):
            count = self._check_expr(args[0])
            self._require(count == INT, token, "repeat(count) { ... } の count は int です")
            self._check_expr(args[1], FUNCTION([], ANY))
            return UNIT
        if name == "transform":
            self._arity_expr(name, args, 2, token)
            values = self._check_expr(args[1])
            self._require(values.name == "list", token, "transform(function, list) と書きます")
            fn = self._check_callback(args[0], [values.args[0]], ANY, token, "transform")
            return LIST(fn.result or ANY)
        if name == "filter":
            self._arity_expr(name, args, 2, token)
            values = self._check_expr(args[1])
            self._require(values.name == "list", token, "filter(function, list) と書きます")
            self._check_callback(args[0], [values.args[0]], BOOL, token, "filter"); return values
        if name == "reduce":
            self._arity_expr(name, args, 3, token)
            values = self._check_expr(args[1]); self._require(values.name == "list", token, "reduce(function, list, initial) と書きます")
            initial = self._check_expr(args[2])
            self._check_callback(args[0], [initial, values.args[0]], initial, token, "reduce"); return initial
        if name == "find":
            self._arity_expr(name, args, 3, token)
            values = self._check_expr(args[1]); self._require(values.name == "list", token, "find(function, list, fallback) と書きます")
            fallback = self._check_expr(args[2], values.args[0]); self._require_assignable(values.args[0], fallback, token)
            self._check_callback(args[0], [values.args[0]], BOOL, token, "find"); return values.args[0]
        if name in {"any", "all"}:
            self._arity_expr(name, args, 2, token)
            values = self._check_expr(args[1]); self._require(values.name == "list", token, f"{name}(function, list) と書きます")
            self._check_callback(args[0], [values.args[0]], BOOL, token, name); return BOOL
        if name == "ok":
            if len(args) != 1: self._error(token, "ok の引数は 1 個必要です")
            if expected and expected.name == "result":
                actual = self._check_expr(args[0], expected.args[0])
                self._require_assignable(expected.args[0], actual, token)
                return expected
            return RESULT(self._check_expr(args[0]), ANY)
        if name == "err":
            if len(args) != 1: self._error(token, "err の引数は 1 個必要です")
            if expected and expected.name == "result":
                actual = self._check_expr(args[0], expected.args[1])
                self._require_assignable(expected.args[1], actual, token)
                return expected
            return RESULT(ANY, self._check_expr(args[0]))
        if name in {"is_ok", "is_err"}:
            if len(args) != 1: self._error(token, f"{name} の引数は 1 個必要です")
            typ = self._check_expr(args[0]); self._require(typ.name == "result" or typ == ANY, token, f"{name} は result に使います"); return BOOL
        if name in {"unwrap_ok", "unwrap_err"}:
            if len(args) != 1: self._error(token, f"{name} の引数は 1 個必要です")
            typ = self._check_expr(args[0]); self._require(typ.name == "result", token, f"{name} は result に使います"); return typ.args[0 if name=="unwrap_ok" else 1]
        if name == "unwrap_result_or":
            if len(args) != 2: self._error(token, "unwrap_result_or の引数は 2 個必要です")
            typ = self._check_expr(args[0]); self._require(typ.name == "result", token, "unwrap_result_or の1つ目は result です")
            fb = self._check_expr(args[1], typ.args[0]); self._require_assignable(typ.args[0], fb, token); return typ.args[0]
        if name == "some":
            if len(args) != 1: self._error(token, "some の引数は 1 個必要です")
            if expected and expected.name == "option":
                actual = self._check_expr(args[0], expected.args[0])
                self._require_assignable(expected.args[0], actual, token)
                return expected
            return OPTION(self._check_expr(args[0]))
        if name == "none":
            if args: self._error(token, "none の引数は 0 個です")
            return expected if expected and expected.name == "option" else OPTION(ANY)
        if name in {"is_some", "is_none"}:
            if len(args) != 1: self._error(token, f"{name} の引数は 1 個必要です")
            typ = self._check_expr(args[0])
            self._require(typ.name == "option" or typ == ANY, token, f"{name} は option に使います")
            return BOOL
        if name == "unwrap":
            if len(args) != 1: self._error(token, "unwrap の引数は 1 個必要です")
            typ = self._check_expr(args[0])
            self._require(typ.name == "option", token, "unwrap は option に使います")
            return typ.args[0]
        if name == "unwrap_or":
            if len(args) != 2: self._error(token, "unwrap_or の引数は 2 個必要です")
            typ = self._check_expr(args[0])
            self._require(typ.name == "option", token, "unwrap_or の1つ目は option です")
            fallback = self._check_expr(args[1], typ.args[0])
            self._require_assignable(typ.args[0], fallback, token)
            return typ.args[0]
        if name == "map_get":
            if len(args) != 3: self._error(token, "map_get の引数は 3 個必要です")
            map_type = self._check_expr(args[0])
            key_type = self._check_expr(args[1])
            if map_type == ANY:
                fallback_type = self._check_expr(args[2], ANY) if not isinstance(args[2], ast.ListLiteral) or args[2].elements else LIST(ANY)
                return fallback_type
            self._require(map_type.name == "map", token, "map_get の1つ目はmapです")
            self._require_assignable(map_type.args[0], key_type, token)
            fallback_type = self._check_expr(args[2], map_type.args[1])
            self._require_assignable(map_type.args[1], fallback_type, token)
            return map_type.args[1]
        types = [self._check_expr(arg) for arg in args]
        if name == "print": return UNIT
        if name == "len":
            self._arity(name, types, 1, token); self._require(types[0] == TEXT or types[0] == BYTES or types[0].name in {"list", "map", "set"}, token, "len は text、bytes、list、map、set に使います"); return INT
        if name == "text": self._arity(name, types, 1, token); return TEXT
        if name == "int":
            self._arity(name, types, 1, token)
            self._require(types[0] in {INT, DECIMAL, RATIONAL, TEXT}, token, "int は数値または整数形式のtextに使います")
            return INT
        if name == "decimal": self._arity(name, types, 1, token); self._require(is_numeric(types[0]), token, "decimal は数値に使います"); return DECIMAL
        if name == "ratio": self._arity(name, types, 2, token); self._require(types == [INT, INT], token, "ratio は int を2つ受け取ります"); return RATIONAL
        if name == "abs": self._arity(name, types, 1, token); self._require(is_numeric(types[0]), token, "abs は数値に使います"); return types[0]
        if name == "sqrt": self._arity(name, types, 1, token); self._require(is_numeric(types[0]), token, "sqrt は数値に使います"); return DECIMAL
        if name == "round": self._arity(name, types, 2, token); self._require(is_numeric(types[0]) and types[1] == INT, token, "round(value, digits) と書きます"); return DECIMAL
        if name in {"floor", "ceil"}: self._arity(name, types, 1, token); self._require(is_numeric(types[0]), token, f"{name} は数値に使います"); return INT
        if name in {"min", "max"}: self._arity(name, types, 2, token); self._require(all(is_numeric(t) for t in types), token, f"{name} は数値を2つ受け取ります"); return common_numeric(types[0], types[1])
        if name in {"sum", "mean"}:
            self._arity(name, types, 1, token); self._require(types[0].name == "list" and is_numeric(types[0].args[0]), token, f"{name} は数値リストに使います")
            return DECIMAL if name == "mean" and types[0].args[0] == DECIMAL else (RATIONAL if name == "mean" else types[0].args[0])
        if name == "repeat":
            self._arity(name, types, 2, token); self._require(types[1] == INT, token, "repeat(value, count) のcountはintです"); return LIST(types[0])
        if name == "set_at":
            self._arity(name, types, 3, token); self._require(types[0].name == "list" and types[1] == INT, token, "set_at(list, index, value) と書きます")
            self._require_assignable(types[0].args[0], types[2], token); return types[0]
        if name in {"append", "prepend"}:
            self._arity(name, types, 2, token); self._require(types[0].name == "list", token, f"{name} の1つ目はリストです"); self._require_assignable(types[0].args[0], types[1], token); return types[0]
        if name == "get":
            self._arity(name, types, 3, token); self._require(types[0].name == "list" and types[1] == INT, token, "get(list, index, fallback) と書きます"); self._require_assignable(types[0].args[0], types[2], token); return types[0].args[0]
        if name == "contains":
            self._arity(name, types, 2, token)
            if types[0].name in {"list", "set"}: self._require_assignable(types[0].args[0], types[1], token)
            elif types[0] == TEXT: self._require(types[1] == TEXT, token, "文字列検索にはtextを指定してください")
            else: self._error(token, "contains は list、set、text に使います")
            return BOOL
        if name == "assert":
            if len(types) not in {1,2}: self._error(token, "assert は1個または2個の引数を受け取ります")
            self._require(types[0] == BOOL, token, "assert の1つ目は bool にしてください")
            if len(types)==2: self._require(types[1] == TEXT, token, "assert のメッセージは text にしてください")
            return UNIT
        if name == "precision": self._arity(name, types, 1, token); self._require(types[0] == INT, token, "precision は int を受け取ります"); return UNIT
        if name in {"slice", "reverse", "sort", "unique"}:
            if name == "slice":
                self._arity(name, types, 3, token); self._require(types[0].name == "list" and types[1:] == [INT, INT], token, "slice(list, start, end) と書きます")
            else:
                self._arity(name, types, 1, token); self._require(types[0].name == "list", token, f"{name} はlistに使います")
                if name == "sort": self._require(is_numeric(types[0].args[0]) or types[0].args[0] == TEXT, token, "sort は数値またはtextのlistに使います")
            return types[0]
        if name in {"transform", "filter"}:
            self._arity(name, types, 2, token)
            function_type, list_type = types
            self._require(list_type.name == "list", token, f"{name} の2つ目はlistです")
            self._require(function_type.name == "fn" or function_type == ANY, token, f"{name} の1つ目は関数です")
            if function_type == ANY:
                return list_type if name == "filter" else LIST(ANY)
            self._require(len(function_type.args) == 1, token, f"{name} の関数は引数を1つ受け取る必要があります")
            self._require_assignable(function_type.args[0], list_type.args[0], token)
            if name == "filter":
                self._require(function_type.result == BOOL, token, "filter の判定関数は bool を返す必要があります")
                return list_type
            return LIST(function_type.result or ANY)
        if name == "reduce":
            self._arity(name, types, 3, token)
            function_type, list_type, initial_type = types
            self._require(list_type.name == "list", token, "reduce(function, list, initial) と書きます")
            self._require(function_type.name == "fn" or function_type == ANY, token, "reduce の1つ目は関数です")
            if function_type != ANY:
                self._require(len(function_type.args) == 2, token, "reduce の関数は accumulator と item の2引数が必要です")
                self._require_assignable(function_type.args[0], initial_type, token)
                self._require_assignable(function_type.args[1], list_type.args[0], token)
                self._require_assignable(initial_type, function_type.result or ANY, token)
            return initial_type
        if name == "find":
            self._arity(name, types, 3, token)
            function_type, list_type, fallback_type = types
            self._require(list_type.name == "list", token, "find(function, list, fallback) と書きます")
            self._require_assignable(list_type.args[0], fallback_type, token)
            self._require(function_type.name == "fn" or function_type == ANY, token, "find の1つ目は関数です")
            if function_type != ANY:
                self._require(len(function_type.args) == 1, token, "find の判定関数は引数を1つ受け取る必要があります")
                self._require_assignable(function_type.args[0], list_type.args[0], token)
                self._require(function_type.result == BOOL, token, "find の判定関数は bool を返す必要があります")
            return list_type.args[0]
        if name in {"any", "all"}:
            self._arity(name, types, 2, token)
            function_type, list_type = types
            self._require(list_type.name == "list", token, f"{name}(function, list) と書きます")
            self._require(function_type.name == "fn" or function_type == ANY, token, f"{name} の1つ目は関数です")
            if function_type != ANY:
                self._require(len(function_type.args) == 1, token, f"{name} の判定関数は引数を1つ受け取る必要があります")
                self._require_assignable(function_type.args[0], list_type.args[0], token)
                self._require(function_type.result == BOOL, token, f"{name} の判定関数は bool を返す必要があります")
            return BOOL
        if name == "split": self._arity(name, types, 2, token); self._require(types == [TEXT,TEXT], token, "split(text, separator) と書きます"); return LIST(TEXT)
        if name == "join": self._arity(name, types, 2, token); self._require(types[0].name == "list" and types[0].args[0] == TEXT and types[1] == TEXT, token, "join(list[text], separator) と書きます"); return TEXT
        if name in {"trim", "upper", "lower"}: self._arity(name, types, 1, token); self._require(types[0] == TEXT, token, f"{name} はtextに使います"); return TEXT
        if name == "replace": self._arity(name, types, 3, token); self._require(types == [TEXT,TEXT,TEXT], token, "replace(text, old, new) と書きます"); return TEXT
        if name in {"starts_with", "ends_with"}: self._arity(name, types, 2, token); self._require(types == [TEXT,TEXT], token, f"{name} はtextを2つ受け取ります"); return BOOL
        if name == "find_text": self._arity(name, types, 2, token); self._require(types == [TEXT,TEXT], token, "find_text(text, search) と書きます"); return INT
        if name == "substring": self._arity(name, types, 3, token); self._require(types == [TEXT,INT,INT], token, "substring(text, start, end) と書きます"); return TEXT
        if name == "map_of":
            if len(types)%2 != 0: self._error(token, "map_of は key, value の組を渡してください")
            if not types: return MAP(ANY,ANY)
            key, value = types[0], types[1]
            self._require(self._is_hashable(key), token, f"mapのキーにはハッシュ可能な型が必要です: {key}")
            for i in range(2,len(types),2):
                self._require(types[i] == key, token, "mapのキー型を統一してください")
                if types[i+1] != value: value = ANY
            return MAP(key,value)
        if name == "map_put":
            self._arity(name, types, 3, token); self._require(types[0].name=="map", token, "map_put の1つ目はmapです"); self._require_assignable(types[0].args[0], types[1], token)
            if types[0].args[1] != ANY: self._require_assignable(types[0].args[1], types[2], token)
            return types[0]
        if name == "map_remove":
            self._arity(name, types, 2, token); self._require(types[0].name=="map", token, "map_remove の1つ目はmapです")
            self._require_assignable(types[0].args[0], types[1], token); return types[0]
        if name == "map_keys": self._arity(name, types, 1, token); self._require(types[0].name=="map", token, "map_keys はmapに使います"); return LIST(types[0].args[0])
        if name == "map_values": self._arity(name, types, 1, token); self._require(types[0].name=="map", token, "map_values はmapに使います"); return LIST(types[0].args[1])
        if name == "map_contains":
            self._arity(name, types, 2, token); self._require(types[0].name=="map", token, "map_contains はmapに使います")
            self._require_assignable(types[0].args[0], types[1], token); return BOOL
        if name == "set_of":
            if not types: return SET(ANY)
            first=types[0]
            self._require(self._is_hashable(first), token, f"setの要素にはハッシュ可能な型が必要です: {first}")
            for typ in types[1:]: self._require(typ==first, token, "setの要素型を統一してください")
            return SET(first)
        if name in {"set_add","set_remove"}: self._arity(name, types, 2, token); self._require(types[0].name=="set", token, f"{name} の1つ目はsetです"); self._require_assignable(types[0].args[0], types[1], token); return types[0]
        if name == "set_contains":
            self._arity(name, types, 2, token); self._require(types[0].name=="set", token, "set_contains はsetに使います")
            self._require_assignable(types[0].args[0], types[1], token); return BOOL
        if name in {"set_union","set_intersection"}: self._arity(name, types, 2, token); self._require(types[0].name=="set" and types[0]==types[1], token, f"{name} は同じ型のsetに使います"); return types[0]
        raise AssertionError(name)

    def _arity(self, name: str, args: list[Type], count: int, token: Token) -> None:
        if len(args) != count: self._error(token, f"{name} の引数は {count} 個必要です", diagnostic_id="SAGA-T105")

    def _find_var(self, name: str) -> VariableInfo | None:
        for scope in reversed(self.scopes):
            if name in scope: return scope[name]
        return None

    def _name_candidates(self) -> list[str]:
        names: set[str] = set(BUILTINS) | set(MODULES) | set(self.functions) | set(self.classes) | set(self.enums) | set(self.source_modules)
        for scope in self.scopes:
            names.update(scope)
        return sorted(names)

    @staticmethod
    def _closest(name: str, candidates: list[str] | set[str] | tuple[str, ...]) -> str | None:
        matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.62)
        return matches[0] if matches else None

    def _resolve_var(self, token: Token) -> VariableInfo:
        info = self._find_var(token.lexeme)
        if info: return info
        candidate = self._closest(token.lexeme, self._name_candidates())
        hint = f"candidate:{candidate}" if candidate else f"先に let {token.lexeme} = ... と宣言してください"
        self._error(token, f"変数 '{token.lexeme}' は宣言されていません", hint, "SAGA-T102")

    def _require_assignable(self, expected: Type, actual: Type, token: Token) -> None:
        if not self._is_assignable(expected, actual): self._error(token, f"型が一致しません。必要: {expected}、実際: {actual}", diagnostic_id="SAGA-T103")

    def _is_assignable(self, expected: Type, actual: Type) -> bool:
        if expected.name == "fn" and actual.name == "fn":
            if len(expected.args) != len(actual.args) or expected.result is None or actual.result is None:
                return False
            # A value stored behind an expected function type must accept every
            # argument the caller is allowed to provide (contravariance), and
            # its result must fit the expected result contract (covariance).
            return (
                all(self._is_assignable(actual_param, expected_param) for expected_param, actual_param in zip(expected.args, actual.args))
                and self._is_assignable(expected.result, actual.result)
            )
        if is_assignable(expected, actual):
            return True
        if expected.name.startswith("object:") and actual.name.startswith("object:"):
            return self._class_is_subtype(actual, expected)
        return False

    def _class_is_subtype(self, actual: Type, expected: Type) -> bool:
        if actual == expected:
            return True
        actual_name = self._object_name(actual)
        if actual_name is None:
            return False
        info = self.classes.get(actual_name)
        if info is None:
            return False
        mapping = dict(zip(info.type_params, actual.args))
        for relation in info.interfaces:
            specialized = substitute(relation, mapping)
            if specialized == expected or self._class_is_subtype(specialized, expected):
                return True
        if info.base:
            specialized = substitute(info.base, mapping)
            if specialized == expected or self._class_is_subtype(specialized, expected):
                return True
        return False

    def _unify_invariant(self, pattern: Type, actual: Type, mapping: dict[str, Type]) -> bool:
        if pattern.name == "typeapply" and pattern.args:
            constructor, *arguments = pattern.args
            if not is_typevar(constructor) or len(arguments) != len(actual.args):
                return False
            name = typevar_name(constructor)
            candidate = TYPECTOR(actual.name)
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = candidate
            elif existing != candidate:
                return False
            return all(self._unify_invariant(p, a, mapping) for p, a in zip(arguments, actual.args))
        if is_typevar(pattern):
            name = typevar_name(pattern)
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = actual
                return True
            return existing == actual
        if pattern == ANY or actual == ANY:
            return pattern == actual
        if pattern.name != actual.name or len(pattern.args) != len(actual.args):
            return False
        if pattern.name == "fn":
            if pattern.result is None or actual.result is None:
                return pattern.result is actual.result
            return (
                all(self._unify_invariant(p, a, mapping) for p, a in zip(pattern.args, actual.args))
                and self._unify_invariant(pattern.result, actual.result, mapping)
            )
        if pattern.args:
            return all(self._unify_invariant(p, a, mapping) for p, a in zip(pattern.args, actual.args))
        return pattern == actual

    def _unify_native_contract(self, pattern: Type, actual: Type, mapping: dict[str, Type]) -> bool:
        """Match a hosted/native API contract without weakening Saga generics.

        Native signatures use nested ``any`` as an explicit host-boundary
        wildcard (for example ``list[any]`` means "a list whose elements are
        dynamically validated by that API"). User-defined generic assignment
        remains invariant; this wildcard rule is intentionally scoped to
        standard/native function parameter contracts only.
        """
        if pattern == ANY:
            return True
        if is_typevar(pattern):
            name = typevar_name(pattern)
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = actual
                return True
            return self._is_assignable(existing, actual) and self._is_assignable(actual, existing)
        if pattern.name != actual.name:
            return self._is_assignable(pattern, actual)
        if len(pattern.args) != len(actual.args):
            return False
        if pattern.name == "fn":
            if pattern.result is None or actual.result is None:
                return pattern.result is actual.result
            return (
                all(self._unify_native_contract(p, a, mapping) for p, a in zip(pattern.args, actual.args))
                and self._unify_native_contract(pattern.result, actual.result, mapping)
            )
        if pattern.args:
            return all(self._unify_native_contract(p, a, mapping) for p, a in zip(pattern.args, actual.args))
        return self._is_assignable(pattern, actual)

    def _unify(self, pattern: Type, actual: Type, mapping: dict[str, Type]) -> bool:
        if pattern.name == "typeapply" and pattern.args:
            constructor, *arguments = pattern.args
            if not is_typevar(constructor) or len(arguments) != len(actual.args):
                return False
            name = typevar_name(constructor)
            candidate = TYPECTOR(actual.name)
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = candidate
            elif existing != candidate:
                return False
            return all(self._unify(p, a, mapping) for p, a in zip(arguments, actual.args))
        if is_typevar(pattern):
            name = typevar_name(pattern)
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = actual
                return True
            return self._is_assignable(existing, actual) and self._is_assignable(actual, existing)
        if pattern == ANY or actual == ANY:
            return True
        if pattern.name == actual.name and len(pattern.args) == len(actual.args):
            if pattern.name == "fn":
                if pattern.result is None or actual.result is None:
                    return pattern.result is actual.result
                return (
                    all(self._unify(p, a, mapping) for p, a in zip(pattern.args, actual.args))
                    and self._unify(pattern.result, actual.result, mapping)
                )
            if pattern.args:
                return all(self._unify_invariant(p, a, mapping) for p, a in zip(pattern.args, actual.args))
            return pattern == actual
        return self._is_assignable(pattern, actual)

    @staticmethod
    def _is_hashable(value: Type) -> bool:
        return value in {INT, DECIMAL, RATIONAL, BOOL, TEXT, BYTES, DATETIME, DURATION} or (value.name == "option" and TypeChecker._is_hashable(value.args[0]))

    def _require_annotation_literal(self, expr: ast.Expr, token: Token) -> None:
        if isinstance(expr, ast.Literal):
            return
        if isinstance(expr, ast.ListLiteral):
            for item in expr.elements:
                self._require_annotation_literal(item, token)
            return
        self._error(token, "アノテーション引数はリテラルまたはリテラルのリストにしてください")

    def _require(self, condition: bool, token: Token, message: str, hint: str | None = None, diagnostic_id: str | None = None) -> None:
        if not condition: self._error(token, message, hint, diagnostic_id or "SAGA-T103")

    def _error(self, token: Token, message: str, hint: str | None = None, diagnostic_id: str | None = None):
        raise TypeCheckError(
            message, token.line, token.column, token.filename or self.filename, hint,
            end_column=token.column + max(len(token.lexeme), 1), detail_code=diagnostic_id, detail_data={"token": token.lexeme},
        )
