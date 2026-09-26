"""Optional Limited API accelerator for session directory discovery.

When ``groket._listwalk`` is built, :data:`listwalk` is that module and
:func:`find_sessions` returns session paths. When the extension is absent,
``listwalk`` is ``None`` and :func:`find_sessions` returns ``None``.
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import ModuleType

listwalk: ModuleType | None
try:
    from groket import _listwalk as listwalk
except ImportError:
    listwalk = None

# Exact directory names the C walker will not descend into.
WALK_SKIP_DIRS: frozenset[str] = frozenset(
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
    }
)


def skip_dir_name(name: str) -> bool:
    """Return True when *name* is not descended into.

    :param name: A single path component (not a full path).
    :returns: ``True`` for names in :data:`WALK_SKIP_DIRS` or ending in ``.stage``.
    """
    return name in WALK_SKIP_DIRS or name.endswith(".stage")


def looks_like_session_dir(path: Path | str) -> bool:
    """Whether *path* has session artifacts (Python twin of the C predicate).

    :param path: Directory to inspect.
    :returns: ``True`` when ``updates.jsonl`` or ``summary.json`` exists as a
        non-directory entry, or ``events.jsonl`` exists with size greater
        than zero. Missing paths and OS errors return ``False``.
    """
    directory = Path(path)

    def _nonsdir(name: str) -> bool:
        entry = directory / name
        try:
            return not stat.S_ISDIR(entry.lstat().st_mode)
        except OSError:
            return False

    try:
        if _nonsdir("updates.jsonl") or _nonsdir("summary.json"):
            return True
        if _nonsdir("events.jsonl"):
            return (directory / "events.jsonl").stat().st_size > 0
    except OSError:
        return False
    return False


def find_sessions(root: Path | str) -> list[Path] | None:
    """Discover session directories via the optional C extension.

    :param root: Traces tree or other directory to walk.
    :returns: Session paths, or ``None`` when ``groket._listwalk`` is absent.
    """
    if listwalk is None:
        return None
    found = listwalk.find_sessions(str(root))  # pragma: no cover
    return [Path(p) for p in found]  # pragma: no cover
