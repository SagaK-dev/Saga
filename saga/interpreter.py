from __future__ import annotations

import copy
import re
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from fractions import Fraction
from typing import Callable

from . import ast_nodes as ast
from .errors import RuntimeLanguageError, RuntimeResourceError
from .native import Capabilities, NativeFailure, NativeFunction, NativeModule
from .stdlib import MODULES
from .tokens import Token, TokenKind
from .typesys import ANY, BOOL, BYTES, CLASS_VALUE, DATETIME, DECIMAL, DURATION, ERROR, INT, RATIONAL, TEXT, UNIT, FUNCTION, TYPECTOR, Type, is_assignable, is_typevar, parse_type, substitute, typevar_name, unify
from .values import OptionValue, ResultValue


@dataclass(slots=True)
class Cell:
    value: object
    mutable: bool
    contract: Type | None = None
    moved: bool = False


class Environment:
    def __init__(self, parent: "Environment | None" = None) -> None:
        self.parent = parent
        self.values: dict[str, Cell] = {}

    def define(self, name: str, value: object, mutable: bool, contract: Type | None = None) -> None:
        self.values[name] = Cell(value, mutable, contract)

    def find_cell(self, name: str) -> Cell | None:
        if name in self.values:
            return self.values[name]
        if self.parent:
            return self.parent.find_cell(name)
        return None

    def get(self, token: Token) -> object:
        return self.get_name(token.lexeme)

    def get_name(self, name: str) -> object:
        if name in self.values:
            cell = self.values[name]
            if cell.moved: raise RuntimeError(f"SAGA-R181: use after move: {name}")
            return cell.value
        if self.parent: return self.parent.get_name(name)
        raise KeyError(name)

    def assign(self, token: Token, value: object) -> None:
        self.assign_name(token.lexeme, value)

    def assign_name(self, name: str, value: object) -> None:
        if name in self.values:
            cell = self.values[name]
            if not cell.mutable: raise PermissionError(name)
            cell.value = value; cell.moved = False; return
        if self.parent: self.parent.assign_name(name, value); return
        raise KeyError(name)

    def move_name(self, name: str) -> object:
        cell = self.find_cell(name)
        if cell is None:
            raise KeyError(name)
        if cell.moved:
            raise RuntimeError(f"SAGA-R181: value already moved: {name}")
        cell.moved = True
        return cell.value


class ReturnSignal(Exception):
    def __init__(self, value: object) -> None: self.value = value


class BreakSignal(Exception): pass
class ContinueSignal(Exception): pass


@dataclass(slots=True)
class ErrorValue:
    message: str
    kind: str = "Error"


class SagaThrown(Exception):
    def __init__(self, error: ErrorValue) -> None:
        self.error = error


@dataclass(frozen=True, slots=True)
class SagaRange:
    start: int
    end: int

    def values(self):
        step = 1 if self.end >= self.start else -1
        return range(self.start, self.end + step, step)


@dataclass(slots=True)
class RuntimeField:
    name: str
    mutable: bool
    private: bool
    owner: str
    type_name: str


class BuiltinFunction:
    def __init__(self, name: str) -> None: self.name = name
    def __repr__(self) -> str: return f"<builtin {self.name}>"


@dataclass(slots=True)
class SagaClosure:
    expression: ast.ClosureExpr
    closure: Environment
    type_bindings: dict[str, Type] = field(default_factory=dict)

    def call(self, interpreter: "Interpreter", arguments: list[object]) -> object:
        explicit = self.expression.parameters
        if explicit:
            if len(arguments) != len(explicit):
                interpreter._runtime_error(
                    self.expression.brace,
                    f"このクロージャの引数は {len(explicit)} 個必要です",
                )
        elif len(arguments) > 1:
            interpreter._runtime_error(self.expression.brace, "暗黙の it クロージャが受け取れる引数は1個までです")

        env = Environment(self.closure)
        if explicit:
            for token, value in zip(explicit, arguments):
                env.define(token.lexeme, value, False)
        elif arguments:
            env.define("it", arguments[0], False)

        previous = interpreter.environment
        interpreter.call_depth += 1
        interpreter._type_var_stack.append(dict(self.type_bindings))
        interpreter._defer_frames.append([])
        try:
            interpreter.environment = env
            statements = self.expression.body.statements
            # Keep local function semantics identical to ordinary lexical blocks.
            for stmt in statements:
                if isinstance(stmt, ast.FunctionDecl) and stmt.name.lexeme not in env.values:
                    fn = UserFunction(stmt, env, captured_type_bindings=dict(interpreter._type_var_stack[-1]))
                    fn.annotations = interpreter._annotation_map(stmt.annotations)
                    env.define(stmt.name.lexeme, fn, False)
            for index, stmt in enumerate(statements):
                is_last = index == len(statements) - 1
                if is_last and isinstance(stmt, ast.ExpressionStmt):
                    return interpreter._evaluate(stmt.expression)
                interpreter._execute(stmt)
            return None
        except ReturnSignal as signal:
            # ``return`` remains useful in a multi-statement callback and returns
            # from the callback itself, never from the enclosing function.
            return signal.value
        except BreakSignal:
            interpreter._runtime_error(self.expression.brace, "break はクロージャの外側のループへジャンプできません")
        except ContinueSignal:
            interpreter._runtime_error(self.expression.brace, "continue はクロージャの外側のループへジャンプできません")
        finally:
            frame = interpreter._defer_frames.pop()
            deferred_error: BaseException | None = None
            for expr in reversed(frame):
                try:
                    interpreter._evaluate(expr)
                except BaseException as exc:
                    if deferred_error is None:
                        deferred_error = exc
            interpreter.environment = previous
            interpreter._type_var_stack.pop()
            interpreter.call_depth -= 1
            if deferred_error is not None and sys.exc_info()[0] is None:
                raise deferred_error

    def __repr__(self) -> str:
        return "<closure>"


@dataclass(slots=True)
class ExtensionMethod:
    receiver: object
    name: str

    def __repr__(self) -> str:
        return f"<extension {self.name}>"


class UserFunction:
    def __init__(
        self,
        declaration: ast.FunctionDecl,
        closure: Environment,
        owner: str | None = None,
        captured_type_bindings: dict[str, Type] | None = None,
    ) -> None:
        self.declaration = declaration
        self.closure = closure
        self.owner = owner
        self.captured_type_bindings = dict(captured_type_bindings or {})
        self.annotations: dict[str, tuple[object, ...]] = {}

    @property
    def name(self) -> str: return self.declaration.name.lexeme

    def call(self, interpreter: "Interpreter", arguments: list[object], receiver: "SagaInstance | None" = None) -> object:
        if self.declaration.abstract:
            interpreter._runtime_error(self.declaration.name, f"抽象メソッド '{self.name}' は直接実行できません")
        expected = len(self.declaration.parameters)
        if len(arguments) != expected:
            interpreter._runtime_error(self.declaration.name, f"関数 '{self.name}' の引数は {expected} 個必要です")
        type_bindings = dict(self.captured_type_bindings)
        type_var_names = set(type_bindings)
        type_var_names.update(self.declaration.type_params)
        if self.owner and self.owner in interpreter.classes:
            type_var_names.update(interpreter.classes[self.owner].declaration.type_params)
        # Infer concrete runtime bindings for generic function parameters from
        # the actual arguments. Static checking remains authoritative, but the
        # runtime mapping is needed to re-check ``any``/hosted values that cross
        # a dynamic boundary inside a generic function.
        for parameter, value in zip(self.declaration.parameters, arguments):
            interpreter._bind_runtime_typevars(
                parse_type(parameter.type_name, type_var_names), value, type_bindings,
            )
        for parameter, value in zip(self.declaration.parameters, arguments):
            contract = substitute(parse_type(parameter.type_name, type_var_names), type_bindings)
            interpreter.validate_native_value(
                contract, value,
                f"関数 '{self.name}' の引数 '{parameter.name.lexeme}'",
            )
        env = Environment(self.closure)
        if receiver is not None: env.define("self", receiver, False)
        for parameter, value in zip(self.declaration.parameters, arguments): env.define(parameter.name.lexeme, value, False)
        interpreter.call_depth += 1
        interpreter._owner_stack.append(self.owner)
        interpreter._type_var_stack.append(type_bindings)
        previous = interpreter.environment
        try:
            interpreter.environment = env
            if self.declaration.expression_body is not None:
                result = interpreter._evaluate(self.declaration.expression_body)
            else:
                assert self.declaration.body is not None
                try:
                    interpreter._execute_block(self.declaration.body.statements, env, restore=False)
                    result = None
                except ReturnSignal as signal:
                    result = signal.value
                except BreakSignal:
                    interpreter._runtime_error(self.declaration.name, "break は関数の外側のループへジャンプできません")
                except ContinueSignal:
                    interpreter._runtime_error(self.declaration.name, "continue は関数の外側のループへジャンプできません")
            if self.declaration.return_type is not None:
                return_contract = substitute(parse_type(self.declaration.return_type, type_var_names), type_bindings)
                interpreter.validate_native_value(
                    return_contract, result,
                    f"関数 '{self.name}' の戻り値",
                )
            return result
        finally:
            interpreter.environment = previous
            interpreter._type_var_stack.pop()
            interpreter._owner_stack.pop()
            interpreter.call_depth -= 1

    def __repr__(self) -> str: return f"<fn {self.name}>"


@dataclass(slots=True)
class BoundMethod:
    receiver: "SagaInstance"
    function: UserFunction

    def __repr__(self) -> str: return f"<method {self.receiver.klass.name}.{self.function.name}>"


@dataclass(slots=True)
class EnumType:
    name: str
    variants: dict[str, tuple[str, ...]]
    module_namespace: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.module_namespace}.{self.name}" if self.module_namespace else self.name

    def __repr__(self) -> str:
        return f"<enum {self.qualified_name}>"


@dataclass(frozen=True, slots=True)
class EnumValue:
    enum_name: str
    variant: str
    payload: tuple[object, ...] = ()

    def __repr__(self) -> str:
        if not self.payload:
            return f"{self.enum_name}.{self.variant}"
        args = ", ".join(repr(value) for value in self.payload)
        return f"{self.enum_name}.{self.variant}({args})"


@dataclass(frozen=True, slots=True)
class EnumConstructor:
    enum_type: EnumType
    variant: str
    payload_types: tuple[str, ...]

    def __call__(self, *args: object) -> object:
        if len(args) != len(self.payload_types):
            raise NativeFailure(
                f"{self.enum_type.qualified_name}.{self.variant} は "
                f"{len(self.payload_types)} 個のpayloadを必要とします"
            )
        if self.enum_type.qualified_name == "Option" and self.variant == "Some":
            return OptionValue.some(args[0])
        if self.enum_type.qualified_name == "Result" and self.variant == "Ok":
            return ResultValue.success(args[0])
        if self.enum_type.qualified_name == "Result" and self.variant == "Err":
            return ResultValue.failure(args[0])
        return EnumValue(self.enum_type.qualified_name, self.variant, tuple(args))


@dataclass(slots=True)
class SourceModuleValue:
    name: str
    bind_name: str
    exports: dict[str, object]

    def __repr__(self) -> str:
        return f"<module {self.name}>"


@dataclass(slots=True)
class SagaClass:
    name: str
    declaration: ast.ClassDecl
    base: "SagaClass | None" = None
    fields: dict[str, RuntimeField] = field(default_factory=dict)
    own_fields: dict[str, RuntimeField] = field(default_factory=dict)
    methods: dict[str, UserFunction] = field(default_factory=dict)
    annotations: dict[str, tuple[object, ...]] = field(default_factory=dict)
    abstract: bool = False
    interface: bool = False
    module_namespace: str | None = None

    def constructor_fields(self) -> list[RuntimeField]:
        return list(self.fields.values())

    def __repr__(self) -> str: return f"<class {self.name}>"


@dataclass(slots=True)
class SagaInstance:
    klass: SagaClass
    values: dict[str, object]

    def __repr__(self) -> str: return f"<{self.klass.name} object>"


class Interpreter:
    def __init__(
        self,
        filename: str = "<input>",
        output: Callable[[str], None] = print,
        precision: int = 50,
        step_limit: int | None = None,
        capabilities: Capabilities | None = None,
        debug_hook: Callable[[Token, "Environment"], None] | None = None,
    ) -> None:
        self.filename = filename
        self.output = output
        self.globals = Environment()
        self.environment = self.globals
        self.functions: dict[str, UserFunction] = {}
        self.classes: dict[str, SagaClass] = {}
        self.enums: dict[str, EnumType] = {}
        self.context = Context(prec=precision)
        self.step_limit = step_limit
        self.steps = 0
        self.call_depth = 0
        self._owner_stack: list[str | None] = []
        self._type_var_stack: list[dict[str, Type]] = [{}]
        self.capabilities = capabilities or Capabilities.safe()
        self.debug_hook = debug_hook
        self.program: ast.Program | None = None
        self._call_lock = threading.RLock()
        self._output_lock = threading.RLock()
        self._task_pool = ThreadPoolExecutor(thread_name_prefix="saga-task")
        self._resources: list[object] = []
        self._module_interpreters: list["Interpreter"] = []
        self._defer_frames: list[list[ast.Expr]] = []
        self._task_groups: list[list[Future]] = []
        self._register_builtins()

    def _register_builtins(self) -> None:
        from .checker import BUILTINS
        for name in BUILTINS:
            if name not in {"Option", "Result"}:
                self.globals.define(name, BuiltinFunction(name), False)
        option_enum = EnumType("Option", {"Some": ("T",), "None": ()})
        result_enum = EnumType("Result", {"Ok": ("T",), "Err": ("E",)})
        self.enums.update({"Option": option_enum, "Result": result_enum})
        self.globals.define("Option", option_enum, False)
        self.globals.define("Result", result_enum, False)

    def interpret(self, program: ast.Program) -> None:
        if self.program is not None:
            raise RuntimeError("Interpreter.interpret can only load one complete program; use interpret_incremental for a session")
        self.program = program
        # Namespaced source modules are initialized before importer class
        # resolution. A local class may extend `m.Base`, whose runtime class
        # object lives in the isolated module interpreter rather than this
        # interpreter's local class table.
        for stmt in program.statements:
            if isinstance(stmt, ast.SourceModuleStmt):
                self._execute(stmt)
        self._register_declarations(program)
        for stmt in program.statements:
            if not isinstance(stmt, (ast.FunctionDecl, ast.ClassDecl, ast.SourceModuleStmt)):
                self._execute(stmt)

    def _snapshot_session_environment(
        self,
        env: Environment,
        value_memo: dict[int, object],
        env_memo: dict[int, Environment],
    ) -> Environment:
        if env is self.globals:
            return self.globals
        identity = id(env)
        if identity in env_memo:
            return env_memo[identity]
        parent = self._snapshot_session_environment(env.parent, value_memo, env_memo) if env.parent else None
        copied = Environment(parent)
        env_memo[identity] = copied
        for name, cell in env.values.items():
            copied.values[name] = Cell(
                self._snapshot_session_value(cell.value, value_memo, env_memo),
                cell.mutable,
                cell.contract,
            )
        return copied

    def _snapshot_session_value(
        self,
        value: object,
        memo: dict[int, object],
        env_memo: dict[int, Environment],
    ) -> object:
        """Copy mutable Saga language state for REPL rollback.

        Host/native resources keep their identity because external side effects cannot
        be rolled back. Saga objects, collections, closures, and local functions are
        copied so a failed submission cannot mutate committed in-language state.
        """
        identity = id(value)
        if identity in memo:
            return memo[identity]
        if value is None or isinstance(value, (bool, int, Decimal, Fraction, str, bytes, datetime, timedelta, SagaRange)):
            return value
        if isinstance(value, ErrorValue):
            copied = ErrorValue(value.message, value.kind)
            memo[identity] = copied
            return copied
        if isinstance(value, OptionValue):
            # Register frozen wrapper identity before descending into its payload.
            # A legal Saga/native value graph may point back to the wrapper via a
            # map/object field; registering afterwards would duplicate the wrapper
            # and break alias/cycle identity during REPL rollback.
            copied = object.__new__(OptionValue)
            memo[identity] = copied
            object.__setattr__(copied, "present", value.present)
            payload = self._snapshot_session_value(value.value, memo, env_memo) if value.present else None
            object.__setattr__(copied, "value", payload)
            return copied
        if isinstance(value, ResultValue):
            copied = object.__new__(ResultValue)
            memo[identity] = copied
            object.__setattr__(copied, "ok", value.ok)
            object.__setattr__(copied, "value", self._snapshot_session_value(value.value, memo, env_memo))
            return copied
        if isinstance(value, tuple):
            copied = tuple(self._snapshot_session_value(item, memo, env_memo) for item in value)
            memo[identity] = copied
            return copied
        if isinstance(value, dict):
            copied: dict[object, object] = {}
            memo[identity] = copied
            for key, item in value.items():
                copied[self._snapshot_session_value(key, memo, env_memo)] = self._snapshot_session_value(item, memo, env_memo)
            return copied
        if isinstance(value, frozenset):
            copied = frozenset(self._snapshot_session_value(item, memo, env_memo) for item in value)
            memo[identity] = copied
            return copied
        if isinstance(value, SagaInstance):
            copied = SagaInstance(value.klass, {})
            memo[identity] = copied
            for name, item in value.values.items():
                copied.values[name] = self._snapshot_session_value(item, memo, env_memo)
            return copied
        if isinstance(value, SagaClosure):
            copied = SagaClosure(
                value.expression,
                self._snapshot_session_environment(value.closure, memo, env_memo),
                dict(value.type_bindings),
            )
            memo[identity] = copied
            return copied
        if isinstance(value, UserFunction):
            if self.functions.get(value.name) is value:
                return value
            copied = UserFunction(
                value.declaration,
                self._snapshot_session_environment(value.closure, memo, env_memo),
                value.owner,
                dict(value.captured_type_bindings),
            )
            copied.annotations = dict(value.annotations)
            memo[identity] = copied
            return copied
        if isinstance(value, BoundMethod):
            receiver = self._snapshot_session_value(value.receiver, memo, env_memo)
            assert isinstance(receiver, SagaInstance)
            copied = BoundMethod(receiver, value.function)
            memo[identity] = copied
            return copied
        if isinstance(value, ExtensionMethod):
            copied = ExtensionMethod(self._snapshot_session_value(value.receiver, memo, env_memo), value.name)
            memo[identity] = copied
            return copied
        # Builtins, classes, native resources, futures and other host objects are
        # stable identities from the session's perspective. Their external side
        # effects are explicitly outside REPL rollback guarantees.
        return value

    def interpret_incremental(self, program: ast.Program) -> None:
        if self.program is None:
            self.program = ast.Program([])

        # A REPL submission is a language-state transaction.  Type checking is
        # already transactional in SagaSession; without a matching runtime
        # rollback, a failed submission could leave a binding in the
        # interpreter that the checker did not know about (or vice versa).
        program_length = len(self.program.statements)
        snapshot_memo: dict[int, object] = {}
        environment_memo: dict[int, Environment] = {}
        globals_snapshot = {
            name: Cell(
                self._snapshot_session_value(cell.value, snapshot_memo, environment_memo),
                cell.mutable,
                cell.contract,
            )
            for name, cell in self.globals.values.items()
        }
        functions_snapshot = dict(self.functions)
        classes_snapshot = dict(self.classes)
        resource_count = len(self._resources)
        try:
            self.program.statements.extend(program.statements)
            self._register_declarations(program)
            for stmt in program.statements:
                if not isinstance(stmt, (ast.FunctionDecl, ast.ClassDecl)):
                    self._execute(stmt)
        except BaseException:
            del self.program.statements[program_length:]
            self.globals.values = globals_snapshot
            self.functions = functions_snapshot
            self.classes = classes_snapshot
            self.environment = self.globals
            # Resources opened by a failed submission are not part of the
            # committed session state.  Close only those new resources; host
            # side effects outside registered resources cannot be rolled back.
            for resource in reversed(self._resources[resource_count:]):
                self._close_resource(resource)
            del self._resources[resource_count:]
            raise

    def _register_declarations(self, program: ast.Program) -> None:
        for stmt in program.statements:
            if isinstance(stmt, ast.EnumDecl):
                enum = EnumType(
                    stmt.name.lexeme,
                    {variant.name.lexeme: tuple(variant.payload_types) for variant in stmt.variants},
                )
                self.enums[enum.name] = enum
                self.globals.define(enum.name, enum, False)
        # Class shells first so inheritance and methods can refer to later classes.
        for stmt in program.statements:
            if isinstance(stmt, ast.ClassDecl):
                klass = SagaClass(stmt.name.lexeme, stmt, abstract=stmt.abstract, interface=stmt.interface)
                klass.annotations = self._annotation_map(stmt.annotations)
                self.classes[klass.name] = klass
                self.globals.define(klass.name, klass, False)
        for stmt in program.statements:
            if isinstance(stmt, ast.FunctionDecl):
                fn = UserFunction(stmt, self.globals)
                fn.annotations = self._annotation_map(stmt.annotations)
                self.functions[stmt.name.lexeme] = fn
                self.globals.define(stmt.name.lexeme, fn, False)
        visiting: set[str] = set(); done: set[str] = set()
        def build(name: str) -> None:
            if name in done: return
            if name in visiting: raise RuntimeError("inheritance cycle")
            visiting.add(name); klass = self.classes[name]; decl = klass.declaration
            if decl.base_name:
                try:
                    base_type = parse_type(decl.base_name, set(decl.type_params))
                except ValueError as exc:
                    self._runtime_error(decl.name, str(exc))
                base_name = base_type.name.split(":", 1)[1] if base_type.name.startswith("object:") else decl.base_name
                base_class = self._resolve_runtime_class(base_name)
                if base_class is None:
                    self._runtime_error(decl.name, f"親クラス '{base_name}' が見つかりません")
                if base_name in self.classes:
                    build(base_name)
                klass.base = base_class
                base_info = base_class.declaration
                mapping = dict(zip(base_info.type_params, base_type.args))
                for field_name, inherited in klass.base.fields.items():
                    specialized_name = inherited.type_name
                    if mapping:
                        try:
                            specialized_name = str(substitute(parse_type(inherited.type_name, set(mapping)), mapping))
                        except ValueError:
                            specialized_name = inherited.type_name
                    klass.fields[field_name] = RuntimeField(
                        inherited.name, inherited.mutable, inherited.private, inherited.owner, specialized_name
                    )
                klass.methods.update(klass.base.methods)
            for field_decl in decl.fields:
                runtime_field = RuntimeField(field_decl.name.lexeme, field_decl.mutable, field_decl.private, klass.name, field_decl.type_name)
                klass.own_fields[runtime_field.name] = runtime_field; klass.fields[runtime_field.name] = runtime_field
            for method_decl in decl.methods:
                fn = UserFunction(method_decl, self.globals, owner=klass.name)
                fn.annotations = self._annotation_map(method_decl.annotations)
                klass.methods[method_decl.name.lexeme] = fn
            visiting.remove(name); done.add(name)
        for name in self.classes: build(name)

    def _resolve_runtime_class(self, name: str) -> SagaClass | None:
        local = self.classes.get(name)
        if local is not None:
            return local
        if "." not in name:
            return None
        bind, member = name.split(".", 1)
        try:
            namespace = self.environment.get_name(bind)
        except Exception:
            return None
        if isinstance(namespace, SourceModuleValue):
            value = namespace.exports.get(member)
            if isinstance(value, SagaClass):
                return value
        return None

    def _annotation_map(self, annotations: list[ast.Annotation]) -> dict[str, tuple[object, ...]]:
        result: dict[str, tuple[object, ...]] = {}
        for item in annotations:
            result[item.name.lexeme] = tuple(self._metadata_value(arg) for arg in item.arguments)
        return result

    def _metadata_value(self, expr: ast.Expr) -> object:
        if isinstance(expr, ast.Literal): return expr.value
        if isinstance(expr, ast.ListLiteral): return tuple(self._metadata_value(v) for v in expr.elements)
        raise RuntimeLanguageError("アノテーション引数はリテラルにしてください", 1, 1, self.filename)

    def _tick(self, token: Token | None = None) -> None:
        self.steps += 1
        if self.step_limit is not None and self.steps > self.step_limit:
            if token is None:
                raise RuntimeResourceError("利用者が指定した実行ステップ予算を使い切りました", 1, 1, self.filename)
            raise RuntimeResourceError(
                "利用者が指定した実行ステップ予算を使い切りました",
                token.line, token.column, token.filename or self.filename,
                "--step-limit は任意の実行監視設定であり、Saga言語仕様の規定上限ではありません",
            )

    def _execute(self, stmt: ast.Stmt) -> None:
        token = getattr(stmt, "keyword", None) or getattr(stmt, "name", None)
        self._tick(token)
        debug_token = token or getattr(stmt, "equals", None)
        if debug_token is None and isinstance(stmt, ast.ExpressionStmt):
            debug_token = self._expression_debug_token(stmt.expression)
        if self.debug_hook is not None and isinstance(debug_token, Token):
            self.debug_hook(debug_token, self.environment)
        if isinstance(stmt, ast.ModuleDecl):
            return
        if isinstance(stmt, ast.SourceModuleStmt):
            bind = stmt.bind_name or stmt.name
            child = Interpreter(
                stmt.token.filename or self.filename,
                output=self.output, precision=self.context.prec, step_limit=self.step_limit,
                capabilities=self.capabilities, debug_hook=self.debug_hook,
            )
            child.interpret(ast.Program(stmt.statements))
            # Preserve namespace identity at runtime. The class's local name is
            # retained for method/private-field ownership; module_namespace makes
            # nominal contracts distinguish two modules that both export `User`.
            for klass in child.classes.values():
                klass.module_namespace = bind
            for enum in child.enums.values():
                enum.module_namespace = bind
            exports: dict[str, object] = {}
            for decl in stmt.statements:
                if getattr(decl, "visibility", "internal") != "public":
                    continue
                if isinstance(decl, (ast.VarDecl, ast.FunctionDecl, ast.ClassDecl, ast.EnumDecl)):
                    try:
                        exports[decl.name.lexeme] = child.globals.get_name(decl.name.lexeme)
                    except KeyError:
                        pass
            self.environment.define(bind, SourceModuleValue(stmt.name, bind, exports), False)
            self._module_interpreters.append(child)
            return
        if isinstance(stmt, ast.UseStmt):
            if stmt.source_path is not None:
                self._runtime_error(stmt.module, "ソース単位のuseが実行前に展開されていません")
            module = MODULES.get(stmt.module.lexeme)
            if module is None: self._runtime_error(stmt.module, f"標準モジュール '{stmt.module.lexeme}' がありません")
            bind = stmt.alias.lexeme if stmt.alias is not None else stmt.module.lexeme
            if bind not in self.environment.values: self.environment.define(bind, module, False)
        elif isinstance(stmt, ast.VarDecl):
            value = self._evaluate(stmt.initializer)
            contract = None
            if stmt.type_name:
                try:
                    bindings = self._type_var_stack[-1]
                    contract = substitute(parse_type(stmt.type_name, set(bindings)), bindings)
                except ValueError as exc:
                    self._runtime_error(stmt.name, str(exc))
                self._validate_contract(contract, value, stmt.name, f"変数 '{stmt.name.lexeme}'")
            self.environment.define(stmt.name.lexeme, value, stmt.mutable, contract)
        elif isinstance(stmt, ast.Assign): self._assign(stmt)
        elif isinstance(stmt, ast.ExpressionStmt): self._evaluate(stmt.expression)
        elif isinstance(stmt, ast.DeferStmt):
            if not self._defer_frames:
                self._runtime_error(stmt.keyword, "defer は lexical block の中でのみ使えます", diagnostic_id="SAGA-R183")
            self._defer_frames[-1].append(stmt.value)
        elif isinstance(stmt, ast.UsingStmt):
            resource = self._evaluate(stmt.initializer)
            env = Environment(self.environment); env.define(stmt.name.lexeme, resource, False)
            pending = None
            try:
                self._execute_block(stmt.body.statements, env)
            except BaseException as exc:
                pending = exc
            close_error = self._close_resource_strict(resource, stmt.keyword)
            if pending is not None: raise pending
            if close_error is not None: raise close_error
        elif isinstance(stmt, ast.TaskGroupStmt):
            group: list[Future] = []
            self._task_groups.append(group)
            pending = None
            try:
                self._execute(stmt.body)
            except BaseException as exc:
                pending = exc
                for future in group: future.cancel()
            finally:
                self._task_groups.pop()
            for future in group:
                try: future.result()
                except BaseException as exc:
                    if pending is None: pending = exc
            if pending is not None: raise pending
        elif isinstance(stmt, ast.Block): self._execute_block(stmt.statements, Environment(self.environment))
        elif isinstance(stmt, ast.IfStmt):
            branch = stmt.then_branch if self._evaluate(stmt.condition) else stmt.else_branch
            if branch: self._execute_block(branch.statements, Environment(self.environment))
        elif isinstance(stmt, ast.MatchStmt):
            value = self._evaluate(stmt.value)
            for case in stmt.cases:
                enum_pattern, payload_match = self._match_enum_payload_pattern(value, case.pattern)
                if enum_pattern:
                    if payload_match is None:
                        continue
                    env = Environment(self.environment)
                    for name, item in payload_match.items():
                        env.define(name, item, False)
                    self._execute_block(case.body.statements, env)
                    return
                pattern = self._evaluate(case.pattern)
                if self._values_equal(value, pattern):
                    self._execute_block(case.body.statements, Environment(self.environment))
                    return
            if stmt.default is not None:
                self._execute_block(stmt.default.statements, Environment(self.environment))
        elif isinstance(stmt, ast.WhileStmt):
            while self._evaluate(stmt.condition):
                try: self._execute_block(stmt.body.statements, Environment(self.environment))
                except ContinueSignal: continue
                except BreakSignal: break
        elif isinstance(stmt, ast.ForStmt):
            iterable = self._evaluate(stmt.iterable)
            if isinstance(iterable, SagaRange):
                values = iterable.values()
            elif isinstance(iterable, frozenset):
                values = sorted(iterable, key=self._stable_order_key)
            else:
                values = iterable
            for value in values:
                env = Environment(self.environment); env.define(stmt.name.lexeme, value, False)
                try: self._execute_block(stmt.body.statements, env)
                except ContinueSignal: continue
                except BreakSignal: break
        elif isinstance(stmt, ast.BreakStmt): raise BreakSignal()
        elif isinstance(stmt, ast.ContinueStmt): raise ContinueSignal()
        elif isinstance(stmt, ast.ReturnStmt): raise ReturnSignal(None if stmt.value is None else self._evaluate(stmt.value))
        elif isinstance(stmt, ast.ThrowStmt):
            value = self._evaluate(stmt.value)
            if isinstance(value, ErrorValue): raise SagaThrown(value)
            raise SagaThrown(ErrorValue(self.format_value(value), "Thrown"))
        elif isinstance(stmt, ast.TryStmt): self._execute_try(stmt)
        elif isinstance(stmt, (ast.FunctionDecl, ast.ClassDecl, ast.EnumDecl)): return
        else: raise AssertionError(f"unknown statement: {stmt!r}")

    @staticmethod
    def _expression_debug_token(expr: ast.Expr) -> Token | None:
        if isinstance(expr, ast.Literal): return expr.token
        if isinstance(expr, ast.Variable): return expr.name
        if isinstance(expr, ast.ListLiteral): return expr.token
        if isinstance(expr, ast.AwaitExpr): return expr.keyword
        if isinstance(expr, ast.MoveExpr): return expr.keyword
        if isinstance(expr, ast.Unary): return expr.operator
        if isinstance(expr, ast.Binary): return Interpreter._expression_debug_token(expr.left) or expr.operator
        if isinstance(expr, ast.RangeExpr): return Interpreter._expression_debug_token(expr.start) or expr.operator
        if isinstance(expr, ast.Call): return Interpreter._expression_debug_token(expr.callee) or expr.paren
        if isinstance(expr, ast.Index): return Interpreter._expression_debug_token(expr.target) or expr.bracket
        if isinstance(expr, ast.Member): return Interpreter._expression_debug_token(expr.target) or expr.name
        if isinstance(expr, ast.PropagateExpr): return Interpreter._expression_debug_token(expr.value) or expr.question
        return None

    def _validate_contract(self, expected: Type, value: object, token: Token, label: str) -> None:
        try:
            self.validate_native_value(expected, value, label)
        except NativeFailure as exc:
            self._runtime_error(token, str(exc), diagnostic_id=getattr(exc, "diagnostic_id", None) or "SAGA-T103")

    def _assign(self, stmt: ast.Assign) -> None:
        if isinstance(stmt.target, ast.Variable):
            cell = self.environment.find_cell(stmt.target.name.lexeme)
            if cell is None:
                # Natural binding: the first simple assignment introduces a
                # local immutable name. Mutation is intentionally explicit via
                # ``var`` so concise code does not weaken Safe by Default.
                value = self._evaluate(stmt.value)
                self.environment.define(stmt.target.name.lexeme, value, False)
                return
            # Resolve and validate the assignment target before evaluating the
            # right-hand side.  This is observable when the RHS has effects and
            # is required by the language's left-to-right assignment semantics.
            if not cell.mutable:
                self._runtime_error(stmt.target.name, f"'{stmt.target.name.lexeme}' は変更できません")
            value = self._evaluate(stmt.value)
            if cell.contract is not None:
                self._validate_contract(cell.contract, value, stmt.target.name, f"変数 '{stmt.target.name.lexeme}'")
            cell.value = value
            cell.moved = False
            return
        if isinstance(stmt.target, ast.Member):
            target = self._evaluate(stmt.target.target)
            if not isinstance(target, SagaInstance): self._runtime_error(stmt.target.name, "フィールド代入できるのはSagaオブジェクトだけです")
            field = target.klass.fields.get(stmt.target.name.lexeme)
            if field is None: self._runtime_error(stmt.target.name, f"フィールド '{stmt.target.name.lexeme}' がありません")
            if field.private and self._current_owner() != field.owner:
                self._runtime_error(stmt.target.name, f"private フィールド '{field.name}' にはクラス外からアクセスできません")
            if not field.mutable: self._runtime_error(stmt.target.name, f"フィールド '{field.name}' は変更できません")
            try:
                field_contract = parse_type(field.type_name, set(target.klass.declaration.type_params))
            except ValueError as exc:
                self._runtime_error(stmt.target.name, str(exc))
            # Member target resolution (receiver, field lookup, visibility and
            # mutability) precedes RHS evaluation. A failed target therefore
            # cannot trigger unrelated RHS effects.
            value = self._evaluate(stmt.value)
            self._validate_contract(field_contract, value, stmt.target.name, f"フィールド '{field.name}'")
            target.values[field.name] = value; return
        self._runtime_error(stmt.equals, "代入先が正しくありません")

    def _execute_try(self, stmt: ast.TryStmt) -> None:
        pending: BaseException | None = None
        try:
            self._execute_block(stmt.try_block.statements, Environment(self.environment))
        except (SagaThrown, RuntimeLanguageError, NativeFailure) as exc:
            if stmt.catch_block and stmt.catch_name:
                if isinstance(exc, SagaThrown): error = exc.error
                elif isinstance(exc, RuntimeLanguageError): error = ErrorValue(exc.message, type(exc).__name__)
                else: error = ErrorValue(str(exc), type(exc).__name__)
                env = Environment(self.environment); env.define(stmt.catch_name.lexeme, error, False)
                self._execute_block(stmt.catch_block.statements, env)
            else: pending = exc
        finally:
            if stmt.finally_block: self._execute_block(stmt.finally_block.statements, Environment(self.environment))
        if pending: raise pending

    def _execute_block(self, statements: list[ast.Stmt], env: Environment, *, restore: bool = True) -> None:
        previous = self.environment
        frame: list[ast.Expr] = []
        self._defer_frames.append(frame)
        pending: BaseException | None = None
        try:
            self.environment = env
            # Lexical functions are hoisted within their block. The closure points
            # at the block environment itself, so mutually-recursive local functions
            # and captured mutable cells have predictable lexical semantics.
            for stmt in statements:
                if isinstance(stmt, ast.FunctionDecl) and stmt.name.lexeme not in env.values:
                    fn = UserFunction(stmt, env, captured_type_bindings=dict(self._type_var_stack[-1]))
                    fn.annotations = self._annotation_map(stmt.annotations)
                    env.define(stmt.name.lexeme, fn, False)
            try:
                for stmt in statements: self._execute(stmt)
            except BaseException as exc:
                pending = exc
            # LIFO, like Go/Rust scope guards. Defer expressions observe the block
            # environment at scope exit; the first deferred failure wins only when
            # normal execution had not already failed.
            for expr in reversed(frame):
                try: self._evaluate(expr)
                except BaseException as exc:
                    if pending is None: pending = exc
            if pending is not None: raise pending
        finally:
            self._defer_frames.pop()
            if restore: self.environment = previous

    @staticmethod
    def _enum_runtime_parts(value: object) -> tuple[str, str, tuple[object, ...]] | None:
        if isinstance(value, EnumValue):
            return value.enum_name, value.variant, value.payload
        if isinstance(value, OptionValue):
            return ("Option", "Some", (value.value,)) if value.present else ("Option", "None", ())
        if isinstance(value, ResultValue):
            return ("Result", "Ok", (value.value,)) if value.ok else ("Result", "Err", (value.value,))
        return None

    def _match_enum_payload_pattern(
        self, value: object, pattern: ast.Expr
    ) -> tuple[bool, dict[str, object] | None]:
        """Recognize a payload ADT pattern without evaluating bind variables."""
        parts = self._enum_runtime_parts(value)
        if parts is None or not isinstance(pattern, ast.Call):
            return False, None
        enum_name, variant, payload = parts
        callee = pattern.callee
        if not isinstance(callee, ast.Member):
            return False, None
        qname = self._qualified_expr_name_runtime(callee.target)
        if qname is None:
            return False, None
        expected_enum = qname
        if enum_name != expected_enum and not enum_name.endswith("." + expected_enum):
            return False, None
        if variant != callee.name.lexeme or len(payload) != len(pattern.arguments):
            return True, None
        bindings: dict[str, object] = {}
        for expr, item in zip(pattern.arguments, payload):
            if not isinstance(expr, ast.Variable):
                return True, None
            name = expr.name.lexeme
            if name != "_":
                bindings[name] = item
        return True, bindings

    @staticmethod
    def _qualified_expr_name_runtime(expr: ast.Expr) -> str | None:
        if isinstance(expr, ast.Variable):
            return expr.name.lexeme
        if isinstance(expr, ast.Member):
            base = Interpreter._qualified_expr_name_runtime(expr.target)
            return f"{base}.{expr.name.lexeme}" if base else None
        return None

    def _evaluate(self, expr: ast.Expr) -> object:
        self._tick(getattr(expr, "token", None) or getattr(expr, "operator", None) or getattr(expr, "name", None))
        if isinstance(expr, ast.Literal): return expr.value
        if isinstance(expr, ast.Variable):
            try: return self.environment.get(expr.name)
            except KeyError: self._runtime_error(expr.name, f"名前 '{expr.name.lexeme}' が見つかりません")
            except RuntimeError as exc: self._runtime_error(expr.name, str(exc), diagnostic_id="SAGA-R181")
        if isinstance(expr, ast.ListLiteral): return tuple(self._evaluate(item) for item in expr.elements)
        if isinstance(expr, ast.AwaitExpr):
            value = self._evaluate(expr.value)
            if not isinstance(value, Future):
                self._runtime_error(expr.keyword, "await には future[T] が必要です", diagnostic_id="SAGA-R184")
            try: return value.result()
            except (SagaThrown, RuntimeLanguageError, RuntimeResourceError, NativeFailure): raise
            except Exception as exc: raise NativeFailure(f"非同期処理が失敗しました: {exc}") from exc
        if isinstance(expr, ast.MoveExpr):
            if not isinstance(expr.value, ast.Variable):
                self._runtime_error(expr.keyword, "move には名前付き資源が必要です", diagnostic_id="SAGA-R181")
            try: return self.environment.move_name(expr.value.name.lexeme)
            except KeyError: self._runtime_error(expr.value.name, f"名前 '{expr.value.name.lexeme}' が見つかりません")
            except RuntimeError as exc: self._runtime_error(expr.keyword, str(exc), diagnostic_id="SAGA-R181")
        if isinstance(expr, ast.Unary):
            right = self._evaluate(expr.right)
            return not right if expr.operator.kind in {TokenKind.BANG, TokenKind.NOT} else -right
        if isinstance(expr, ast.Binary): return self._binary(expr)
        if isinstance(expr, ast.RangeExpr): return SagaRange(int(self._evaluate(expr.start)), int(self._evaluate(expr.end)))
        if isinstance(expr, ast.PropagateExpr):
            value = self._evaluate(expr.value)
            if isinstance(value, ResultValue):
                if value.ok: return value.value
                raise ReturnSignal(ResultValue.failure(value.value))
            if isinstance(value, OptionValue):
                if value.present: return value.value
                raise ReturnSignal(OptionValue.none())
            self._runtime_error(expr.question, "? は result または option にのみ使えます")
        if isinstance(expr, ast.ClosureExpr): return SagaClosure(expr, self.environment, dict(self._type_var_stack[-1]))
        if isinstance(expr, ast.Call): return self._call(expr)
        if isinstance(expr, ast.Index):
            target = self._evaluate(expr.target); index = int(self._evaluate(expr.index))
            if index < 0 or index >= len(target): self._runtime_error(expr.bracket, f"添字 {index} は範囲外です", f"使える添字は 0..{len(target)-1} です", "SAGA-R101")
            return target[index]
        if isinstance(expr, ast.Member): return self._member(expr)
        raise AssertionError(f"unknown expression: {expr!r}")

    def _member(self, expr: ast.Member) -> object:
        target = self._evaluate(expr.target); name = expr.name.lexeme
        if isinstance(target, NativeModule):
            try: return target.get(name)
            except NativeFailure as exc: self._runtime_error(expr.name, str(exc), diagnostic_id=getattr(exc, "diagnostic_id", None))
        if isinstance(target, EnumType):
            if name in target.variants:
                payload_types = target.variants[name]
                if payload_types:
                    return EnumConstructor(target, name, payload_types)
                if target.qualified_name == "Option" and name == "None":
                    return OptionValue.none()
                return EnumValue(target.qualified_name, name)
            self._runtime_error(expr.name, f"enum variant '{target.qualified_name}.{name}' が見つかりません", diagnostic_id="SAGA-R123")
        if isinstance(target, SourceModuleValue):
            if name in target.exports:
                return target.exports[name]
            self._runtime_error(expr.name, f"module member '{target.bind_name}.{name}' はpublicではないか存在しません")
        if isinstance(target, SagaInstance):
            if name in target.values:
                field = target.klass.fields.get(name)
                if field and field.private and self._current_owner() != field.owner:
                    self._runtime_error(expr.name, f"private フィールド '{name}' にはクラス外からアクセスできません")
                return target.values[name]
            method = target.klass.methods.get(name)
            if method: return BoundMethod(target, method)
            self._runtime_error(expr.name, f"'{target.klass.name}' に '{name}' はありません")
        if isinstance(target, ErrorValue):
            if name == "message": return target.message
            if name == "kind": return target.kind
            self._runtime_error(expr.name, "error で使えるのは message と kind です")
        if isinstance(target, dict) and name in target: return target[name]
        if self._is_extension_member(target, name): return ExtensionMethod(target, name)
        self._runtime_error(expr.name, f"{type(target).__name__} にはメンバー '{name}' がありません")

    @staticmethod
    def _is_extension_member(target: object, name: str) -> bool:
        sequence = {
            "map", "filter", "each", "reduce", "fold", "find", "any", "all", "none",
            "sorted", "sortedBy", "distinct", "take", "skip", "zip", "flatten", "flatMap", "chunk",
            "window", "group", "groupBy", "sum", "contains",
        }
        text = {"trim", "upper", "lower", "split", "startsWith", "endsWith", "contains", "length"}
        mapping = {"keys", "values", "containsKey", "get"}
        setting = {"contains", "toList"}
        if isinstance(target, tuple): return name in sequence
        if isinstance(target, str): return name in text
        if isinstance(target, dict): return name in mapping
        if isinstance(target, frozenset): return name in setting
        return False

    def _binary(self, expr: ast.Binary) -> object:
        try:
            return self._binary_impl(expr)
        except (RuntimeLanguageError, SagaThrown, NativeFailure):
            raise
        except MemoryError as exc:
            raise RuntimeResourceError(
                "ホストが演算に必要なメモリを確保できませんでした",
                expr.operator.line, expr.operator.column,
                expr.operator.filename or self.filename,
                "Saga規格の固定上限ではありません。ホスト資源を増やすか入力を調整してください",
            ) from exc
        except ArithmeticError as exc:
            self._runtime_error(expr.operator, f"数値演算を完了できません: {type(exc).__name__}")

    def _binary_impl(self, expr: ast.Binary) -> object:
        kind = expr.operator.kind
        if kind is TokenKind.AND:
            left = bool(self._evaluate(expr.left)); return left and bool(self._evaluate(expr.right))
        if kind is TokenKind.OR:
            left = bool(self._evaluate(expr.left)); return left or bool(self._evaluate(expr.right))
        left, right = self._evaluate(expr.left), self._evaluate(expr.right)
        if kind is TokenKind.PLUS and isinstance(left, str): return left + right
        if kind in {TokenKind.EQUAL_EQUAL, TokenKind.BANG_EQUAL}:
            result = self._values_equal(left, right)
            return result if kind is TokenKind.EQUAL_EQUAL else not result
        if kind in {TokenKind.LESS, TokenKind.LESS_EQUAL, TokenKind.GREATER, TokenKind.GREATER_EQUAL}:
            if isinstance(left, str) and isinstance(right, str): a, b = left, right
            else: a, b = self._exact_pair(left, right)
            return {TokenKind.LESS: a < b, TokenKind.LESS_EQUAL: a <= b, TokenKind.GREATER: a > b, TokenKind.GREATER_EQUAL: a >= b}[kind]
        if kind is TokenKind.PERCENT:
            if right == 0: self._runtime_error(expr.operator, "0 で剰余を計算できません", diagnostic_id="SAGA-R102")
            # Saga defines remainder using an integer quotient truncated toward
            # zero. Do not use Python's `%` directly: Python uses floor division
            # for negative operands and would disagree with Native/WASM/C.
            q = abs(left) // abs(right)
            if (left < 0) != (right < 0): q = -q
            return left - q * right
        if kind is TokenKind.SLASH:
            if right == 0: self._runtime_error(expr.operator, "0 で割ることはできません", diagnostic_id="SAGA-R102")
            if isinstance(left, Decimal) or isinstance(right, Decimal):
                a, b = self._decimal_pair(left, right)
                with localcontext(self.context): return a / b
            return self._fraction(left) / self._fraction(right)
        if kind is TokenKind.POWER:
            exact_exponent = self._fraction(right)
            if exact_exponent.denominator != 1:
                self._runtime_error(expr.operator, "指数は整数値である必要があります")
            exponent = exact_exponent.numerator
            if isinstance(left, Decimal):
                if left == 0 and exponent < 0: self._runtime_error(expr.operator, "0 を負の指数で累乗できません")
                with localcontext(self.context): return left ** exponent
            base = self._fraction(left)
            if base == 0 and exponent < 0: self._runtime_error(expr.operator, "0 を負の指数で累乗できません")
            return base ** exponent
        if isinstance(left, Decimal) or isinstance(right, Decimal):
            a, b = self._decimal_pair(left, right)
            with localcontext(self.context):
                if kind is TokenKind.PLUS: return a + b
                if kind is TokenKind.MINUS: return a - b
                if kind is TokenKind.STAR: return a * b
        elif isinstance(left, Fraction) or isinstance(right, Fraction):
            a, b = self._fraction(left), self._fraction(right)
            if kind is TokenKind.PLUS: return a + b
            if kind is TokenKind.MINUS: return a - b
            if kind is TokenKind.STAR: return a * b
        else:
            if kind is TokenKind.PLUS: return left + right
            if kind is TokenKind.MINUS: return left - right
            if kind is TokenKind.STAR: return left * right
        raise AssertionError("unknown binary operator")

    def _call(self, expr: ast.Call) -> object:
        callee = self._evaluate(expr.callee); args = [self._evaluate(arg) for arg in expr.arguments]
        try: return self.invoke_callable(callee, args)
        except NativeFailure as exc: self._runtime_error(expr.paren, str(exc), diagnostic_id=getattr(exc, "diagnostic_id", None))
        except (SagaThrown, RuntimeLanguageError, RuntimeResourceError): raise
        except Exception as exc: self._runtime_error(expr.paren, f"呼び出し中にエラーが発生しました: {exc}")

    def invoke_callable(self, callee: object, args: list[object]) -> object:
        is_async = (
            isinstance(callee, UserFunction) and callee.declaration.async_
        ) or (
            isinstance(callee, BoundMethod) and callee.function.declaration.async_
        )
        if is_async:
            owner = self._callable_owner_interpreter(callee)
            owner.validate_task_call(callee, args)
            future = owner._task_pool.submit(owner.invoke_callable_isolated_direct, callee, args)
            if self._task_groups:
                self._task_groups[-1].append(future)
            return future
        return self.invoke_callable_direct(callee, args)

    def _callable_owner_interpreter(self, callee: object) -> "Interpreter":
        if isinstance(callee, UserFunction):
            if self.functions.get(callee.name) is callee:
                return self
            for child in self._module_interpreters:
                try: return child._callable_owner_interpreter(callee)
                except LookupError: pass
        elif isinstance(callee, BoundMethod):
            if callee.function.owner and callee.function.owner in self.classes:
                candidate = self.classes[callee.function.owner].methods.get(callee.function.name)
                if candidate is callee.function:
                    return self
            for child in self._module_interpreters:
                try: return child._callable_owner_interpreter(callee)
                except LookupError: pass
        raise LookupError("callable does not belong to this interpreter tree")

    def invoke_callable_direct(self, callee: object, args: list[object]) -> object:
        if isinstance(callee, BuiltinFunction): return self._builtin(callee.name, args)
        if isinstance(callee, NativeFunction): return callee(self, args)
        if isinstance(callee, UserFunction): return callee.call(self, args)
        if isinstance(callee, SagaClosure): return callee.call(self, args)
        if isinstance(callee, ExtensionMethod): return self._invoke_extension(callee, args)
        if isinstance(callee, BoundMethod): return callee.function.call(self, args, callee.receiver)
        if isinstance(callee, SagaClass): return self._construct(callee, args)
        if callable(callee): return callee(*args)
        raise NativeFailure(f"{self.format_value(callee)} は呼び出せません")

    def _invoke_extension(self, method: ExtensionMethod, args: list[object]) -> object:
        receiver, name = method.receiver, method.name
        if isinstance(receiver, tuple):
            if name == "map":
                self._extension_arity(name, args, 1)
                return tuple(self.invoke_callable(args[0], [item]) for item in receiver)
            if name == "filter":
                self._extension_arity(name, args, 1); result = []
                for item in receiver:
                    keep = self.invoke_callable(args[0], [item])
                    if not isinstance(keep, bool): raise NativeFailure("filter のブロックは bool を返す必要があります")
                    if keep: result.append(item)
                return tuple(result)
            if name == "each":
                self._extension_arity(name, args, 1)
                for item in receiver: self.invoke_callable(args[0], [item])
                return None
            if name in {"reduce", "fold"}:
                self._extension_arity(name, args, 2); result = args[0]
                for item in receiver: result = self.invoke_callable(args[1], [result, item])
                return result
            if name == "find":
                self._extension_arity(name, args, 1)
                for item in receiver:
                    found = self.invoke_callable(args[0], [item])
                    if not isinstance(found, bool): raise NativeFailure("find のブロックは bool を返す必要があります")
                    if found: return OptionValue.some(item)
                return OptionValue.none()
            if name in {"any", "all", "none"}:
                self._extension_arity(name, args, 1); values: list[bool] = []
                for item in receiver:
                    value = self.invoke_callable(args[0], [item])
                    if not isinstance(value, bool): raise NativeFailure(f"{name} のブロックは bool を返す必要があります")
                    values.append(value)
                if name == "any": return any(values)
                if name == "all": return all(values)
                return not any(values)
            if name == "sorted":
                self._extension_arity(name, args, 0)
                try: return tuple(sorted(receiver))
                except TypeError as exc: raise NativeFailure("比較できない値が含まれているため並べ替えできません") from exc
            if name == "sortedBy":
                self._extension_arity(name, args, 1)
                try: return tuple(sorted(receiver, key=lambda item: self.invoke_callable(args[0], [item])))
                except TypeError as exc: raise NativeFailure("sortedBy のキーを比較できません") from exc
            if name == "distinct":
                self._extension_arity(name, args, 0); result = []
                for item in receiver:
                    if not any(self._values_equal(item, existing) for existing in result): result.append(item)
                return tuple(result)
            if name == "take":
                self._extension_arity(name, args, 1); return receiver[:max(0, int(args[0]))]
            if name == "skip":
                self._extension_arity(name, args, 1); return receiver[max(0, int(args[0])):]
            if name == "zip":
                self._extension_arity(name, args, 1); return tuple((left, right) for left, right in zip(receiver, args[0]))
            if name == "flatten":
                self._extension_arity(name, args, 0); flattened = []
                for item in receiver:
                    if not isinstance(item, tuple): raise NativeFailure("flatten はリストのリストに使います")
                    flattened.extend(item)
                return tuple(flattened)
            if name == "flatMap":
                self._extension_arity(name, args, 1); flattened = []
                for item in receiver:
                    values = self.invoke_callable(args[0], [item])
                    if not isinstance(values, tuple): raise NativeFailure("flatMap のブロックはリストを返す必要があります")
                    flattened.extend(values)
                return tuple(flattened)
            if name == "chunk":
                self._extension_arity(name, args, 1); size = int(args[0])
                if size <= 0: raise NativeFailure("chunk のサイズは1以上にしてください")
                return tuple(tuple(receiver[i:i + size]) for i in range(0, len(receiver), size))
            if name == "window":
                self._extension_arity(name, args, 1); size = int(args[0])
                if size <= 0: raise NativeFailure("window のサイズは1以上にしてください")
                if size > len(receiver): return tuple()
                return tuple(tuple(receiver[i:i + size]) for i in range(0, len(receiver) - size + 1))
            if name == "group":
                self._extension_arity(name, args, 0); grouped: dict[object, list[object]] = {}
                for item in receiver:
                    self._require_hashable(item, "group の要素")
                    grouped.setdefault(item, []).append(item)
                return {key: tuple(values) for key, values in grouped.items()}
            if name == "groupBy":
                self._extension_arity(name, args, 1); grouped: dict[object, list[object]] = {}
                for item in receiver:
                    key = self.invoke_callable(args[0], [item])
                    self._require_hashable(key, "groupBy のキー")
                    grouped.setdefault(key, []).append(item)
                return {key: tuple(values) for key, values in grouped.items()}
            if name == "sum":
                self._extension_arity(name, args, 0); return self._builtin("sum", [receiver])
            if name == "contains":
                self._extension_arity(name, args, 1); return self._builtin("contains", [receiver, args[0]])

        if isinstance(receiver, str):
            if name == "trim": self._extension_arity(name, args, 0); return receiver.strip()
            if name == "upper": self._extension_arity(name, args, 0); return receiver.upper()
            if name == "lower": self._extension_arity(name, args, 0); return receiver.lower()
            if name == "split": self._extension_arity(name, args, 1); return tuple(receiver.split(str(args[0])))
            if name == "startsWith": self._extension_arity(name, args, 1); return receiver.startswith(str(args[0]))
            if name == "endsWith": self._extension_arity(name, args, 1); return receiver.endswith(str(args[0]))
            if name == "contains": self._extension_arity(name, args, 1); return str(args[0]) in receiver
            if name == "length": self._extension_arity(name, args, 0); return len(receiver)

        if isinstance(receiver, dict):
            if name == "keys": self._extension_arity(name, args, 0); return tuple(receiver.keys())
            if name == "values": self._extension_arity(name, args, 0); return tuple(receiver.values())
            if name == "containsKey": self._extension_arity(name, args, 1); return args[0] in receiver
            if name == "get":
                if len(args) not in {1, 2}: raise NativeFailure("map.get の引数は1個または2個必要です")
                if len(args) == 2: return receiver.get(args[0], args[1])
                return OptionValue.some(receiver[args[0]]) if args[0] in receiver else OptionValue.none()

        if isinstance(receiver, frozenset):
            if name == "contains": self._extension_arity(name, args, 1); return args[0] in receiver
            if name == "toList": self._extension_arity(name, args, 0); return tuple(sorted(receiver, key=self._stable_order_key))
        raise NativeFailure(f"{type(receiver).__name__} に拡張メソッド '{name}' はありません")

    @staticmethod
    def _extension_arity(name: str, args: list[object], count: int) -> None:
        if len(args) != count:
            raise NativeFailure(f"{name} の引数は {count} 個必要です")

    def _construct(self, klass: SagaClass, args: list[object]) -> SagaInstance:
        if klass.interface: raise NativeFailure(f"interface '{klass.name}' は作成できません")
        if klass.abstract: raise NativeFailure(f"abstract class '{klass.name}' は直接作成できません")
        fields = klass.constructor_fields()
        if len(args) != len(fields): raise NativeFailure(f"{klass.name} の引数は {len(fields)} 個必要です")
        type_vars = set(klass.declaration.type_params)
        for field, value in zip(fields, args):
            self.validate_native_value(parse_type(field.type_name, type_vars), value, f"{klass.name}.{field.name}")
        return SagaInstance(klass, {field.name: value for field, value in zip(fields, args)})

    def _builtin(self, name: str, args: list[object]) -> object:
        self._validate_builtin_arity(name, args)
        if name == "ok": return ResultValue.success(args[0])
        if name == "err": return ResultValue.failure(args[0])
        if name == "is_ok": return isinstance(args[0], ResultValue) and args[0].ok
        if name == "is_err": return isinstance(args[0], ResultValue) and not args[0].ok
        if name == "unwrap_ok":
            if not isinstance(args[0], ResultValue): raise NativeFailure("unwrap_ok は result に使います")
            if not args[0].ok: raise NativeFailure("err を unwrap_ok できません", "SAGA-R141")
            return args[0].value
        if name == "unwrap_err":
            if not isinstance(args[0], ResultValue): raise NativeFailure("unwrap_err は result に使います")
            if args[0].ok: raise NativeFailure("ok を unwrap_err できません", "SAGA-R142")
            return args[0].value
        if name == "unwrap_result_or":
            if not isinstance(args[0], ResultValue): raise NativeFailure("unwrap_result_or は result に使います")
            return args[0].value if args[0].ok else args[1]
        if name == "some": return OptionValue.some(args[0])
        if name == "none": return OptionValue.none()
        if name == "is_some": return isinstance(args[0], OptionValue) and args[0].present
        if name == "is_none": return isinstance(args[0], OptionValue) and not args[0].present
        if name == "unwrap":
            if not isinstance(args[0], OptionValue): raise NativeFailure("unwrap は option に使います")
            if not args[0].present: raise NativeFailure("none を unwrap できません。unwrap_or を使用してください", "SAGA-R104")
            return args[0].value
        if name == "unwrap_or":
            if not isinstance(args[0], OptionValue): raise NativeFailure("unwrap_or の1つ目は option です")
            return args[0].value if args[0].present else args[1]
        if name == "print": self.emit_output(" ".join(self.format_value(v) for v in args)); return None
        if name == "len": return len(args[0])
        if name == "text": return self.format_value(args[0])
        if name == "int":
            value = args[0]
            if isinstance(value, bool): raise NativeFailure("bool を int に変換できません")
            if isinstance(value, str):
                try: return int(value.strip(), 10)
                except ValueError as exc: raise NativeFailure(f"整数として読めません: {value}") from exc
            if isinstance(value, Fraction) and value.denominator != 1: raise NativeFailure("小数部分のある rational を int に変換できません")
            if isinstance(value, Decimal) and value != value.to_integral_value(): raise NativeFailure("小数部分のある decimal を int に変換できません")
            return int(value)
        if name == "decimal": return self._to_decimal(args[0])
        if name == "ratio":
            if args[1] == 0: raise NativeFailure("ratio の分母を0にはできません")
            return Fraction(args[0], args[1])
        if name == "abs": return abs(args[0])
        if name == "sqrt":
            value = self._to_decimal(args[0])
            if value < 0: raise NativeFailure("負の数の平方根は実数ではありません")
            with localcontext(self.context): return value.sqrt()
        if name == "round":
            value = self._to_decimal(args[0]); digits = int(args[1]); quantum = Decimal(1).scaleb(-digits)
            with localcontext(self.context): return value.quantize(quantum)
        if name == "floor": return int(self._to_decimal(args[0]).to_integral_value(rounding=ROUND_FLOOR))
        if name == "ceil": return int(self._to_decimal(args[0]).to_integral_value(rounding=ROUND_CEILING))
        if name in {"min", "max"}:
            a, b = args; chosen = a if (self._fraction(a) <= self._fraction(b)) == (name == "min") else b
            if isinstance(a, Decimal) or isinstance(b, Decimal): return self._to_decimal(chosen)
            if isinstance(a, Fraction) or isinstance(b, Fraction): return self._fraction(chosen)
            return chosen
        if name == "sum":
            if not args[0]: raise NativeFailure("空のリストの合計は型を確定できません")
            result = args[0][0]
            for value in args[0][1:]: result = self._numeric_add(result, value)
            return result
        if name == "mean":
            values = args[0]
            if not values: raise NativeFailure("空のリストの平均は計算できません")
            total = values[0]
            for value in values[1:]: total = self._numeric_add(total, value)
            if isinstance(total, Decimal):
                with localcontext(self.context): return total / Decimal(len(values))
            return self._fraction(total) / len(values)
        if name == "repeat":
            if len(args) == 2 and isinstance(args[1], SagaClosure):
                count = int(args[0])
                if count < 0: raise NativeFailure("repeat の回数は0以上にしてください")
                for _ in range(count): self.invoke_callable(args[1], [])
                return None
            count = int(args[1])
            if count < 0: raise NativeFailure("repeat の個数は0以上にしてください")
            return tuple(args[0] for _ in range(count))
        if name == "set_at":
            values, index, value = tuple(args[0]), int(args[1]), args[2]
            if index < 0 or index >= len(values): raise NativeFailure(f"添字 {index} は範囲外です")
            result = list(values); result[index] = value; return tuple(result)
        if name == "append": return tuple(args[0]) + (args[1],)
        if name == "prepend": return (args[1],) + tuple(args[0])
        if name == "get": return args[0][args[1]] if 0 <= args[1] < len(args[0]) else args[2]
        if name == "contains":
            if isinstance(args[0], tuple):
                return any(self._values_equal(args[1], item) for item in args[0])
            return args[1] in args[0]
        if name == "assert":
            if not args[0]: raise NativeFailure(str(args[1]) if len(args) == 2 else "assert に失敗しました", "SAGA-R105")
            return None
        if name == "precision":
            digits = int(args[0])
            if digits < 1: raise NativeFailure("precision は1以上にしてください")
            self.context.prec = digits; return None
        if name == "slice": return tuple(args[0][args[1]:args[2]])
        if name == "reverse": return tuple(reversed(args[0]))
        if name == "sort":
            try: return tuple(sorted(args[0]))
            except TypeError as exc: raise NativeFailure("比較できない値が含まれているため並べ替えできません") from exc
        if name == "unique":
            result = []
            for item in args[0]:
                if not any(self._values_equal(item, existing) for existing in result): result.append(item)
            return tuple(result)
        if name == "transform": return tuple(self.invoke_callable(args[0], [item]) for item in args[1])
        if name == "filter":
            result = []
            for item in args[1]:
                decision = self.invoke_callable(args[0], [item])
                if not isinstance(decision, bool): raise NativeFailure("filter の判定関数は bool を返す必要があります")
                if decision: result.append(item)
            return tuple(result)
        if name == "reduce":
            result = args[2]
            for item in args[1]: result = self.invoke_callable(args[0], [result, item])
            return result
        if name == "find":
            for item in args[1]:
                decision = self.invoke_callable(args[0], [item])
                if not isinstance(decision, bool): raise NativeFailure("find の判定関数は bool を返す必要があります")
                if decision: return item
            return args[2]
        if name in {"any", "all"}:
            decisions = []
            for item in args[1]:
                decision = self.invoke_callable(args[0], [item])
                if not isinstance(decision, bool): raise NativeFailure(f"{name} の判定関数は bool を返す必要があります")
                decisions.append(decision)
            return any(decisions) if name == "any" else all(decisions)
        if name == "split": return tuple(args[0].split(args[1]))
        if name == "join": return args[1].join(args[0])
        if name == "trim": return args[0].strip()
        if name == "upper": return args[0].upper()
        if name == "lower": return args[0].lower()
        if name == "replace": return args[0].replace(args[1], args[2])
        if name == "starts_with": return args[0].startswith(args[1])
        if name == "ends_with": return args[0].endswith(args[1])
        if name == "find_text": return args[0].find(args[1])
        if name == "substring": return args[0][args[1]:args[2]]
        if name == "map_of":
            result = {}
            for i in range(0, len(args), 2):
                self._require_hashable(args[i], "mapのキー")
                result[args[i]] = args[i + 1]
            return result
        if name == "map_get": return args[0].get(args[1], args[2])
        if name == "map_put": self._require_hashable(args[1], "mapのキー"); result = dict(args[0]); result[args[1]] = args[2]; return result
        if name == "map_remove": result = dict(args[0]); result.pop(args[1], None); return result
        if name == "map_keys": return tuple(args[0].keys())
        if name == "map_values": return tuple(args[0].values())
        if name == "map_contains": return args[1] in args[0]
        if name == "set_of":
            for item in args: self._require_hashable(item, "setの要素")
            return frozenset(args)
        if name == "set_add": self._require_hashable(args[1], "setの要素"); return frozenset(set(args[0]) | {args[1]})
        if name == "set_remove": return frozenset(set(args[0]) - {args[1]})
        if name == "set_contains": return args[1] in args[0]
        if name == "set_union": return frozenset(args[0] | args[1])
        if name == "set_intersection": return frozenset(args[0] & args[1])
        raise AssertionError(name)


    def _stable_order_key(self, value: object) -> tuple[str, str]:
        return (self.runtime_type_name(value), self.format_value(value))

    def _values_equal(self, left: object, right: object, seen: set[tuple[int, int]] | None = None) -> bool:
        if self._both_numeric(left, right):
            return self._numeric_equal(left, right)
        if left is right:
            return True
        if isinstance(left, EnumValue) and isinstance(right, EnumValue):
            if left.enum_name != right.enum_name or left.variant != right.variant or len(left.payload) != len(right.payload):
                return False
            if seen is None:
                seen = set()
            return all(self._values_equal(a, b, seen) for a, b in zip(left.payload, right.payload))
        if isinstance(left, SagaInstance) or isinstance(right, SagaInstance):
            # Objects have identity semantics.  This avoids mutable structural
            # equality and is cycle-safe by definition.
            return False
        if isinstance(left, SagaClass) or isinstance(right, SagaClass):
            return False
        if type(left) is not type(right):
            return False
        if seen is None:
            seen = set()
        pair = (id(left), id(right))
        if pair in seen:
            return True
        seen.add(pair)
        if isinstance(left, OptionValue):
            return left.present == right.present and (
                not left.present or self._values_equal(left.value, right.value, seen)
            )
        if isinstance(left, ResultValue):
            return left.ok == right.ok and self._values_equal(left.value, right.value, seen)
        if isinstance(left, tuple):
            return len(left) == len(right) and all(
                self._values_equal(a, b, seen) for a, b in zip(left, right)
            )
        if isinstance(left, dict):
            if len(left) != len(right) or left.keys() != right.keys():
                return False
            return all(self._values_equal(left[key], right[key], seen) for key in left)
        if isinstance(left, frozenset):
            return left == right
        return left == right

    def validate_task_call(self, callee: object, args: list[object]) -> None:
        """Validate values crossing an isolated-task boundary."""
        if isinstance(callee, UserFunction) and self.functions.get(callee.name) is not callee:
            raise NativeFailure(
                "task.spawn/submit に渡せるSaga関数はトップレベル関数です。"
                "ローカル関数やローカル状態を捕捉するクロージャはSendではありません"
            )
        self._assert_sendable(callee, "task function", allow_callable=True)
        for index, value in enumerate(args):
            self._assert_sendable(value, f"task argument {index + 1}")

    @staticmethod
    def _require_hashable(value: object, label: str) -> None:
        try:
            hash(value)
        except (TypeError, ValueError) as exc:
            raise NativeFailure(f"{label}にはハッシュ可能な値が必要です") from exc

    @staticmethod
    def _validate_builtin_arity(name: str, args: list[object]) -> None:
        fixed = {
            "len": 1, "text": 1, "int": 1, "decimal": 1, "ratio": 2,
            "abs": 1, "sqrt": 1, "round": 2, "min": 2, "max": 2,
            "sum": 1, "mean": 1, "append": 2, "prepend": 2, "get": 3,
            "contains": 2, "precision": 1, "floor": 1, "ceil": 1,
            "slice": 3, "reverse": 1, "sort": 1, "unique": 1,
            "transform": 2, "filter": 2, "reduce": 3, "find": 3,
            "any": 2, "all": 2, "split": 2, "join": 2, "trim": 1,
            "upper": 1, "lower": 1, "replace": 3, "starts_with": 2,
            "ends_with": 2, "find_text": 2, "substring": 3,
            "map_get": 3, "map_put": 3, "map_remove": 2, "map_keys": 1,
            "map_values": 1, "map_contains": 2, "set_add": 2,
            "set_remove": 2, "set_contains": 2, "set_union": 2,
            "set_intersection": 2, "repeat": 2, "set_at": 3,
            "some": 1, "none": 0, "is_some": 1, "is_none": 1,
            "unwrap": 1, "unwrap_or": 2, "ok":1, "err":1, "is_ok":1, "is_err":1, "unwrap_ok":1, "unwrap_err":1, "unwrap_result_or":2,
        }
        if name == "assert":
            if len(args) not in {1, 2}: raise NativeFailure("assert の引数は1個または2個必要です")
            return
        if name in {"print", "map_of", "set_of"}:
            if name == "map_of" and len(args) % 2:
                raise NativeFailure("map_of は key, value の組を渡してください")
            return
        expected = fixed.get(name)
        if expected is not None and len(args) != expected:
            raise NativeFailure(f"{name} の引数は {expected} 個必要です")

    def emit_output(self, text: str) -> None:
        # A print/console.write call is one atomic output event across tasks.
        with self._output_lock:
            self.output(text)

    def spawn_callable(self, callee: object, args: list[object]) -> Future:
        # Saga tasks use snapshot isolation. Values crossing the task boundary
        # must be structurally copyable Saga values; native resources such as
        # sockets, database connections, widgets and futures are not Send.
        self.validate_task_call(callee, args)
        return self._task_pool.submit(self.invoke_callable_isolated, callee, args)

    def _assert_sendable(self, value: object, path: str, *, allow_callable: bool = False, seen: set[int] | None = None) -> None:
        if seen is None:
            seen = set()
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if value is None or isinstance(value, (bool, int, Decimal, Fraction, str, bytes, datetime, timedelta, SagaRange, ErrorValue)):
            return
        if isinstance(value, OptionValue):
            if value.present:
                self._assert_sendable(value.value, f"{path}.value", seen=seen)
            return
        if isinstance(value, ResultValue):
            branch = "ok" if value.ok else "err"
            self._assert_sendable(value.value, f"{path}.{branch}", seen=seen)
            return
        if isinstance(value, tuple):
            for index, item in enumerate(value):
                self._assert_sendable(item, f"{path}[{index}]", seen=seen)
            return
        if isinstance(value, frozenset):
            for item in value:
                self._assert_sendable(item, f"{path} set item", seen=seen)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self._assert_sendable(key, f"{path} key", seen=seen)
                self._assert_sendable(item, f"{path}[{key!r}]", seen=seen)
            return
        if isinstance(value, SagaInstance):
            for name, item in value.values.items():
                self._assert_sendable(item, f"{path}.{name}", seen=seen)
            return
        if allow_callable and isinstance(value, (BuiltinFunction, UserFunction, NativeFunction, SagaClass, BoundMethod)):
            if isinstance(value, BoundMethod):
                self._assert_sendable(value.receiver, f"{path} receiver", seen=seen)
            return
        if isinstance(value, Future):
            raise NativeFailure(f"{path} はFutureなので別タスクへ渡せません")
        raise NativeFailure(
            f"{path} は共有可能なSaga値ではありません ({type(value).__name__})。"
            "ファイル、DB、ソケット、GUI、プラグイン等のネイティブ資源はタスク間で共有せず、各タスク内で開いてください"
        )

    def _assert_process_sendable(self, value: object, path: str, seen: set[int] | None = None) -> None:
        """Validate a value crossing a CPU-process boundary.

        Process workers intentionally accept only value-semantic data.  Saga
        object instances and native resources stay in their owning process.
        This is a semantic isolation rule, not a numeric resource ceiling.
        """
        if seen is None:
            seen = set()
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if value is None or isinstance(value, (bool, int, Decimal, Fraction, str, bytes, datetime, timedelta, SagaRange, ErrorValue)):
            return
        if isinstance(value, OptionValue):
            if value.present:
                self._assert_process_sendable(value.value, f"{path}.value", seen)
            return
        if isinstance(value, ResultValue):
            branch = "ok" if value.ok else "err"
            self._assert_process_sendable(value.value, f"{path}.{branch}", seen)
            return
        if isinstance(value, tuple):
            for index, item in enumerate(value):
                self._assert_process_sendable(item, f"{path}[{index}]", seen)
            return
        if isinstance(value, frozenset):
            for item in value:
                self._assert_process_sendable(item, f"{path} set item", seen)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self._assert_process_sendable(key, f"{path} key", seen)
                self._assert_process_sendable(item, f"{path}[{key!r}]", seen)
            return
        raise NativeFailure(
            f"{path} はCPU並列ワーカーへコピーできません ({type(value).__name__})。"
            "CPU並列処理では数値、text、bytes、option、list、map、set等の値を渡してください"
        )

    def prepare_cpu_job(self, callee: object, args: list[object]) -> dict:
        if not isinstance(callee, UserFunction) or callee.owner is not None:
            raise NativeFailure("CPU並列処理にはトップレベルのSaga関数を指定してください")
        if self.program is None:
            raise NativeFailure("CPU並列処理には読み込み済みのSagaプログラムが必要です")
        for index, value in enumerate(args):
            self._assert_process_sendable(value, f"parallel argument {index + 1}")
        globals_snapshot: dict[str, object] = {}
        modules: list[str] = []
        skip_types = (BuiltinFunction, UserFunction, SagaClass)
        for name, cell in self.globals.values.items():
            if isinstance(cell.value, NativeModule):
                modules.append(name)
                continue
            if isinstance(cell.value, skip_types):
                continue
            try:
                self._assert_process_sendable(cell.value, f"global '{name}'")
            except NativeFailure:
                # Native resources and object identities are intentionally not
                # inherited by process workers. Accessing one from a worker
                # therefore fails as an undefined/nonportable dependency.
                continue
            globals_snapshot[name] = copy.deepcopy(cell.value)
        return {
            "filename": self.filename,
            "program": self.program,
            "function": callee.name,
            "args": copy.deepcopy(tuple(args)),
            "globals": globals_snapshot,
            "modules": tuple(modules),
            "precision": self.context.prec,
        }

    def invoke_callable_threadsafe(self, callee: object, args: list[object]) -> object:
        with self._call_lock: return self.invoke_callable(callee, args)

    def invoke_callable_isolated(self, callee: object, args: list[object]) -> object:
        snapshot_memo: dict[int, object] = {}
        fork = self.fork(snapshot_memo)
        try:
            mapped = fork._map_callable_from(self, callee, snapshot_memo)
            mapped_args = [self._snapshot_value_to(fork, value, snapshot_memo) for value in args]
            result = fork.invoke_callable(mapped, mapped_args)
            fork._assert_sendable(result, "task result")
            return fork._snapshot_value_to(self, result)
        finally:
            fork.close()

    def invoke_callable_isolated_direct(self, callee: object, args: list[object]) -> object:
        """Execute an async function once in an isolated interpreter snapshot.

        The direct call is important: mapping an ``async fn`` and then routing it
        through ``invoke_callable`` would create a nested future instead of running
        the body in the worker.
        """
        snapshot_memo: dict[int, object] = {}
        fork = self.fork(snapshot_memo)
        try:
            mapped = fork._map_callable_from(self, callee, snapshot_memo)
            mapped_args = [self._snapshot_value_to(fork, value, snapshot_memo) for value in args]
            result = fork.invoke_callable_direct(mapped, mapped_args)
            fork._assert_sendable(result, "async result")
            return fork._snapshot_value_to(self, result)
        finally:
            fork.close()

    def _snapshot_value_to(
        self,
        target: "Interpreter",
        value: object,
        memo: dict[int, object] | None = None,
    ) -> object:
        """Copy one Send value between isolated interpreter worlds.

        ``copy.deepcopy`` alone retains the source interpreter's class objects.
        This routine remaps class instances by class name and preserves cycles.
        """
        if memo is None:
            memo = {}
        identity = id(value)
        if identity in memo:
            return memo[identity]
        if value is None or isinstance(value, (bool, int, Decimal, Fraction, str, bytes, datetime, timedelta, SagaRange, ErrorValue)):
            return copy.deepcopy(value)
        if isinstance(value, OptionValue):
            copied = object.__new__(OptionValue)
            memo[identity] = copied
            object.__setattr__(copied, "present", value.present)
            payload = self._snapshot_value_to(target, value.value, memo) if value.present else None
            object.__setattr__(copied, "value", payload)
            return copied
        if isinstance(value, ResultValue):
            copied = object.__new__(ResultValue)
            memo[identity] = copied
            object.__setattr__(copied, "ok", value.ok)
            object.__setattr__(copied, "value", self._snapshot_value_to(target, value.value, memo))
            return copied
        if isinstance(value, tuple):
            placeholder: list[object] = []
            memo[identity] = placeholder
            result = tuple(self._snapshot_value_to(target, item, memo) for item in value)
            memo[identity] = result
            return result
        if isinstance(value, dict):
            result: dict[object, object] = {}
            memo[identity] = result
            for key, item in value.items():
                result[self._snapshot_value_to(target, key, memo)] = self._snapshot_value_to(target, item, memo)
            return result
        if isinstance(value, frozenset):
            result = frozenset(self._snapshot_value_to(target, item, memo) for item in value)
            memo[identity] = result
            return result
        if isinstance(value, SagaInstance):
            target_class = target.classes.get(value.klass.name)
            if target_class is None:
                raise NativeFailure(f"task value class '{value.klass.name}' is not available in the target task")
            result = SagaInstance(target_class, {})
            memo[identity] = result
            for name, item in value.values.items():
                result.values[name] = self._snapshot_value_to(target, item, memo)
            return result
        raise NativeFailure(f"task snapshot cannot copy {type(value).__name__}")

    def _map_callable_from(
        self, source: "Interpreter", callee: object, memo: dict[int, object] | None = None
    ) -> object:
        if isinstance(callee, UserFunction): return self.functions[callee.name]
        if isinstance(callee, BuiltinFunction): return self.globals.get_name(callee.name)
        if isinstance(callee, NativeFunction): return callee
        if isinstance(callee, SagaClass): return self.classes[callee.name]
        if isinstance(callee, BoundMethod):
            receiver = source._snapshot_value_to(self, callee.receiver, memo)
            target_class = receiver.klass
            return BoundMethod(receiver, target_class.methods[callee.function.name])
        return callee

    def fork(self, snapshot_memo: dict[int, object] | None = None) -> "Interpreter":
        fork = Interpreter(self.filename, output=self.output, precision=self.context.prec, step_limit=self.step_limit, capabilities=self.capabilities)
        fork._output_lock = self._output_lock
        if self.program is None: return fork
        fork.program = self.program; fork._register_declarations(self.program)
        skip_types = (BuiltinFunction, UserFunction, SagaClass, NativeModule)
        memo = snapshot_memo if snapshot_memo is not None else {}
        for name, cell in self.globals.values.items():
            if isinstance(cell.value, skip_types):
                if isinstance(cell.value, NativeModule) and name not in fork.globals.values: fork.globals.define(name, cell.value, False)
                continue
            try:
                self._assert_sendable(cell.value, f"global '{name}'")
            except NativeFailure:
                # Non-Send native resources and futures are deliberately not
                # captured by the task snapshot. Direct arguments are rejected
                # before scheduling; an attempted global access fails as an
                # undefined name inside the isolated task.
                continue
            # Remap Saga class instances into the fork's class universe.
            # copy.deepcopy() preserves the source SagaClass/UserFunction
            # graph and can accidentally copy ThreadPoolExecutor locks.
            value = self._snapshot_value_to(fork, cell.value, memo)
            if name in fork.globals.values: fork.globals.values[name] = Cell(value, cell.mutable, cell.contract)
            else: fork.globals.define(name, value, cell.mutable, cell.contract)
        return fork

    def _current_owner(self) -> str | None:
        return self._owner_stack[-1] if self._owner_stack else None

    def _runtime_type_of(self, value: object) -> Type | None:
        """Best-effort Saga type for a runtime value.

        This is deliberately narrower than static inference.  Its purpose is to
        materialize generic type variables at dynamic/hosted boundaries so a
        value that arrived through ``any`` is checked against the concrete Saga
        contract before ordinary operators can observe it.
        """
        if value is None: return UNIT
        if isinstance(value, bool): return BOOL
        if isinstance(value, int): return INT
        if isinstance(value, Decimal): return DECIMAL
        if isinstance(value, Fraction): return RATIONAL
        if isinstance(value, str): return TEXT
        if isinstance(value, bytes): return BYTES
        if isinstance(value, datetime): return DATETIME
        if isinstance(value, timedelta): return DURATION
        if isinstance(value, ErrorValue): return ERROR
        if isinstance(value, EnumValue): return Type(f"object:{value.enum_name}")
        if isinstance(value, EnumType): return Type(f"enumtype:{value.qualified_name}")
        if isinstance(value, SagaInstance): return Type(f"object:{value.klass.name}")
        if isinstance(value, SagaClass): return CLASS_VALUE
        if isinstance(value, tuple):
            if not value: return Type("list", (ANY,))
            item_types = [self._runtime_type_of(item) for item in value]
            first = item_types[0]
            return Type("list", (first if first is not None and all(item == first for item in item_types) else ANY,))
        if isinstance(value, frozenset):
            if not value: return Type("set", (ANY,))
            item_types = [self._runtime_type_of(item) for item in value]
            first = item_types[0]
            return Type("set", (first if first is not None and all(item == first for item in item_types) else ANY,))
        if isinstance(value, dict):
            if not value: return Type("map", (ANY, ANY))
            key_types = [self._runtime_type_of(key) for key in value]
            value_types = [self._runtime_type_of(item) for item in value.values()]
            key = key_types[0]
            item = value_types[0]
            return Type(
                "map",
                (
                    key if key is not None and all(current == key for current in key_types) else ANY,
                    item if item is not None and all(current == item for current in value_types) else ANY,
                ),
            )
        if isinstance(value, OptionValue):
            inner = self._runtime_type_of(value.value) if value.present else ANY
            return Type("option", (inner or ANY,))
        if isinstance(value, ResultValue):
            inner = self._runtime_type_of(value.value) or ANY
            return Type("result", (inner, ANY) if value.ok else (ANY, inner))
        callable_type = self._runtime_callable_type(value)
        if callable_type is not None:
            return callable_type
        return None

    def _bind_runtime_typevars(self, pattern: Type, value: object, mapping: dict[str, Type]) -> None:
        """Bind type variables in *pattern* from one runtime value.

        Static generic inference already rejects inconsistent ordinary calls.
        The runtime copy exists specifically so contracts that contain a type
        variable can be reified after an ``any``/native boundary.
        """
        if pattern.name == "typeapply" and pattern.args:
            constructor, *arguments = pattern.args
            actual = self._runtime_type_of(value)
            if (
                actual is None
                or actual.name == "fn"
                or len(arguments) != len(actual.args)
                or not is_typevar(constructor)
            ):
                return
            name = typevar_name(constructor)
            candidate = TYPECTOR(actual.name)
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = candidate
            elif existing != candidate:
                return
            for expected_arg, actual_arg in zip(arguments, actual.args):
                unify(expected_arg, actual_arg, mapping)
            return
        if is_typevar(pattern):
            name = typevar_name(pattern)
            if name in mapping:
                return
            actual = self._runtime_type_of(value)
            if actual is not None and actual != ANY:
                mapping[name] = actual
            return
        if pattern.name == "list" and isinstance(value, tuple) and pattern.args:
            for item in value:
                self._bind_runtime_typevars(pattern.args[0], item, mapping)
            return
        if pattern.name == "set" and isinstance(value, frozenset) and pattern.args:
            for item in value:
                self._bind_runtime_typevars(pattern.args[0], item, mapping)
            return
        if pattern.name == "map" and isinstance(value, dict) and len(pattern.args) == 2:
            for key, item in value.items():
                self._bind_runtime_typevars(pattern.args[0], key, mapping)
                self._bind_runtime_typevars(pattern.args[1], item, mapping)
            return
        if pattern.name == "option" and isinstance(value, OptionValue) and value.present and pattern.args:
            self._bind_runtime_typevars(pattern.args[0], value.value, mapping)
            return
        if pattern.name == "result" and isinstance(value, ResultValue) and len(pattern.args) == 2:
            self._bind_runtime_typevars(pattern.args[0] if value.ok else pattern.args[1], value.value, mapping)
            return
        if pattern.name == "fn":
            actual = self._runtime_callable_type(value)
            if actual is not None:
                unify(pattern, actual, mapping)

    def _runtime_callable_type(self, value: object) -> Type | None:
        target = value.function if isinstance(value, BoundMethod) else value
        if isinstance(target, UserFunction):
            names = set(target.captured_type_bindings)
            names.update(target.declaration.type_params)
            if target.owner and target.owner in self.classes:
                names.update(self.classes[target.owner].declaration.type_params)
            try:
                params = [parse_type(parameter.type_name, names) for parameter in target.declaration.parameters]
                result = parse_type(target.declaration.return_type, names) if target.declaration.return_type else ANY
            except ValueError:
                return None
            signature = FUNCTION(params, result)
            return substitute(signature, target.captured_type_bindings)
        if isinstance(target, NativeFunction) and not target.signature.variadic:
            return FUNCTION(list(target.signature.params), target.signature.returns)
        if isinstance(target, SagaClass):
            names = set(target.declaration.type_params)
            try:
                params = [parse_type(field.type_name, names) for field in target.constructor_fields()]
            except ValueError:
                return None
            return FUNCTION(params, Type(f"object:{target.name}"))
        return None

    def _runtime_function_contract_matches(self, expected: Type, value: object) -> bool:
        # Closures are contextually typed by the checker but do not carry their
        # inferred return type in the runtime AST.  We can still enforce arity;
        # the return value is checked at the next concrete boundary.
        if isinstance(value, SagaClosure):
            explicit = value.expression.parameters
            return len(expected.args) == (len(explicit) if explicit else min(len(expected.args), 1))
        actual = self._runtime_callable_type(value)
        if actual is None:
            return isinstance(value, BuiltinFunction) or callable(value)
        # Generic functions may be specialized to the expected function type at
        # this dynamic boundary (e.g. id[T] -> fn[int,int]).
        mapping: dict[str, Type] = {}
        if actual.name == "fn" and len(actual.args) == len(expected.args):
            for pattern, concrete in zip(actual.args, expected.args):
                if not unify(pattern, concrete, mapping):
                    return False
            if actual.result is not None and expected.result is not None:
                unify(actual.result, expected.result, mapping)
            actual = substitute(actual, mapping)
        return is_assignable(expected, actual)

    def validate_native_value(self, expected: Type, value: object, label: str) -> None:
        if expected == ANY or is_typevar(expected):
            return
        if expected.name.startswith("native:"):
            from .stdlib.modules import native_value_matches
            kind = expected.name.split(":", 1)[1]
            if not native_value_matches(kind, value):
                raise NativeFailure(f"{label}のネイティブ資源型が不正です。必要: {expected}、実際: {self.runtime_type_name(value)}", "SAGA-T103")
            return
        ok = False
        if expected == UNIT: ok = value is None
        elif expected == BOOL: ok = isinstance(value, bool)
        elif expected == INT: ok = isinstance(value, int) and not isinstance(value, bool)
        elif expected == DECIMAL: ok = isinstance(value, (int, Decimal, Fraction)) and not isinstance(value, bool)
        elif expected == RATIONAL: ok = isinstance(value, (int, Fraction)) and not isinstance(value, bool)
        elif expected == TEXT: ok = isinstance(value, str)
        elif expected == BYTES: ok = isinstance(value, bytes)
        elif expected == DATETIME: ok = isinstance(value, datetime)
        elif expected == DURATION: ok = isinstance(value, timedelta)
        elif expected == ERROR: ok = isinstance(value, ErrorValue)
        elif expected == CLASS_VALUE: ok = isinstance(value, SagaClass)
        elif expected.name == "list":
            ok = isinstance(value, tuple) and all(self._runtime_value_matches(expected.args[0], item) for item in value)
        elif expected.name == "map":
            ok = isinstance(value, dict) and all(self._runtime_value_matches(expected.args[0], key) and self._runtime_value_matches(expected.args[1], item) for key, item in value.items())
        elif expected.name == "set":
            ok = isinstance(value, frozenset) and all(self._runtime_value_matches(expected.args[0], item) for item in value)
        elif expected.name == "option":
            ok = isinstance(value, OptionValue) and (not value.present or self._runtime_value_matches(expected.args[0], value.value))
        elif expected.name == "result":
            ok = isinstance(value, ResultValue) and self._runtime_value_matches(expected.args[0] if value.ok else expected.args[1], value.value)
        elif expected.name == "future":
            ok = isinstance(value, Future)
        elif expected.name == "fn":
            ok = self._runtime_function_contract_matches(expected, value)
        elif expected.name.startswith("module:"):
            ok = isinstance(value, NativeModule)
        elif expected.name.startswith("object:"):
            class_name = expected.name.split(":", 1)[1]
            if isinstance(value, EnumValue):
                ok = value.enum_name == class_name or value.enum_name.endswith("." + class_name) or class_name.endswith("." + value.enum_name)
            else:
                ok = isinstance(value, SagaInstance) and self._runtime_class_is_subtype(value.klass, class_name)
        if not ok:
            raise NativeFailure(f"{label}の型が不正です。必要: {expected}、実際: {self.runtime_type_name(value)}")

    def _runtime_value_matches(self, expected: Type, value: object) -> bool:
        try:
            self.validate_native_value(expected, value, "値")
            return True
        except NativeFailure:
            return False

    @staticmethod
    def _runtime_class_is_subtype(actual: SagaClass, expected_name: str) -> bool:
        current: SagaClass | None = actual
        while current is not None:
            qualified = f"{current.module_namespace}.{current.name}" if current.module_namespace else current.name
            if current.name == expected_name or qualified == expected_name:
                return True
            if any(
                type_name.split("[", 1)[0] == expected_name
                or (current.module_namespace and f"{current.module_namespace}.{type_name.split('[', 1)[0]}" == expected_name)
                for type_name in current.declaration.interfaces
            ):
                return True
            current = current.base
        return False

    def runtime_type_name(self, value: object) -> str:
        if isinstance(value, EnumValue): return value.enum_name
        if isinstance(value, EnumType): return f"enum[{value.qualified_name}]"
        if isinstance(value, SagaInstance): return value.klass.name
        if isinstance(value, SagaClass): return f"class[{value.name}]"
        if isinstance(value, bool): return "bool"
        if isinstance(value, int): return "int"
        if isinstance(value, Decimal): return "decimal"
        if isinstance(value, Fraction): return "rational"
        if isinstance(value, str): return "text"
        if isinstance(value, bytes): return "bytes"
        if isinstance(value, tuple): return "list"
        if isinstance(value, dict): return "map"
        if isinstance(value, frozenset): return "set"
        if isinstance(value, datetime): return "datetime"
        if isinstance(value, timedelta): return "duration"
        if isinstance(value, ErrorValue): return "error"
        if isinstance(value, OptionValue): return "option"
        if isinstance(value, ResultValue): return "result"
        return type(value).__name__

    def reflect_fields(self, value: object) -> list[str]:
        if isinstance(value, SagaInstance): return [name for name, field in value.klass.fields.items() if not field.private]
        if isinstance(value, SagaClass): return [name for name, field in value.fields.items() if not field.private]
        if isinstance(value, dict): return [str(k) for k in value]
        return []

    def reflect_methods(self, value: object) -> list[str]:
        if isinstance(value, SagaInstance): return list(value.klass.methods)
        if isinstance(value, SagaClass): return list(value.methods)
        return []

    def reflect_annotations(self, value: object) -> dict[str, tuple[object, ...]]:
        if isinstance(value, (SagaInstance, SagaClass)):
            klass = value.klass if isinstance(value, SagaInstance) else value
            return dict(klass.annotations)
        if isinstance(value, (UserFunction, BoundMethod)):
            fn = value.function if isinstance(value, BoundMethod) else value
            return dict(fn.annotations)
        return {}

    def reflect_get(self, value: object, name: str) -> object:
        if isinstance(value, SagaInstance):
            if name in value.values:
                field = value.klass.fields.get(name)
                if field and field.private:
                    raise NativeFailure(f"private フィールド '{name}' はリフレクションで取得できません")
                return value.values[name]
            if name in value.klass.methods: return BoundMethod(value, value.klass.methods[name])
        if isinstance(value, dict): return value.get(name)
        raise NativeFailure(f"'{name}' を取得できません")

    def class_of(self, value: object) -> SagaClass:
        if isinstance(value, SagaInstance): return value.klass
        if isinstance(value, SagaClass): return value
        raise NativeFailure("Sagaクラスまたはオブジェクトが必要です")

    def orm_create_table(self, conn, klass: object) -> None:
        if not isinstance(klass, SagaClass): raise NativeFailure("orm.create_table の2つ目はSagaクラスです")
        if klass.interface or klass.abstract: raise NativeFailure("interfaceまたはabstract classはORM表にできません")
        table = self._orm_table_name(klass); columns = []
        for field in klass.fields.values():
            sql_type = self._orm_sql_type(field.type_name)
            extra = " PRIMARY KEY" if field.name == "id" and sql_type == "INTEGER" else ""
            columns.append(f'"{self._safe_identifier(field.name)}" {sql_type}{extra}')
        if not columns: raise NativeFailure("ORMクラスには1つ以上のフィールドが必要です")
        auto_commit = not conn.in_transaction
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(columns)})')
        if auto_commit: conn.commit()

    def orm_insert(self, conn, instance: object) -> int:
        if not isinstance(instance, SagaInstance): raise NativeFailure("orm.insert の2つ目はSagaオブジェクトです")
        klass = instance.klass; table = self._orm_table_name(klass); names = list(klass.fields)
        placeholders = ",".join("?" for _ in names); cols = ",".join(f'"{self._safe_identifier(n)}"' for n in names)
        values = [self._orm_value(instance.values[n]) for n in names]
        auto_commit = not conn.in_transaction
        cur = conn.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})', values)
        if auto_commit: conn.commit()
        return int(cur.lastrowid)

    def orm_all(self, conn, klass: object) -> tuple[SagaInstance, ...]:
        if not isinstance(klass, SagaClass): raise NativeFailure("orm.all の2つ目はSagaクラスです")
        table = self._orm_table_name(klass); rows = conn.execute(f'SELECT * FROM "{table}"').fetchall(); fields = list(klass.fields.values())
        return tuple(SagaInstance(klass, {field.name: self._orm_decode(row[field.name], field.type_name) for field in fields}) for row in rows)

    def _orm_table_name(self, klass: SagaClass) -> str:
        annotation = klass.annotations.get("table")
        raw = str(annotation[0]) if annotation else klass.name.lower()
        return self._safe_identifier(raw)

    @staticmethod
    def _safe_identifier(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value): raise NativeFailure(f"SQL識別子が安全ではありません: {value}")
        return value

    @staticmethod
    def _orm_sql_type(type_name: str) -> str:
        normalized = type_name.replace(" ", "")
        if normalized.lower().startswith("option[") and normalized.endswith("]"):
            normalized = normalized[7:-1]
        base = normalized.split("[", 1)[0].lower()
        return {"int":"INTEGER", "bool":"INTEGER", "decimal":"TEXT", "rational":"TEXT", "bytes":"BLOB", "text":"TEXT", "string":"TEXT"}.get(base, "TEXT")

    @staticmethod
    def _orm_value(value: object) -> object:
        if isinstance(value, OptionValue): return Interpreter._orm_value(value.value) if value.present else None
        if isinstance(value, bool): return int(value)
        if isinstance(value, (Decimal, Fraction)): return str(value)
        return value

    @staticmethod
    def _orm_decode(value: object, type_name: str) -> object:
        normalized = type_name.replace(" ", "")
        if normalized.lower().startswith("option[") and normalized.endswith("]"):
            inner = normalized[7:-1]
            return OptionValue.none() if value is None else OptionValue.some(Interpreter._orm_decode(value, inner))
        base = normalized.split("[", 1)[0].lower()
        if value is None: raise NativeFailure("SQL NULL はSagaの非null型へ変換できません。option[T]を使用してください")
        if base == "bool": return bool(value)
        if base == "int": return int(value)
        if base == "decimal": return Decimal(str(value))
        if base == "rational": return Fraction(str(value))
        if base == "bytes": return bytes(value)
        if base in {"text", "string"}: return str(value)
        return value

    def to_plain(self, value: object) -> object:
        if isinstance(value, SagaInstance):
            return {k: self.to_plain(v) for k, v in value.values.items() if not value.klass.fields[k].private}
        if isinstance(value, OptionValue): return self.to_plain(value.value) if value.present else None
        if isinstance(value, tuple): return [self.to_plain(v) for v in value]
        if isinstance(value, frozenset): return [self.to_plain(v) for v in value]
        if isinstance(value, dict): return {str(k): self.to_plain(v) for k, v in value.items()}
        if isinstance(value, Decimal): return str(value)
        if isinstance(value, Fraction): return f"{value.numerator}/{value.denominator}"
        if isinstance(value, bytes):
            import base64
            return {"$bytes": base64.b64encode(value).decode("ascii")}
        if isinstance(value, datetime): return value.isoformat()
        if isinstance(value, timedelta):
            return str(Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1_000_000))
        if isinstance(value, ErrorValue): return {"message": value.message, "kind": value.kind}
        return value

    def _numeric_add(self, left: object, right: object) -> object:
        if isinstance(left, Decimal) or isinstance(right, Decimal):
            a, b = self._decimal_pair(left, right)
            with localcontext(self.context): return a + b
        if isinstance(left, Fraction) or isinstance(right, Fraction): return self._fraction(left) + self._fraction(right)
        return left + right

    @staticmethod
    def _both_numeric(a: object, b: object) -> bool:
        return isinstance(a, (int, Decimal, Fraction)) and not isinstance(a, bool) and isinstance(b, (int, Decimal, Fraction)) and not isinstance(b, bool)

    def _numeric_equal(self, a: object, b: object) -> bool: return self._fraction(a) == self._fraction(b)
    def _exact_pair(self, a: object, b: object) -> tuple[Fraction, Fraction]: return self._fraction(a), self._fraction(b)

    @staticmethod
    def _fraction(value: object) -> Fraction:
        if isinstance(value, Fraction): return value
        if isinstance(value, Decimal): return Fraction(value)
        return Fraction(int(value), 1)

    def _to_decimal(self, value: object) -> Decimal:
        if isinstance(value, Decimal): return value
        fraction = self._fraction(value)
        with localcontext(self.context): return Decimal(fraction.numerator) / Decimal(fraction.denominator)

    def _decimal_pair(self, a: object, b: object) -> tuple[Decimal, Decimal]: return self._to_decimal(a), self._to_decimal(b)

    def format_value(self, value: object) -> str:
        if value is True: return "true"
        if value is False: return "false"
        if value is None: return "unit"
        if isinstance(value, tuple): return "[" + ", ".join(self.format_value(v) for v in value) + "]"
        if isinstance(value, dict): return "{" + ", ".join(f"{self.format_value(k)}: {self.format_value(v)}" for k,v in value.items()) + "}"
        if isinstance(value, frozenset): return "set{" + ", ".join(self.format_value(v) for v in sorted(value, key=self._stable_order_key)) + "}"
        if isinstance(value, bytes): return f"bytes[{len(value)}]"
        if isinstance(value, Fraction): return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
        if isinstance(value, Decimal):
            text = format(value, "f")
            if "." in text: text = text.rstrip("0").rstrip(".")
            return text or "0"
        if isinstance(value, datetime): return value.isoformat()
        if isinstance(value, timedelta):
            seconds = Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1_000_000)
            return f"duration({seconds}s)"
        if isinstance(value, ErrorValue): return f"{value.kind}: {value.message}"
        if isinstance(value, OptionValue): return f"some({self.format_value(value.value)})" if value.present else "none"
        if isinstance(value, ResultValue): return f"ok({self.format_value(value.value)})" if value.ok else f"err({self.format_value(value.value)})"
        if isinstance(value, SagaInstance):
            # Display is an observable serialization boundary. Never expose
            # private state through print(...) or text(...).
            visible = []
            for name, item in value.values.items():
                field = value.klass.fields.get(name)
                if field is not None and field.private:
                    continue
                visible.append(f"{name}={self.format_value(item)}")
            inner = ", ".join(visible)
            return f"{value.klass.name}({inner})"
        return str(value)

    @staticmethod
    def _format(value: object) -> str:
        # Compatibility for earlier callers.
        temp = Interpreter(step_limit=1)
        try: return temp.format_value(value)
        finally: temp._task_pool.shutdown(wait=False, cancel_futures=True)

    def register_resource(self, resource: object) -> object:
        self._resources.append(resource)
        return resource

    def _close_resource_strict(self, resource: object, token: Token) -> BaseException | None:
        try:
            closer = getattr(resource, "close", None)
            if callable(closer): closer(); return None
            releaser = getattr(resource, "release", None)
            if callable(releaser): releaser(); return None
            stopper = getattr(resource, "stop", None)
            if callable(stopper): stopper(); return None
            destroyer = getattr(resource, "destroy", None)
            if callable(destroyer): destroyer(); return None
            return RuntimeLanguageError("資源に deterministic close 操作がありません", token.line, token.column, token.filename, detail_code="SAGA-R186")
        except BaseException as exc:
            return exc

    @staticmethod
    def _close_resource(resource: object) -> None:
        try:
            closer = getattr(resource, "close", None)
            if callable(closer):
                closer()
                return
            releaser = getattr(resource, "release", None)
            if callable(releaser):
                releaser()
                return
            stopper = getattr(resource, "stop", None)
            if callable(stopper):
                stopper()
                return
            destroyer = getattr(resource, "destroy", None)
            if callable(destroyer):
                destroyer()
        except Exception:
            # Cleanup is best-effort and must not hide the program result.
            pass

    def close(self) -> None:
        for child in reversed(self._module_interpreters):
            child.close()
        self._module_interpreters.clear()
        for resource in reversed(self._resources):
            self._close_resource(resource)
        self._resources.clear()
        self._task_pool.shutdown(wait=True, cancel_futures=False)

    def _runtime_error(self, token: Token, message: str, hint: str | None = None, diagnostic_id: str | None = None):
        raise RuntimeLanguageError(
            message, token.line, token.column, token.filename or self.filename, hint,
            end_column=token.column + max(len(token.lexeme), 1), detail_code=diagnostic_id, detail_data={"token": token.lexeme},
        )
