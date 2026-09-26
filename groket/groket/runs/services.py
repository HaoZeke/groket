"""UI-agnostic services — log capture for background runs.

Textual screens depend on these; never the reverse.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

__all__ = ["LogBuffer", "LogLine"]


@dataclass(frozen=True, slots=True)
class LogLine:
    """One captured log line tagged by stream/container source."""

    source: str
    text: str


LogListener = Callable[[str, str], None]


class LogBuffer:
    """Thread-safe ring buffer with optional fan-out to live listeners.

    * Capture always happens (append).
    * Listeners are optional (log viewer only); they must not do heavy work.
    * ``snapshot()`` is for UI paint; no listener required for data retention.
    """

    def __init__(self, *, maxlen: int = 8000) -> None:
        self._lines: deque[LogLine] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._listeners: list[LogListener] = []
        self._notify_listeners = False

    def enable_live_notify(self, enabled: bool = True) -> None:
        """When False (default), only the buffer grows; viewers use ``snapshot()``."""
        with self._lock:
            self._notify_listeners = enabled

    def clear_listeners(self) -> None:
        """Drop live fan-out callbacks (e.g. TUI quit)."""
        with self._lock:
            self._listeners.clear()
            self._notify_listeners = False

    def append(self, source: str, text: str) -> None:
        line = LogLine(source=source or "", text=text or "")
        listeners: list[LogListener] = []
        notify = False
        with self._lock:
            self._lines.append(line)
            notify = self._notify_listeners and bool(self._listeners)
            if notify:
                listeners = list(self._listeners)
        if notify:
            for cb in listeners:
                try:
                    cb(line.source, line.text)
                except Exception:
                    pass

    def extend(self, items: Iterable[tuple[str, str]]) -> None:
        for src, txt in items:
            self.append(src, txt)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def snapshot(self, *, max_lines: int | None = None) -> list[LogLine]:
        with self._lock:
            data = list(self._lines)
        if max_lines is not None and max_lines >= 0:
            return data[-max_lines:]
        return data

    def snapshot_text(self, *, max_lines: int | None = None, include_source: bool = True) -> str:
        lines = self.snapshot(max_lines=max_lines)
        if include_source:
            return "\n".join(f"[{ln.source}] {ln.text}" if ln.source else ln.text for ln in lines)
        return "\n".join(ln.text for ln in lines)

    def add_listener(self, cb: LogListener) -> None:
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: LogListener) -> None:
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)
