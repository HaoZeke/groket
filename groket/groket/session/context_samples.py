"""In-memory context-usage samples collected during live refresh.

Grok only exposes a session snapshot in ``signals.json``. While a browser is
open we record that snapshot against the current turn index on each read-only
refresh so the turns table can show values observed during earlier turns
without writing into the traces tree.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..models import SessionMeta
from ..utils import fmt_context_usage


@dataclass(frozen=True)
class ContextSample:
    """One read-only observation of session context fill."""

    turn_index: int
    usage_pct: int | None
    tokens_used: int | None
    window_tokens: int | None
    compact: str
    ts: float


class ContextSampleStore:
    """Thread-safe map of turn index → latest observed context snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_turn: dict[int, ContextSample] = {}

    def record(self, turn_index: int, meta: SessionMeta | None) -> bool:
        """Store *meta* context against *turn_index* when the compact label changes.

        :returns: True when the store changed.
        """
        if meta is None or not meta.has_context_usage:
            return False
        compact = meta.context_usage_compact or meta.context_usage_str
        if not compact:
            return False
        sample = ContextSample(
            turn_index=max(0, int(turn_index)),
            usage_pct=meta.context_window_usage_pct,
            tokens_used=meta.context_tokens_used,
            window_tokens=meta.context_window_tokens,
            compact=compact,
            ts=time.time(),
        )
        with self._lock:
            prev = self._by_turn.get(sample.turn_index)
            if prev is not None and prev.compact == sample.compact:
                return False
            self._by_turn[sample.turn_index] = sample
            return True

    def compact_for_turn(self, turn_index: int) -> str:
        with self._lock:
            sample = self._by_turn.get(int(turn_index))
            return sample.compact if sample is not None else ""

    def compact_by_turn(self) -> dict[int, str]:
        """Copy of turn → compact label for UI rendering."""
        with self._lock:
            return {idx: sample.compact for idx, sample in self._by_turn.items()}

    def clear(self) -> None:
        with self._lock:
            self._by_turn.clear()


def context_compact_from_meta(meta: SessionMeta | None) -> str:
    """Compact label helper for call sites that do not keep a sample store."""
    if meta is None or not meta.has_context_usage:
        return ""
    return meta.context_usage_compact or fmt_context_usage(
        meta.context_window_usage_pct,
        meta.context_tokens_used,
        meta.context_window_tokens,
        compact=True,
    )
