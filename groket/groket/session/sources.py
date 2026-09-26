"""Session catalog roots: eval (Docker) traces and optional host Grok sessions.

Eval catalog (always): ``<work>/runs/traces`` — sessions launched by groket.
Host catalog (optional): ``~/.grok/sessions`` — native Grok Build sessions.

On-disk origin codes remain ``work`` / ``host``; the TUI labels them Eval / Host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..parser import find_sessions

_HOST_SKIP_DIR_NAMES = frozenset(
    {
        "groket-plugins",
        "groket-skills",
        "subagents",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        ".cache",
        ".tox",
        ".groket-resume-seed",
        ".groket-workspace-seed",
        "workspace",
        "runs",
    }
)

ORIGIN_WORK = "work"
ORIGIN_HOST = "host"

type SessionOrigin = str


def host_grok_sessions_root() -> Path:
    """Default Grok Build sessions root: ~/.grok/sessions."""
    return Path.home() / ".grok" / "sessions"


def work_traces_root(work_dir: Path) -> Path:
    """Docker/eval traces under the work root."""
    return Path(work_dir).expanduser() / "runs" / "traces"


def _resolved(path: Path) -> Path:
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p


def is_host_grok_sessions_root(path: Path) -> bool:
    """True when *path* is the host Grok sessions tree (~/.grok/sessions)."""
    try:
        return _resolved(path) == _resolved(host_grok_sessions_root())
    except OSError:
        return False


def is_under_host_grok_sessions(session_dir: Path) -> bool:
    """True when *session_dir* lives under the host Grok sessions tree."""
    try:
        host = _resolved(host_grok_sessions_root())
        p = _resolved(session_dir)
    except OSError:
        return False
    if p == host:
        return True
    return host in p.parents


def classify_session_origin(
    session_dir: Path,
    *,
    work_traces: Path,
    host_root: Path | None = None,
) -> SessionOrigin:
    """Return work or host for *session_dir*."""
    sd = _resolved(session_dir)
    host = _resolved(host_root) if host_root is not None else _resolved(host_grok_sessions_root())
    if sd == host or host in sd.parents:
        return ORIGIN_HOST
    wt = _resolved(work_traces)
    if sd == wt or wt in sd.parents:
        return ORIGIN_WORK
    if is_under_host_grok_sessions(session_dir):
        return ORIGIN_HOST
    return ORIGIN_WORK


@dataclass(frozen=True)
class SessionScanRoot:
    """One directory to scan for operator-facing sessions."""

    origin: SessionOrigin
    path: Path


def session_scan_roots(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool = False,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Roots for the sessions home list.

    Always includes the work traces tree. When *include_host* is true, also
    includes the host Grok sessions root. An explicit *traces_path* that is
    not already covered is added with a classified origin.
    """
    work = work_traces_root(work_dir)
    host = Path(host_root).expanduser() if host_root is not None else host_grok_sessions_root()
    out: list[SessionScanRoot] = []
    seen: set[str] = set()

    def add(origin: SessionOrigin, path: Path) -> None:
        key = str(_resolved(path))
        if key in seen:
            return
        seen.add(key)
        out.append(SessionScanRoot(origin=origin, path=Path(path).expanduser()))

    add(ORIGIN_WORK, work)
    if include_host:
        add(ORIGIN_HOST, host)
    if traces_path is not None:
        tp = Path(traces_path).expanduser()
        origin = classify_session_origin(tp, work_traces=work, host_root=host)
        add(origin, tp)
    return out


def is_encoded_cwd_name(name: str) -> bool:
    """True for URL-encoded absolute paths used as Grok host session buckets."""
    n = (name or "").casefold()
    return n.startswith("%2f") or "%2f" in n


def is_host_skip_dir_name(name: str) -> bool:
    """Host-tree names that must not be descended (workspace / staging junk)."""
    low = (name or "").casefold()
    if not low:
        return True
    if low in _HOST_SKIP_DIR_NAMES or low.endswith(".stage"):
        return True
    return False


def _dir_is_session(path: Path) -> bool:
    """True when *path* itself is a session directory (no recursion)."""
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if not ent.is_file(follow_symlinks=False):
                    continue
                names.add(ent.name)
                if names & {"summary.json", "updates.jsonl"}:
                    return True
    except OSError:
        return False
    if "events.jsonl" in names:
        try:
            return (path / "events.jsonl").stat().st_size > 0
        except OSError:
            return False
    return False


def _immediate_session_children(path: Path) -> list[Path]:
    """Session dirs that are direct children of *path* (no deeper walk)."""
    out: list[Path] = []
    try:
        with os.scandir(path) as it:
            children = list(it)
    except OSError:
        return out
    for ent in children:
        if not ent.is_dir(follow_symlinks=False):
            continue
        if is_host_skip_dir_name(ent.name):
            continue
        child = Path(ent.path)
        if _dir_is_session(child):
            out.append(child)
    return out


def collect_host_session_dirs(root: Path) -> list[Path]:
    """Host sessions: shallow children, or one level under encoded-cwd buckets.

    Does not walk ``workspace`` / staging junk or recurse into a session.
    """
    path = Path(root).expanduser()
    if not path.is_dir():
        return []
    found: list[Path] = []
    try:
        with os.scandir(path) as it:
            tops = list(it)
    except OSError:
        return found
    for ent in tops:
        if not ent.is_dir(follow_symlinks=False):
            continue
        name = ent.name
        if is_host_skip_dir_name(name):
            continue
        child = Path(ent.path)
        if _dir_is_session(child):
            found.append(child)
            continue
        if is_encoded_cwd_name(name):
            found.extend(_immediate_session_children(child))
    return found


def collect_session_dirs(
    roots: list[SessionScanRoot],
) -> list[tuple[Path, SessionOrigin]]:
    """Find unique session directories across *roots* (first origin wins)."""
    found: list[tuple[Path, SessionOrigin]] = []
    seen: set[str] = set()
    for root in roots:
        path = root.path
        if not path.exists():
            continue
        if root.origin == ORIGIN_HOST:
            session_dirs = collect_host_session_dirs(path)
        else:
            session_dirs = find_sessions(path)
        for sd in session_dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append((sd, root.origin))
    return found


__all__ = [
    "ORIGIN_HOST",
    "ORIGIN_WORK",
    "SessionOrigin",
    "SessionScanRoot",
    "classify_session_origin",
    "collect_host_session_dirs",
    "collect_session_dirs",
    "host_grok_sessions_root",
    "is_encoded_cwd_name",
    "is_host_skip_dir_name",
    "is_host_grok_sessions_root",
    "is_under_host_grok_sessions",
    "session_scan_roots",
    "work_traces_root",
]
