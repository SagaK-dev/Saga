from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Type:
    name: str
    args: tuple["Type", ...] = ()
    result: "Type | None" = None

    def __str__(self) -> str:
        if self.name == "fn":
            params = ", ".join(str(arg) for arg in self.args)
            return f"fn({params}) -> {self.result or UNIT}"
        if self.name == "typevar":
            return self.args[0].name if self.args else "T"
        if self.name.startswith("typector:"):
            return self.name.split(":", 1)[1]
        if self.name == "typeapply" and self.args:
            constructor, *arguments = self.args
            return f"{constructor}[{', '.join(str(arg) for arg in arguments)}]"
        if self.args:
            return f"{self.name}[{', '.join(str(arg) for arg in self.args)}]"
        return self.name


INT = Type("int")
DECIMAL = Type("decimal")
RATIONAL = Type("rational")
BOOL = Type("bool")
TEXT = Type("text")
UNIT = Type("unit")
RANGE = Type("range")
ANY = Type("any")
BYTES = Type("bytes")
ERROR = Type("error")
DATETIME = Type("datetime")
DURATION = Type("duration")
CLASS_VALUE = Type("class")


def LIST(element: Type = ANY) -> Type:
    return Type("list", (element,))


def MAP(key: Type = TEXT, value: Type = ANY) -> Type:
    return Type("map", (key, value))


def SET(element: Type = ANY) -> Type:
    return Type("set", (element,))


def FUTURE(value: Type = ANY) -> Type:
    return Type("future", (value,))


def OPTION(value: Type = ANY) -> Type:
    return Type("option", (value,))

def RESULT(ok: Type = ANY, err: Type = ANY) -> Type:
    return Type("result", (ok, err))


def NATIVE(name: str) -> Type:
    return Type(f"native:{name}")


def OBJECT(name: str, args: tuple[Type, ...] = ()) -> Type:
    return Type(f"object:{name}", args)


def MODULE(name: str) -> Type:
    return Type(f"module:{name}")


def FUNCTION(params: list[Type] | tuple[Type, ...], result: Type) -> Type:
    return Type("fn", tuple(params), result)


def TYPEVAR(name: str) -> Type:
    return Type("typevar", (Type(name),))


def TYPECTOR(name: str) -> Type:
    """A unary-or-higher type constructor captured during HKT inference."""
    return Type(f"typector:{name}")


def TYPEAPPLY(constructor: Type, args: list[Type] | tuple[Type, ...]) -> Type:
    return Type("typeapply", (constructor, *tuple(args)))


def is_typector(value: Type) -> bool:
    return value.name.startswith("typector:")


def typector_name(value: Type) -> str:
    return value.name.split(":", 1)[1]


NATIVE_ALIASES = {
    "db_connection": NATIVE("db_connection"),
    "document_database": NATIVE("document_database"),
    "http_response": NATIVE("http_response"),
    "http_request": NATIVE("http_request"),
    "http_server": NATIVE("http_server"),
    "socket": NATIVE("socket"),
    "websocket": NATIVE("websocket"),
    "task_pool": NATIVE("task_pool"),
    "window": NATIVE("window"),
    "widget": NATIVE("widget"),
    "image": NATIVE("image"),
    "video": NATIVE("video"),
    "model": NATIVE("model"),
    "plugin": NATIVE("plugin"),
    "spark_session": NATIVE("spark_session"),
    "gpio_pin": NATIVE("gpio_pin"),
}


ALIASES = {
    "int": INT, "Int": INT, "integer": INT,
    "decimal": DECIMAL, "Decimal": DECIMAL, "number": DECIMAL,
    "rational": RATIONAL, "Rational": RATIONAL, "fraction": RATIONAL,
    "bool": BOOL, "Bool": BOOL, "boolean": BOOL,
    "text": TEXT, "Text": TEXT, "string": TEXT, "String": TEXT,
    "unit": UNIT, "Unit": UNIT,
    "range": RANGE, "Range": RANGE,
    "any": ANY, "Any": ANY,
    "bytes": BYTES, "Bytes": BYTES,
    "error": ERROR, "Error": ERROR,
    "datetime": DATETIME, "DateTime": DATETIME,
    "duration": DURATION, "Duration": DURATION,
}


class _TypeParser:
    def __init__(self, text: str, type_vars: set[str] | None = None) -> None:
        self.text = text.replace(" ", "")
        self.pos = 0
        self.type_vars = type_vars or set()

    def parse(self) -> Type:
        result = self._parse_type()
        if self.pos != len(self.text):
            raise ValueError(f"型の書き方が正しくありません: {self.text}")
        return result

    def _parse_type(self) -> Type:
        name = self._identifier()
        if name in self.type_vars:
            base = TYPEVAR(name)
        elif name in ALIASES:
            base = ALIASES[name]
        elif name in NATIVE_ALIASES:
            base = NATIVE_ALIASES[name]
        else:
            base = OBJECT(name)
        if self._peek() == "[":
            self.pos += 1
            args: list[Type] = []
            if self._peek() != "]":
                while True:
                    args.append(self._parse_type())
                    if self._peek() != ",":
                        break
                    self.pos += 1
            if self._peek() != "]":
                raise ValueError(f"型の ']' がありません: {self.text}")
            self.pos += 1
            # A declared type variable used in constructor position (F[A]) is
            # an applied higher-kinded variable, not a nominal object named F.
            # Its kind arity is inferred from the number of applied arguments.
            if name in self.type_vars:
                return TYPEAPPLY(base, args)
            lower = name.lower()
            if lower == "list":
                if len(args) != 1: raise ValueError("list は1つの型引数が必要です")
                return LIST(args[0])
            if lower == "map":
                if len(args) != 2: raise ValueError("map は2つの型引数が必要です")
                return MAP(args[0], args[1])
            if lower == "set":
                if len(args) != 1: raise ValueError("set は1つの型引数が必要です")
                return SET(args[0])
            if lower == "future":
                if len(args) != 1: raise ValueError("future は1つの型引数が必要です")
                return FUTURE(args[0])
            if lower == "option":
                if len(args) != 1: raise ValueError("option は1つの型引数が必要です")
                return OPTION(args[0])
            if lower == "result":
                if len(args) != 2: raise ValueError("result は2つの型引数が必要です")
                return RESULT(args[0], args[1])
            if lower == "fn":
                if len(args) < 1: raise ValueError("fn は最後の型引数に戻り値型が必要です")
                return FUNCTION(args[:-1], args[-1])
            return OBJECT(name, tuple(args))
        return base

    def _identifier(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_:."):
            self.pos += 1
        if start == self.pos:
            raise ValueError(f"型名が必要です: {self.text}")
        return self.text[start:self.pos]

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""


def parse_type(text: str, type_vars: set[str] | None = None) -> Type:
    return _TypeParser(text, type_vars).parse()


def is_numeric(value: Type) -> bool:
    return value in {INT, DECIMAL, RATIONAL}


def common_numeric(left: Type, right: Type) -> Type:
    if DECIMAL in {left, right}:
        return DECIMAL
    if RATIONAL in {left, right}:
        return RATIONAL
    return INT


def is_typevar(value: Type) -> bool:
    return value.name == "typevar"


def typevar_name(value: Type) -> str:
    return value.args[0].name


def substitute(value: Type, mapping: dict[str, Type]) -> Type:
    if is_typevar(value):
        return mapping.get(typevar_name(value), value)
    if value.name == "typeapply" and value.args:
        constructor = substitute(value.args[0], mapping)
        arguments = tuple(substitute(arg, mapping) for arg in value.args[1:])
        if is_typector(constructor):
            return Type(typector_name(constructor), arguments)
        return TYPEAPPLY(constructor, arguments)
    if value.name == "fn":
        return FUNCTION([substitute(arg, mapping) for arg in value.args], substitute(value.result or UNIT, mapping))
    if value.args:
        return Type(value.name, tuple(substitute(arg, mapping) for arg in value.args), value.result)
    return value


def _unify_invariant(pattern: Type, actual: Type, mapping: dict[str, Type]) -> bool:
    """Unify a generic argument without introducing numeric covariance.

    Saga generic parameters are invariant. Type variables may still bind to the
    corresponding actual type, but concrete generic arguments must match their
    structure exactly.
    """
    if pattern.name == "typeapply" and pattern.args:
        constructor, *arguments = pattern.args
        if (
            not is_typevar(constructor)
            or actual.name == "fn"
            or len(arguments) != len(actual.args)
        ):
            return False
        name = typevar_name(constructor)
        candidate = TYPECTOR(actual.name)
        existing = mapping.get(name)
        if existing is None:
            mapping[name] = candidate
        elif existing != candidate:
            return False
        return all(_unify_invariant(p, a, mapping) for p, a in zip(arguments, actual.args))
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
            all(_unify_invariant(p, a, mapping) for p, a in zip(pattern.args, actual.args))
            and _unify_invariant(pattern.result, actual.result, mapping)
        )
    if pattern.args:
        return all(_unify_invariant(p, a, mapping) for p, a in zip(pattern.args, actual.args))
    return pattern == actual


def unify(pattern: Type, actual: Type, mapping: dict[str, Type]) -> bool:
    if pattern.name == "typeapply" and pattern.args:
        constructor, *arguments = pattern.args
        if (
            not is_typevar(constructor)
            or actual.name == "fn"
            or len(arguments) != len(actual.args)
        ):
            return False
        name = typevar_name(constructor)
        candidate = TYPECTOR(actual.name)
        existing = mapping.get(name)
        if existing is None:
            mapping[name] = candidate
        elif existing != candidate:
            return False
        return all(unify(p, a, mapping) for p, a in zip(arguments, actual.args))
    if is_typevar(pattern):
        name = typevar_name(pattern)
        existing = mapping.get(name)
        if existing is None:
            mapping[name] = actual
            return True
        return is_assignable(existing, actual) and is_assignable(actual, existing)
    if pattern == ANY or actual == ANY:
        return True
    if pattern.name != actual.name:
        return is_assignable(pattern, actual)
    if len(pattern.args) != len(actual.args):
        return False
    if pattern.name == "fn":
        if pattern.result is None or actual.result is None:
            return pattern.result is actual.result
        return (
            all(unify(p, a, mapping) for p, a in zip(pattern.args, actual.args))
            and unify(pattern.result, actual.result, mapping)
        )
    if pattern.args:
        return all(_unify_invariant(p, a, mapping) for p, a in zip(pattern.args, actual.args))
    return pattern == actual


def is_assignable(expected: Type, actual: Type) -> bool:
    if expected == ANY or actual == ANY:
        return True
    if expected == CLASS_VALUE and actual.name == "fn" and actual.result is not None and actual.result.name.startswith("object:"):
        return True
    if is_typevar(expected):
        return True
    if expected == actual:
        return True
    if expected == DECIMAL and is_numeric(actual):
        return True
    if expected == RATIONAL and actual == INT:
        return True
    if expected.name == "fn" and actual.name == "fn":
        if len(expected.args) != len(actual.args) or expected.result is None or actual.result is None:
            return False
        # Function parameters are contravariant; return values are covariant.
        return (
            all(is_assignable(actual_param, expected_param) for expected_param, actual_param in zip(expected.args, actual.args))
            and is_assignable(expected.result, actual.result)
        )
    if expected.name == actual.name and len(expected.args) == len(actual.args):
        # Generic arguments are invariant in the Standard Core. Numeric widening
        # is allowed for scalar values, never by changing a generic container's
        # element type.
        return expected.args == actual.args and expected.result == actual.result
    return False
