from __future__ import annotations

"""Stable import alias for Saga's native code generator implementation."""

from hashlib import sha256
from pathlib import Path
import sys

from . import _native_codegen_impl as _impl

_fingerprint = sha256(Path(_impl.__file__).read_bytes()).hexdigest()
_impl.IMPLEMENTATION_FINGERPRINT = _fingerprint
_impl.IMPLEMENTATION_VERSION = f"0.53.0+{_fingerprint[:16]}"

# Expose the implementation module itself rather than copying its globals. This
# preserves historical monkeypatch/introspection behavior while still allowing
# the implementation to live in a separately fingerprinted file.
sys.modules[__name__] = _impl
