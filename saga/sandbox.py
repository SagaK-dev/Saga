from __future__ import annotations

"""OS process sandbox helpers used by untrusted extension hosts.

The language-level capability system remains the primary portable policy.
This module adds a defense-in-depth host boundary.  Linux uses user, mount,
PID, IPC, UTS and network namespaces when available.  Other platforms use a
separate process with a minimized environment and resource limits and report
the reduced isolation level explicitly rather than pretending equivalence.
"""

from dataclasses import dataclass
from functools import partial
import ctypes
import os
from pathlib import Path
import platform
try:
    import resource as _resource
except ImportError:  # Windows
    _resource = None
import shutil
import subprocess
from typing import Sequence

from .exitcodes import RESOURCE_ERROR
from .limits import ProcessBudget


@dataclass(frozen=True, slots=True)
class SandboxSupport:
    mode: str
    os_isolation: bool
    network_isolation: bool
    filesystem_masking: bool
    detail: str


def support() -> SandboxSupport:
    system = platform.system().lower()
    if system == "linux" and shutil.which("unshare"):
        # Availability is checked again by the actual launch because some hosts
        # install unshare while disabling unprivileged user namespaces.
        return SandboxSupport("linux-namespaces", True, True, True, "user+mount+pid+ipc+uts+network namespaces")
    if system == "windows":
        return SandboxSupport("process-only", False, False, False, "strict OS sandbox is not available on this build; plugin execution fails closed")
    return SandboxSupport("process-only", False, False, False, "strict OS sandbox is not available on this platform; plugin execution fails closed")


def _set_no_new_privs() -> None:
    """Require Linux execs to preserve the current privilege boundary."""
    if platform.system().lower() != "linux":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        err = ctypes.get_errno()
        raise RuntimeError(f"PR_SET_NO_NEW_PRIVS failed: errno {err}")


def _resource_limits(process_budget: ProcessBudget | None = None) -> None:
    """Apply limits that are safe before entering a Linux user namespace.

    Do not set ``RLIMIT_NPROC`` here.  Strict Linux launches execute ``unshare
    --fork`` first, so a per-user process limit applied to the outer runner UID
    can prevent ``unshare`` itself from forking on busy CI hosts.  Untrusted
    plugin workers apply a tighter process limit from ``plugin_host._limits``
    after namespace creation, where the limit belongs to the isolated child.
    """
    if _resource is None:
        return
    try:
        _resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass
    kind = getattr(_resource, "RLIMIT_NOFILE", None)
    if kind is not None:
        try:
            _resource.setrlimit(kind, (64, 64))
        except (ValueError, OSError):
            pass

    if process_budget is None:
        return

    for resource_name, value in (
        ("RLIMIT_CPU", process_budget.max_cpu_seconds),
        ("RLIMIT_AS", process_budget.max_address_space_bytes),
    ):
        if value is None:
            continue
        resource_kind = getattr(_resource, resource_name, None)
        if resource_kind is None:
            raise RuntimeError(f"strict process budget requires {resource_name}")
        try:
            _resource.setrlimit(resource_kind, (value, value))
        except (ValueError, OSError) as exc:
            raise RuntimeError(
                f"strict process budget could not apply {resource_name}={value}"
            ) from exc


def _strict_cli_preexec(process_budget: ProcessBudget | None = None) -> None:
    """Install mandatory pre-exec hardening for whole-program strict mode."""
    _set_no_new_privs()
    if process_budget is None:
        _resource_limits()
    else:
        _resource_limits(process_budget)


def _minimal_env() -> dict[str, str]:
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "SAGA_SANDBOX_CHILD": "1",
        "PATH": os.defpath,
    }
    # Keep only the Windows system root needed to start Python DLLs.
    if os.name == "nt" and os.environ.get("SystemRoot"):
        env["SystemRoot"] = os.environ["SystemRoot"]
    return env


def command_for_python(script: Path, *, strict: bool = True) -> list[str]:
    import sys
    base = [sys.executable, "-I", "-S", str(script)]
    if strict:
        if platform.system().lower() == "linux" and shutil.which("unshare"):
            return [
                shutil.which("unshare") or "unshare",
                "--user", "--map-root-user", "--mount", "--pid", "--fork",
                "--kill-child=KILL", "--ipc", "--uts", "--net", "--",
                *base,
            ]
        raise RuntimeError("strict OS sandbox is unavailable on this platform/build; refusing to weaken isolation")
    return base


def run_python_host(
    script: Path,
    payload: bytes,
    *,
    timeout: float = 10.0,
    strict: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    cmd = command_for_python(script, strict=strict)
    kwargs: dict[str, object] = {
        "input": payload,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": _minimal_env(),
        "cwd": str(Path.home() if os.name == "nt" else Path("/")),
        "timeout": timeout,
        "check": False,
        "shell": False,
        "close_fds": True,
    }
    if os.name == "posix":
        kwargs["preexec_fn"] = _resource_limits
    try:
        return subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]
    except (PermissionError, OSError) as exc:
        # A Linux container may expose unshare but prohibit user namespaces.
        # The caller can explicitly choose non-strict fallback; plugin execution
        # never silently weakens isolation in strict mode.
        raise RuntimeError(f"OS sandbox could not start: {exc}") from exc


def run_cli_in_strict_sandbox(
    argv: Sequence[str],
    *,
    process_budget: ProcessBudget | None = None,
) -> int:
    """Re-exec the Saga CLI in an OS isolation boundary.

    Linux: new user, mount, PID, IPC, UTS and network namespaces plus
    no-new-privs and
    conservative descriptor limits. Files remain visible so source units and
    explicitly capability-granted paths work; Saga's path capability checks
    remain mandatory. Network is independently cut at the OS layer.
    """
    import sys
    if os.environ.get("SAGA_OS_SANDBOX_ACTIVE") == "1":
        return -1
    if platform.system().lower() != "linux" or not shutil.which("unshare"):
        raise RuntimeError("strict OS sandbox currently requires Linux with unshare/user namespaces")
    env = dict(os.environ)
    env["SAGA_OS_SANDBOX_ACTIVE"] = "1"
    cmd = [
        shutil.which("unshare") or "unshare",
        "--user", "--map-root-user", "--mount", "--pid", "--fork", "--kill-child=KILL", "--ipc", "--uts", "--net", "--",
        sys.executable, "-m", "saga.cli", *argv,
    ]
    preexec = (
        _strict_cli_preexec
        if process_budget is None
        else partial(_strict_cli_preexec, process_budget)
    )
    wall_timeout = (
        process_budget.max_wall_seconds
        if process_budget is not None
        else None
    )
    try:
        completed = subprocess.run(
            cmd,
            env=env,
            shell=False,
            check=False,
            close_fds=True,
            preexec_fn=preexec,
            timeout=wall_timeout,
        )
    except subprocess.TimeoutExpired:
        # unshare --kill-child uses a parent-death signal for its namespace
        # child. Killing the timed-out outer runner therefore tears down the
        # PID-namespace init and its descendants instead of leaving orphans.
        print(
            "error[SAGA-R002]: strict sandbox wall-clock deadline exceeded "
            f"({wall_timeout} seconds)",
            file=sys.stderr,
        )
        return RESOURCE_ERROR
    return completed.returncode
