"""Debounced filesystem watches for live session / timeline refresh.

Uses :mod:`watchdog` (inotify on Linux). Groket runs on the **host** against
``runs/traces`` bind-mounts, so container writes normally produce events here.

Not a poller: the callback runs only when the OS reports create/modify/move/delete
under the watched tree (coalesced by *debounce_s*).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Names that matter for session list / timeline (others ignored to cut noise).
_TRACE_NAME_HINTS = (
    "updates.jsonl",
    "events.jsonl",
    "summary.json",
    "signals.json",
    "chat_history.jsonl",
    "groket-interrupted.json",
    "status.json",
    "command",
    # Operator notes (TUI may write disk-local; clients need a change signal).
    "operator_notes.toml",
)


def _path_looks_relevant(path: str) -> bool:
    name = Path(path).name
    if name in _TRACE_NAME_HINTS:
        return True
    # New session dirs often appear before files land.
    if name.startswith("019") or name.startswith("groket-"):
        return True
    # Gate / turn dirs under sessions
    if ".groket-turn" in path or "prompt_history" in name:
        return True
    return False


class TraceTreeWatch:
    """Watch *root* recursively; invoke *on_change* (debounced) on relevant events.

    *on_change* is called from the watchdog observer thread — callers must
    marshal to the UI thread themselves (``call_from_thread`` / ``post_message``).

    When *on_paths* is set, it receives the coalesced absolute paths that
    triggered the fire (best-effort; may be empty if only dir events).
    """

    def __init__(
        self,
        root: Path,
        on_change: Callable[[], None],
        *,
        debounce_s: float = 0.4,
        on_paths: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._root = Path(root)
        self._on_change = on_change
        self._on_paths = on_paths
        self._debounce_s = max(0.05, float(debounce_s))
        self._observer: object | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending = False
        self._pending_paths: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root

    def start(self) -> bool:
        """Start watching. Returns False if *root* is missing or observer fails."""
        if not self._root.is_dir():
            return False
        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("watchdog not installed; live FS watch disabled")
            return False

        watch = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if getattr(event, "is_directory", False) and event.event_type == "modified":
                    return
                src = getattr(event, "src_path", "") or ""
                dest = getattr(event, "dest_path", "") or ""
                if not (_path_looks_relevant(str(src)) or _path_looks_relevant(str(dest))):
                    return
                paths: list[str] = []
                if src:
                    paths.append(str(src))
                if dest:
                    paths.append(str(dest))
                watch._schedule_fire(paths)

        try:
            obs = Observer()
            obs.schedule(_Handler(), str(self._root), recursive=True)
            obs.daemon = True
            obs.start()
            self._observer = obs
            logger.debug("FS watch started on %s", self._root)
            return True
        except Exception:
            logger.warning("FS watch failed for %s", self._root, exc_info=True)
            self._observer = None
            return False

    def _schedule_fire(self, paths: list[str] | None = None) -> None:
        with self._lock:
            self._pending = True
            if paths:
                self._pending_paths.update(paths)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if not self._pending:
                return
            self._pending = False
            self._timer = None
            paths = sorted(self._pending_paths)
            self._pending_paths.clear()
        try:
            self._on_change()
        except Exception:
            logger.debug("FS watch callback failed", exc_info=True)
        if self._on_paths is not None:
            try:
                self._on_paths(paths)
            except Exception:
                logger.debug("FS watch path callback failed", exc_info=True)

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = False
        obs = self._observer
        self._observer = None
        if obs is not None:
            try:
                stop = getattr(obs, "stop", None)
                join = getattr(obs, "join", None)
                if callable(stop):
                    stop()
                if callable(join):
                    join(timeout=2.0)
            except Exception:
                logger.debug("FS watch stop failed", exc_info=True)
