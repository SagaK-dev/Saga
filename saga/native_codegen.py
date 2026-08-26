from __future__ import annotations

"""Stable import surface for Saga's native code generator.

The implementation lives in ``_native_codegen_impl`` so the cache identity can
include both the Saga release and the exact implementation bytes.  Any change to
the code generator therefore invalidates previously cached native objects even
when a maintainer forgets to bump a manual cache version.
"""

from hashlib import sha256
from pathlib import Path

from . import _native_codegen_impl as _impl

IMPLEMENTATION_FINGERPRINT = sha256(Path(_impl.__file__).read_bytes()).hexdigest()
_impl.IMPLEMENTATION_VERSION = f"0.53.0+{IMPLEMENTATION_FINGERPRINT[:16]}"

# Preserve the complete historical native_codegen import surface, including
# internal helpers used by the compiler and regression suite.
for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

IMPLEMENTATION_VERSION = _impl.IMPLEMENTATION_VERSION
