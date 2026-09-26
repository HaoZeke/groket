"""Turn filter dropdown rebuilds when a follow-up turn appears live."""

from __future__ import annotations

from pathlib import Path

from groket.models import TraceEvent
from groket.ui.screens.browser import BrowserScreen


def _ev(index: int, event_type: str, content: str = "", **kw) -> TraceEvent:
    return TraceEvent(
        index=index,
        timestamp=float(1000 + index),
        event_type=event_type,
        content=content,
        **kw,
    )


def test_rebuild_turn_select_discovers_follow_up_mid_batch(tmp_path: Path) -> None:
    """turn_started + later tool events in one load must still show multi-turn UI."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "agent_message_chunk", "reply"),
        _ev(3, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = 1
    screen._turn_segments = []  # pretend already segmented once
    screen._turn_rebuild_sig = None
    screen._turn_filter = "all"

    # Capture set_options / display without mounting.
    calls: list[object] = []

    class _Sel:
        display = False
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]

    # Follow-up arrived with turn_started buried under newer tool rows.
    screen.timeline = [
        *screen.timeline,
        _ev(4, "turn_started", "turn_number=1"),
        _ev(5, "user_message_chunk", "follow up please"),
        _ev(6, "tool_call", "bash"),
        _ev(7, "tool_call_update", "ok"),
    ]
    screen._rebuild_turn_select()
    assert sel.display is True
    assert screen._last_turn_segment_count == 2
    assert calls, "set_options should run when becoming multi-turn"
    values = [v for _, v in calls[-1]]
    assert "0" in values and "1" in values and "all" in values


def test_rebuild_turn_select_discovers_next_turn_when_already_multi(tmp_path: Path) -> None:
    """Already multi-turn: a new turn whose batch ends on tool_call must appear.

    Regression: early-return on non-boundary tail left turn N stuck missing
    until a full refresh (user-reported for turn 42).
    """
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    # Two completed turns already segmented.
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "turn_ended", "outcome=completed"),
        _ev(2, "turn_started", "turn_number=1"),
        _ev(3, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = 2
    screen._turn_rebuild_sig = (4, 3)
    screen._turn_segments = [object(), object()]  # non-None placeholders
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = True
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]

    # Turn 2 starts; live batch ends on a tool row (not turn_started).
    screen.timeline = [
        *screen.timeline,
        _ev(4, "turn_started", "turn_number=2"),
        _ev(5, "user_message_chunk", "keep going"),
        _ev(6, "tool_call", "bash"),
    ]
    screen._rebuild_turn_select()
    assert screen._last_turn_segment_count == 3
    assert calls, "set_options must run when a new turn appears"
    values = [v for _, v in calls[-1]]
    assert "0" in values and "1" in values and "2" in values
    labels = [lab for lab, _ in calls[-1]]
    # Harness turn_number preferred in label (turn 2).
    assert any("2" in str(lab) for lab in labels)


def test_rebuild_turn_select_skips_set_options_when_count_unchanged(tmp_path: Path) -> None:
    """Mid-turn append re-segments but does not thrash Select options."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "turn_ended", "outcome=completed"),
        _ev(2, "turn_started", "turn_number=1"),
        _ev(3, "tool_call", "bash"),
    ]
    screen._last_turn_segment_count = 2
    screen._turn_rebuild_sig = (4, 3)
    screen._turn_segments = [object(), object()]
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = True
        value = "all"

        def set_options(self, options):
            calls.append(options)

    screen.query_one = lambda _q, _t=None: _Sel()  # type: ignore[method-assign]
    screen.timeline.append(_ev(4, "tool_call_update", "more"))
    screen._rebuild_turn_select()
    assert calls == []
    assert screen._last_turn_segment_count == 2
    # Same tail again: full no-op (no re-segment needed).
    screen._rebuild_turn_select()
    assert calls == []
