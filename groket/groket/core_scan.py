"""Optional native ``updates.jsonl`` prefilter via ctypes (no pyo3)."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Terminal ``tool_call_update`` needles (Python path when the crate is absent).
_TU_BYTES = b"tool_call_update"
_TERM_BYTES = (
    b'"status":"completed"',
    b'"status": "completed"',
    b'"status":"failed"',
    b'"status": "failed"',
    b'"isError":true',
    b'"isError": true',
)

CORE_LIB_ENV = "GROKET_CORE_LIB"

_RC_OK = 0
_RC_NULL = -2

_lib: ctypes.CDLL | None = None
_lib_checked = False


def keep_updates_line_py(line: bytes) -> bool:
    """Return True when *line* should be JSON-parsed (Python twin of the crate).

    :param line: One ``updates.jsonl`` row (newline optional).
    :returns: False for non-terminal ``tool_call_update`` lines.
    """
    if _TU_BYTES in line and not any(m in line for m in _TERM_BYTES):
        return False
    return True


def filter_updates_py(data: bytes) -> list[bytes]:
    """Split *data* on ``\\n`` and keep lines :func:`keep_updates_line_py` accepts.

    Trailing ``\\r`` is dropped. An incomplete last line (no ``\\n``) is kept
    when nonempty.

    :param data: Raw ``updates.jsonl`` bytes.
    :returns: Kept line bodies (no trailing newline).
    """
    out: list[bytes] = []
    start = 0
    for i, byte in enumerate(data):
        if byte != 0x0A:
            continue
        line = data[start:i]
        if line.endswith(b"\r"):
            line = line[:-1]
        if keep_updates_line_py(line):
            out.append(line)
        start = i + 1
    if start < len(data):
        line = data[start:]
        if line.endswith(b"\r"):
            line = line[:-1]
        if line and keep_updates_line_py(line):
            out.append(line)
    return out


def core_lib_filename() -> str:
    """Platform shared-library file name for the scan leaf."""
    plat = sys.platform
    if plat == "darwin":
        return "libgroket_core.dylib"
    if plat == "win32":
        return "groket_core.dll"
    return "libgroket_core.so"


def core_lib_candidates() -> list[Path]:
    """Search paths: ``GROKET_CORE_LIB``, next to the package, checkout release."""
    name = core_lib_filename()
    pkg = Path(__file__).resolve().parent
    root = pkg.parent
    paths: list[Path] = []
    env = os.environ.get(CORE_LIB_ENV, "").strip()
    if env:
        paths.append(Path(env).expanduser())
    paths.append(pkg / name)
    paths.append(root / name)
    paths.append(root / "native" / "groket-core" / "target" / "release" / name)
    return paths


def reset_core_lib_cache() -> None:
    """Clear the cached CDLL handle (tests that change search paths)."""
    global _lib, _lib_checked
    _lib = None
    _lib_checked = False


def _bind_core_lib(lib: ctypes.CDLL) -> None:
    lib.groket_keep_updates_line.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.groket_keep_updates_line.restype = ctypes.c_int32
    lib.groket_filter_updates.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.groket_filter_updates.restype = ctypes.c_int32


def load_core_lib() -> ctypes.CDLL | None:
    """Load ``libgroket_core`` from env, package dir, or checkout release.

    :returns: Bound CDLL, or ``None`` when no candidate loads.
    """
    global _lib, _lib_checked
    if _lib_checked:
        return _lib
    _lib_checked = True
    for path in core_lib_candidates():
        if not path.is_file():
            continue
        try:
            loaded = ctypes.CDLL(str(path))
        except OSError:
            logger.debug("failed to load groket-core from %s", path, exc_info=True)
            continue
        _bind_core_lib(loaded)
        _lib = loaded
        return _lib
    return None


def _in_buf(data: bytes) -> ctypes.Array[ctypes.c_uint8] | None:
    if not data:
        return None
    return (ctypes.c_uint8 * len(data)).from_buffer_copy(data)


def _decode_kept(blob: bytes) -> list[bytes]:
    if not blob:
        return []
    return blob[:-1].split(b"\n")


def keep_updates_line(line: bytes) -> bool | None:
    """Keep/skip via the native lib, or ``None`` when the lib is not loaded.

    :param line: One ``updates.jsonl`` row (newline optional).
    :returns: Native keep decision, or ``None`` if the CDLL is missing.
    """
    lib = load_core_lib()
    if lib is None:
        return None
    rc = int(lib.groket_keep_updates_line(_in_buf(line), len(line)))
    if rc < 0:
        return None
    return rc != 0


def filter_updates(data: bytes) -> list[bytes] | None:
    """Filter *data* via the native lib, or ``None`` when the lib is not loaded.

    :param data: Raw ``updates.jsonl`` bytes.
    :returns: Kept line bodies, or ``None`` if the CDLL is missing or the call fails.
    """
    lib = load_core_lib()
    if lib is None:
        return None
    needed = ctypes.c_size_t(0)
    in_arr = _in_buf(data)
    rc = int(lib.groket_filter_updates(in_arr, len(data), None, 0, ctypes.byref(needed)))
    if rc == _RC_NULL:
        return None
    n = int(needed.value)
    if n == 0:
        return []
    out_arr = (ctypes.c_uint8 * n)()
    rc = int(lib.groket_filter_updates(in_arr, len(data), out_arr, n, ctypes.byref(needed)))
    if rc != _RC_OK:
        return None
    return _decode_kept(bytes(out_arr[: int(needed.value)]))
