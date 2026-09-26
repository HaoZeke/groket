"""Per-session analysis inflight tracking.

Thin wrappers over :mod:`groket.session_inflight` so browser auto-analyze and
home-list analyze share one lock table with live refresh (separate kind).
"""

from __future__ import annotations

from pathlib import Path

from groket.session_inflight import (
    KIND_ANALYSIS,
    clear,
    end,
    inflight_count,
    is_inflight,
    session_dir_key,
    try_begin,
)

analysis_session_key = session_dir_key


def try_begin_session_analysis(session_dir: Path | str) -> bool:
    """Mark *session_dir* inflight for analysis. False if already in the pipeline."""
    return try_begin(KIND_ANALYSIS, session_dir)


def end_session_analysis(session_dir: Path | str) -> bool:
    """Clear analysis inflight. True if a coalesced rerun was requested."""
    return end(KIND_ANALYSIS, session_dir)


def session_analysis_inflight(session_dir: Path | str) -> bool:
    """True when analysis is queued or running for *session_dir*."""
    return is_inflight(KIND_ANALYSIS, session_dir)


def clear_session_analysis_inflight() -> None:
    """Drop all analysis inflight keys (tests / process teardown)."""
    clear(KIND_ANALYSIS)


def session_analysis_inflight_count() -> int:
    return inflight_count(KIND_ANALYSIS)
