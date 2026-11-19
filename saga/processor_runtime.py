from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .sandbox import run_python_host


class ProcessorSandboxError(RuntimeError):
    pass


def run_processor(path: Path, metadata: dict, *, timeout: float = 10.0) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    host = Path(__file__).with_name("processor_host.py")
    payload = json.dumps(
        {"source": source, "filename": str(path), "metadata": metadata},
        ensure_ascii=False,
    ).encode("utf-8")
    proc = run_python_host(host, payload, timeout=timeout, strict=True)
    try:
        response = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        detail = proc.stderr.decode("utf-8", errors="replace")[:300]
        raise ProcessorSandboxError(detail) from exc

    if not response.get("ok"):
        raise ProcessorSandboxError(f"{response.get('error')}: {response.get('message')}")
    result = response.get("result")
    if not isinstance(result, dict) or not all(
        isinstance(name, str) and isinstance(content, str)
        for name, content in result.items()
    ):
        raise ProcessorSandboxError("invalid processor output")
    return result


def write_outputs(output_dir: Path, files: dict[str, str]) -> None:
    """Validate the complete output set before changing any destination file."""
    root = output_dir.resolve()
    planned: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    for name, content in files.items():
        rel = Path(name)
        if not name or rel.is_absolute() or ".." in rel.parts or rel == Path("."):
            raise ProcessorSandboxError(f"processor output escapes target: {name}")
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ProcessorSandboxError(f"processor output escapes target: {name}") from exc
        if target in seen:
            raise ProcessorSandboxError(f"duplicate processor output: {name}")
        if target.is_dir():
            raise ProcessorSandboxError(f"processor output is a directory: {name}")
        seen.add(target)
        planned.append((target, content))

    for target in seen:
        if any(parent in seen for parent in target.parents if parent != target):
            raise ProcessorSandboxError(f"processor output paths overlap: {target}")

    root.mkdir(parents=True, exist_ok=True)

    # Stage all output before replacing anything. Existing files are backed up
    # so a failed later rename can restore earlier replacements.
    prepared: list[tuple[Path, Path, Path | None]] = []
    replaced: list[tuple[Path, Path | None]] = []
    restore_failed: set[Path] = set()
    try:
        for target, content in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=f".{target.name}.", delete=False,
            ) as handle:
                staged = Path(handle.name)
                prepared.append((staged, target, None))
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

        for index, (staged, target, _) in enumerate(prepared):
            backup = None
            if target.exists():
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=f".{target.name}.backup-",
                    delete=False,
                ) as handle:
                    backup = Path(handle.name)
                prepared[index] = (staged, target, backup)
                shutil.copy2(target, backup)

        for staged, target, backup in prepared:
            os.replace(staged, target)
            replaced.append((target, backup))
    except Exception:
        # Rollback is best effort if the filesystem itself is failing. Retain
        # any backup that could not be restored instead of discarding the data.
        for target, backup in reversed(replaced):
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
            except OSError:
                restore_failed.add(target)
        raise
    finally:
        for staged, target, backup in prepared:
            staged.unlink(missing_ok=True)
            if backup is not None and target not in restore_failed:
                backup.unlink(missing_ok=True)
