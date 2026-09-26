"""Browser analysis scheduling respects per-session inflight lock."""

from __future__ import annotations

from pathlib import Path

from groket.analysis.inflight import (
    clear_session_analysis_inflight,
    session_analysis_inflight,
    try_begin_session_analysis,
)
from groket.job_pools import get_analysis_pool
from groket.ui.screens.browser import BrowserScreen


def setup_function() -> None:
    clear_session_analysis_inflight()


def teardown_function() -> None:
    clear_session_analysis_inflight()


def test_schedule_analysis_skips_second_enqueue(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        '{"type":"turn_ended","outcome":"completed"}\n', encoding="utf-8"
    )
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.plugin_results = {}
    screen.timeline = []
    screen.meta = None
    screen._analysis_pending = False
    screen._findings = []
    screen._analysis_stale_hints = []
    screen._show_analysis_pending = lambda: None  # type: ignore[method-assign]
    screen._set_analysis_stale_banner = lambda _h=None: None  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_analysis_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        assert try_begin_session_analysis(sd) is True
        screen._schedule_analysis()
        assert submitted == []
        assert screen._analysis_pending is True
        assert session_analysis_inflight(sd) is True
        # Second call without prior begin still blocked by inflight + pending flag.
        screen._schedule_analysis()
        assert submitted == []
    finally:
        pool.submit = real  # type: ignore[method-assign]
