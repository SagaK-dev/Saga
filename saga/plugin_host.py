from __future__ import annotations

"""Untrusted Python plugin worker.

This file is executed with ``python -I -S`` in a separate OS process.  It does
not import Saga's runtime package so that a plugin cannot acquire interpreter
objects or capabilities.  Communication is a single JSON request/response.
"""

import ast
import builtins as _builtins
import ctypes
import datetime
import decimal
import fractions
import json
import importlib
import math
import os
try:
    import resource
except ImportError:
    resource = None
import statistics
import sys
import sysconfig
from types import MappingProxyType, SimpleNamespace


class _PluginOption:
    """Small value-semantic option representation for isolated plugins.

    Plugins do not import Saga's runtime package.  Providing a tiny local
    representation preserves option semantics across the JSON boundary while
    keeping interpreter objects and capabilities out of the plugin process.
    """
    __slots__ = ("present", "value")

    def __init__(self, present: bool, value=None):
        self.present = bool(present)
        self.value = value


def _some(value):
    return _PluginOption(True, value)


def _none():
    return _PluginOption(False, None)


def _is_some(value):
    return isinstance(value, _PluginOption) and value.present


def _is_none(value):
    return isinstance(value, _PluginOption) and not value.present


def _unwrap_or(value, fallback):
    if not isinstance(value, _PluginOption):
        raise TypeError("unwrap_or expects an option value")
    return value.value if value.present else fallback


def _trusted_runtime_roots() -> list[str]:
    """Return only Python's interpreter-owned standard-library roots.

    Third-party bridge paths are selected separately by the Saga host.  Keeping
    these sets distinct lets the mount sandbox hide the original interpreter
    prefix while still allowing an allowlisted extension such as NumPy to load
    ordinary stdlib dependencies (for example ``contextvars``).
    """
    roots: list[str] = []
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib"):
        value = paths.get(key)
        if not value:
            continue
        resolved = os.path.realpath(value)
        if os.path.isdir(resolved) and resolved not in roots:
            roots.append(resolved)
    return roots


def _mask_linux_paths(bridge_paths=None) -> tuple[list[str], list[str], list[str]]:
    """Mask host paths and re-expose trusted Python roots read-only.

    Third-party bridges still run in their own user/mount/PID/IPC/UTS/network
    namespaces. Python's own stdlib roots and only the explicitly selected
    package roots are re-exposed read-only under private ``/run`` mount points;
    the plugin cannot choose host paths.
    """
    if sys.platform != "linux" or os.environ.get("SAGA_SANDBOX_CHILD") != "1":
        return [], _trusted_runtime_roots(), list(bridge_paths or [])
    libc = ctypes.CDLL(None, use_errno=True)
    MS_RDONLY=1; MS_NOSUID=2; MS_NODEV=4; MS_NOEXEC=8; MS_REMOUNT=32; MS_BIND=4096
    MS_REC=16384; MS_PRIVATE=1 << 18
    if libc.mount(None, ctypes.c_char_p(b"/"), None, ctypes.c_ulong(MS_REC | MS_PRIVATE), None) != 0:
        raise RuntimeError(f"mount propagation isolation failed: errno {ctypes.get_errno()}")
    requested=[os.path.realpath(str(p)) for p in (bridge_paths or []) if os.path.isdir(p)]
    runtime_roots=_trusted_runtime_roots()
    runtime_mounts=[]
    bridge_mounts=[]
    if runtime_roots or requested:
        # /run is private in this mount namespace and carries only read-only
        # interpreter/package roots selected by Saga, never plugin paths.
        if libc.mount(ctypes.c_char_p(b"tmpfs"), ctypes.c_char_p(b"/run"), ctypes.c_char_p(b"tmpfs"), ctypes.c_ulong(MS_NOSUID|MS_NODEV|MS_NOEXEC), ctypes.c_char_p(b"mode=0700,size=8m")) != 0:
            raise RuntimeError(f"runtime tmpfs mount failed: errno {ctypes.get_errno()}")
        runtime_base="/run/saga-runtime"; os.makedirs(runtime_base,mode=0o700,exist_ok=True)
        for i,src in enumerate(runtime_roots):
            dst=f"{runtime_base}/stdlib{i}"; os.makedirs(dst,mode=0o700,exist_ok=True)
            if libc.mount(ctypes.c_char_p(src.encode()),ctypes.c_char_p(dst.encode()),None,ctypes.c_ulong(MS_BIND|MS_REC),None)!=0:
                raise RuntimeError(f"stdlib bind mount failed: errno {ctypes.get_errno()}")
            if libc.mount(None,ctypes.c_char_p(dst.encode()),None,ctypes.c_ulong(MS_BIND|MS_REMOUNT|MS_RDONLY|MS_NOSUID|MS_NODEV),None)!=0:
                raise RuntimeError(f"stdlib read-only remount failed: errno {ctypes.get_errno()}")
            runtime_mounts.append(dst)
        bridge_base="/run/saga-bridge"; os.makedirs(bridge_base,mode=0o700,exist_ok=True)
        for i,src in enumerate(requested):
            dst=f"{bridge_base}/site{i}"; os.makedirs(dst,mode=0o700,exist_ok=True)
            if libc.mount(ctypes.c_char_p(src.encode()),ctypes.c_char_p(dst.encode()),None,ctypes.c_ulong(MS_BIND|MS_REC),None)!=0:
                raise RuntimeError(f"bridge bind mount failed: errno {ctypes.get_errno()}")
            # Re-mount the bind read-only and with conservative flags.
            if libc.mount(None,ctypes.c_char_p(dst.encode()),None,ctypes.c_ulong(MS_BIND|MS_REMOUNT|MS_RDONLY|MS_NOSUID|MS_NODEV),None)!=0:
                raise RuntimeError(f"bridge read-only remount failed: errno {ctypes.get_errno()}")
            bridge_mounts.append(dst)
    masked=[]
    targets=("/home","/root","/mnt","/media","/srv","/opt","/var","/etc","/proc","/tmp","/sys","/boot")
    if not runtime_mounts and not bridge_mounts: targets=targets+("/run",)
    for target in targets:
        if not os.path.isdir(target): continue
        rc=libc.mount(ctypes.c_char_p(b"tmpfs"),ctypes.c_char_p(target.encode()),ctypes.c_char_p(b"tmpfs"),ctypes.c_ulong(MS_NOSUID|MS_NODEV|MS_NOEXEC),ctypes.c_char_p(b"mode=0700,size=16m"))
        if rc != 0: raise RuntimeError(f"mount sandbox failed for {target}: errno {ctypes.get_errno()}")
        masked.append(target)
    PR_SET_NO_NEW_PRIVS=38
    if libc.prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)!=0:
        raise RuntimeError(f"PR_SET_NO_NEW_PRIVS failed: errno {ctypes.get_errno()}")
    return masked,runtime_mounts,bridge_mounts

def _limits() -> None:
    if resource is None:
        return
    for kind, pair in (
        (getattr(resource, "RLIMIT_CORE", None), (0, 0)),
        (getattr(resource, "RLIMIT_NOFILE", None), (32, 32)),
        (getattr(resource, "RLIMIT_NPROC", None), (8, 8)),
        (getattr(resource, "RLIMIT_FSIZE", None), (0, 0)),
    ):
        if kind is not None:
            try:
                resource.setrlimit(kind, pair)
            except (ValueError, OSError):
                pass



def _wire_in(value):
    if isinstance(value, list):
        return tuple(_wire_in(v) for v in value)
    if isinstance(value, dict):
        tag = value.get("$saga")
        if tag == "option":
            return _some(_wire_in(value.get("value"))) if value.get("present") else _none()
        if tag == "decimal":
            return decimal.Decimal(str(value["value"]))
        if tag == "rational":
            return fractions.Fraction(int(value["numerator"]), int(value["denominator"]))
        if tag == "bytes":
            return bytes.fromhex(str(value["hex"]))
        if tag == "datetime":
            return datetime.datetime.fromisoformat(str(value["value"]))
        if tag == "duration":
            return datetime.timedelta(microseconds=int(value["microseconds"]))
        if tag == "set":
            return frozenset(_wire_in(v) for v in value.get("items", []))
        if tag == "map":
            return {_wire_in(k): _wire_in(v) for k, v in value.get("items", [])}
        return {str(k): _wire_in(v) for k, v in value.items()}
    return value


def _wire_out(value):
    # Array/scalar libraries such as NumPy are normalized to plain value data
    # before crossing the Saga boundary. No third-party object identity escapes.
    if type(value).__module__.startswith("numpy"):
        if hasattr(value, "tolist"):
            return _wire_out(value.tolist())
        if hasattr(value, "item"):
            return _wire_out(value.item())
    if isinstance(value, _PluginOption):
        return {"$saga": "option", "present": value.present, "value": _wire_out(value.value) if value.present else None}
    if isinstance(value, decimal.Decimal):
        return {"$saga": "decimal", "value": str(value)}
    if isinstance(value, fractions.Fraction):
        return {"$saga": "rational", "numerator": str(value.numerator), "denominator": str(value.denominator)}
    if isinstance(value, bytes):
        return {"$saga": "bytes", "hex": value.hex()}
    if isinstance(value, datetime.datetime):
        return {"$saga": "datetime", "value": value.isoformat()}
    if isinstance(value, datetime.timedelta):
        micros = ((value.days * 86_400 + value.seconds) * 1_000_000) + value.microseconds
        return {"$saga": "duration", "microseconds": micros}
    if isinstance(value, tuple):
        return [_wire_out(v) for v in value]
    if isinstance(value, frozenset):
        return {"$saga": "set", "items": [_wire_out(v) for v in sorted(value, key=repr)]}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: _wire_out(v) for k, v in value.items()}
        return {"$saga": "map", "items": [[_wire_out(k), _wire_out(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"plugin result type is not serializable: {type(value).__name__}")


class _PluginPolicy(ast.NodeVisitor):
    """Reject Python constructs that re-acquire ambient authority or introspection.

    This is not the security boundary by itself; Linux namespaces are. The AST
    policy removes common object-graph escape primitives before execution.
    """
    def visit_Import(self, node):
        raise ValueError("import statements are not allowed in isolated Saga plugins")
    def visit_ImportFrom(self, node):
        raise ValueError("import statements are not allowed in isolated Saga plugins")
    def visit_Attribute(self, node):
        if node.attr.startswith("__"):
            raise ValueError("dunder attribute access is not allowed in isolated Saga plugins")
        self.generic_visit(node)
    def visit_Name(self, node):
        if node.id.startswith("__") and node.id not in {"__name__"}:
            raise ValueError("dunder names are not allowed in isolated Saga plugins")
        self.generic_visit(node)

def _validate_source(source: str, filename: str) -> ast.AST:
    tree = ast.parse(source, filename=filename, mode="exec")
    _PluginPolicy().visit(tree)
    return tree

def _safe_builtins() -> dict[str, object]:
    allowed = (
        "abs", "all", "any", "bool", "bytes", "dict", "enumerate", "filter", "float",
        "frozenset", "int", "isinstance", "len", "list", "map", "max", "min", "range",
        "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
        "Exception", "ValueError", "TypeError", "ArithmeticError",
    )
    return {name: getattr(_builtins, name) for name in allowed}


def _safe_namespace(**values):
    return SimpleNamespace(**values)

_SAFE_MATH = _safe_namespace(
    sqrt=math.sqrt, sin=math.sin, cos=math.cos, tan=math.tan,
    floor=math.floor, ceil=math.ceil, fabs=math.fabs,
    exp=math.exp, log=math.log, log10=math.log10, pi=math.pi, e=math.e,
)
_SAFE_DECIMAL = _safe_namespace(Decimal=decimal.Decimal, localcontext=decimal.localcontext, getcontext=decimal.getcontext)
_SAFE_FRACTIONS = _safe_namespace(Fraction=fractions.Fraction)
_SAFE_STATISTICS = _safe_namespace(mean=statistics.mean, median=statistics.median, pvariance=statistics.pvariance, pstdev=statistics.pstdev)

def _external_facades(spec):
    if not isinstance(spec, dict):
        raise TypeError("imports must be an object")
    facades={}
    for module_name,names in spec.items():
        if not isinstance(module_name,str) or not module_name or module_name.startswith("_"):
            raise ValueError("invalid bridge module name")
        if not isinstance(names,list) or not all(isinstance(n,str) and n.isidentifier() and not n.startswith("_") for n in names):
            raise ValueError("invalid bridge export allowlist")
        module=importlib.import_module(module_name)
        values={}
        for name in names:
            value=getattr(module,name)
            if not callable(value) and not isinstance(value,(bool,int,float,str)):
                raise TypeError(f"bridge export must be callable or scalar: {module_name}.{name}")
            values[name]=value
        facades[module_name.split('.')[-1]]=_safe_namespace(**values)
    return facades

def _load(source: str, filename: str, imports=None) -> dict[str, object]:
    globals_dict: dict[str, object] = {
        "__builtins__": MappingProxyType(_safe_builtins()),
        "__name__": "saga_isolated_plugin",
        "math": _SAFE_MATH,
        "decimal": _SAFE_DECIMAL,
        "fractions": _SAFE_FRACTIONS,
        "statistics": _SAFE_STATISTICS,
        "some": _some,
        "none": _none,
        "is_some": _is_some,
        "is_none": _is_none,
        "unwrap_or": _unwrap_or,
    }
    globals_dict.update(_external_facades(imports or {}))
    tree = _validate_source(source, filename)
    code = compile(tree, filename, "exec", dont_inherit=True, optimize=2)
    exec(code, globals_dict, globals_dict)
    exports = globals_dict.get("saga_exports")
    if not isinstance(exports, dict) or not all(isinstance(k, str) and callable(v) for k, v in exports.items()):
        raise ValueError("plugin must define saga_exports as a mapping of names to callables")
    return exports


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        source = request["source"]
        if not isinstance(source, str):
            raise TypeError("source must be text")
        _limits()
        masked, runtime_mounts, bridge_mounts = _mask_linux_paths(request.get("bridge_paths", []))
        for path in reversed([*runtime_mounts, *bridge_mounts]):
            if path not in sys.path: sys.path.insert(0,path)
        exports = _load(source, request.get("filename", "<plugin>"), request.get("imports", {}))
        op = request.get("op")
        if op == "describe":
            result = {"exports": sorted(exports), "masked": masked, "bridge_mounts": bridge_mounts}
        elif op == "call":
            name = request.get("name")
            if name not in exports:
                raise KeyError(f"unknown plugin export: {name}")
            args = request.get("args", [])
            if not isinstance(args, list):
                raise TypeError("args must be a list")
            result = _wire_out(exports[name](*[_wire_in(v) for v in args]))
        else:
            raise ValueError("unknown plugin operation")
        sys.stdout.write(json.dumps({"ok": True, "result": result}, ensure_ascii=False, separators=(",", ":")))
        return 0
    except BaseException as exc:
        # Never emit a host traceback across the plugin protocol.
        sys.stdout.write(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
