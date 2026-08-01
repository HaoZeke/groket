"""Timeline turn segmentation for multi-turn stats/summary/report."""

from __future__ import annotations

from groket.models import TraceEvent
from groket.session.turns import (
    format_turns_plain,
    segment_timeline_turns,
    turn_index_for_event,
    turn_summary_rows,
)


def _ev(index: int, etype: str, content: str = "", **kw) -> TraceEvent:
    return TraceEvent(
        index=index,
        event_type=etype,
        content=content,
        timestamp=kw.get("ts", 1_000_000 + index * 10),
        tool_name=kw.get("tool", ""),
        is_error=kw.get("err", False),
    )


def test_no_markers_single_open_segment():
    tl = [_ev(0, "user_message_chunk", "hi"), _ev(1, "agent_message_chunk", "yo")]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].open is True
    assert segs[0].event_count == 2
    assert segs[0].turn_index == 0


def test_two_turns_with_markers():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "tool_call", tool="grep"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
        _ev(4, "turn_started", "turn started  turn_number=1"),
        _ev(5, "user_message_chunk", "second"),
        _ev(6, "tool_call", tool="bash", err=True),
        _ev(7, "turn_ended", "turn ended  outcome=success"),
    ]
    # fix tool_name kw - TraceEvent uses tool_name
    tl[2] = TraceEvent(index=2, event_type="tool_call", tool_name="grep", timestamp=1_000_020)
    tl[6] = TraceEvent(
        index=6, event_type="tool_call", tool_name="bash", is_error=True, timestamp=1_000_060
    )
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].outcome == "success"
    assert segs[0].tool_call_count == 1
    assert segs[1].tool_error_count == 1
    assert segs[1].user_count == 1
    rows = turn_summary_rows(segs)
    # Newest first: turn 1 is top, turn 0 below.
    assert rows[0]["turn"] == 1
    assert rows[0]["tool_errors"] == 1
    assert rows[-1]["turn"] == 0
    assert rows[-1]["tools"] == 1
    assert "turn" in format_turns_plain(segs).lower()


def test_open_second_turn():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "turn_ended", "turn ended  outcome=success"),
        _ev(2, "turn_started", "turn started  turn_number=1"),
        _ev(3, "agent_message_chunk", "still going"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].open is False
    assert segs[1].open is True


def test_empty_timeline_returns_empty():
    assert segment_timeline_turns([]) == []


def test_turn_label_with_outcome():
    seg = segment_timeline_turns(
        [
            _ev(0, "turn_started", "turn started  turn_number=0"),
            _ev(1, "turn_ended", "turn ended  outcome=success"),
        ]
    )
    assert "success" in seg[0].label
    assert "0" in seg[0].label


def test_turn_label_plain_number():
    """When no markers, label uses turn_index 0."""
    tl = [_ev(0, "user_message_chunk", "hi")]
    seg = segment_timeline_turns(tl)[0]
    assert "0" in seg.label


def test_duration_seconds_large_delta_treated_as_ms():
    """Timestamps with absurd seconds delta should be treated as milliseconds."""
    seg = segment_timeline_turns(
        [
            _ev(0, "user_message_chunk", "hi", ts=0),
            _ev(1, "agent_message_chunk", "bye", ts=86_400 * 365 + 1),
        ]
    )
    dur = seg[0].duration_seconds()
    assert dur is not None
    # delta > 86400*365 triggers ms conversion
    assert dur == (86_400 * 365 + 1) / 1000.0


def test_duration_seconds_from_durations_map():
    """When fewer than 2 timestamps available, use the durations dict fallback."""
    tl = [_ev(0, "user_message_chunk", "hi")]
    tl[0].timestamp = None
    seg = segment_timeline_turns(tl)[0]
    # No timestamps, no durations map → None
    assert seg.duration_seconds() is None
    # With durations map for the event
    assert seg.duration_seconds(durations={0: 2.5}) == 2.5


def test_duration_seconds_durations_map_no_match():
    """Durations map present but no matching indices → None."""
    tl = [_ev(0, "user_message_chunk", "hi")]
    tl[0].timestamp = None
    seg = segment_timeline_turns(tl)[0]
    assert seg.duration_seconds(durations={99: 1.0}) is None


def test_turn_number_from_event_no_match():
    """Turn number regex returns None when content has no turn_number=N."""
    tl = [
        _ev(0, "turn_started", "turn started"),
        _ev(1, "turn_ended", "turn ended  outcome=unknown"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    # turn_number assigned from sequential 0-based index
    assert segs[0].turn_number == 0
    assert segs[0].turn_index == 0


def test_turn_ended_before_started_creates_segment():
    """A turn_ended appearing before any turn_started produces a segment."""
    tl = [_ev(0, "turn_ended", "turn ended  outcome=error")]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].outcome == "error"
    assert segs[0].open is False
    assert segs[0].turn_index == 0


def test_preamble_events_before_first_start():
    """User/agent events before first turn_started merge into turn 0 with the marker."""
    tl = [
        _ev(0, "user_message_chunk", "preamble question"),
        _ev(1, "turn_started", "turn started  turn_number=0"),
        _ev(2, "agent_message_chunk", "reply"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].turn_index == 0
    assert segs[0].turn_number == 0
    assert segs[0].user_count == 1
    assert segs[0].outcome == "success"


def test_system_prompt_is_session_level_not_a_turn():
    """Parser-injected system event is outside turn segments (not merged, not counted)."""
    from groket.session.turns import is_session_level_timeline_event

    tl = [
        _ev(0, "system", "You are Grok…"),
        _ev(1, "turn_started", "turn started  turn_number=0"),
        _ev(2, "user_message_chunk", "hi"),
        _ev(3, "agent_message_chunk", "hello"),
        _ev(4, "turn_ended", "turn ended  outcome=success"),
    ]
    assert is_session_level_timeline_event(tl[0])
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].turn_number == 0
    assert all(e.event_type != "system" for e in segs[0].events)
    assert segs[0].user_count == 1
    assert segs[0].outcome == "success"


def test_system_prompt_does_not_affect_multi_turn_count():
    """Session-level system chrome leaves harness turn count unchanged."""
    tl = [
        _ev(0, "system", "You are Grok…"),
        _ev(1, "turn_started", "turn started  turn_number=0"),
        _ev(2, "turn_ended", "turn ended  outcome=success"),
        _ev(3, "turn_started", "turn started  turn_number=1"),
        _ev(4, "user_message_chunk", "again"),
        _ev(5, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_number == 0
    assert segs[1].turn_number == 1
    assert segs[1].user_count == 1
    assert all(e.event_type != "system" for seg in segs for e in seg.events)


def test_system_only_timeline_has_no_turns():
    """A timeline that is only session-level chrome yields no turn segments."""
    tl = [_ev(0, "system", "You are Grok…")]
    assert segment_timeline_turns(tl) == []


def test_previous_turn_closed_on_new_start():
    """Turn started while previous is open → close previous."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "tool_call"),
        _ev(2, "turn_started", "turn started  turn_number=1"),
        _ev(3, "agent_message_chunk", "done"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].open is False
    assert segs[0].outcome == "unknown"
    assert segs[1].open is True


def test_error_event_count():
    tl = [_ev(0, "session_error", "boom", err=True), _ev(1, "user_message_chunk", "hi")]
    seg = segment_timeline_turns(tl)[0]
    assert seg.error_event_count == 1


def test_format_turns_plain_empty():
    assert format_turns_plain([]) == "(no turns)"


def test_format_turns_plain_with_duration():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=1"),
        _ev(1, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    text = format_turns_plain(segs, durations={0: 1.0, 1: 2.0})
    assert "turn" in text.lower()


def test_turn_summary_rows_structure():
    tl = [_ev(0, "user_message_chunk", "hi"), _ev(1, "agent_message_chunk", "bye")]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(segs)
    assert len(rows) == 1
    row = rows[0]
    assert "turn" in row
    assert "label" in row
    assert "events" in row
    assert row["users"] == 1
    assert row["assistants"] == 1
    assert row["context"] == ""


def test_turn_summary_rows_session_context_on_latest_only():
    tl = [
        _ev(0, "turn_started", "Turn started turn_number=0"),
        _ev(1, "user_message_chunk", "a"),
        _ev(2, "turn_ended", "Turn ended outcome=completed"),
        _ev(3, "turn_started", "Turn started turn_number=1"),
        _ev(4, "user_message_chunk", "b"),
        _ev(5, "turn_ended", "Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(segs, session_context_compact="35% 179k/500k")
    assert len(rows) >= 2
    # Newest first: session-level context attaches to the latest turn only.
    assert rows[0]["context"] == "35% 179k/500k"
    assert rows[0]["turn"] == segs[-1].turn_index
    assert rows[-1]["context"] == ""
    assert rows[-1]["turn"] == segs[0].turn_index


def test_turn_summary_rows_context_by_turn_samples():
    tl = [
        _ev(0, "turn_started", "Turn started turn_number=0"),
        _ev(1, "user_message_chunk", "a"),
        _ev(2, "turn_ended", "Turn ended outcome=completed"),
        _ev(3, "turn_started", "Turn started turn_number=1"),
        _ev(4, "user_message_chunk", "b"),
        _ev(5, "turn_ended", "Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(
        segs,
        session_context_compact="99% 1/1",
        context_by_turn={segs[0].turn_index: "10% 50k/500k", segs[-1].turn_index: "35% 179k/500k"},
    )
    # Newest first.
    assert rows[0]["context"] == "35% 179k/500k"
    assert rows[-1]["context"] == "10% 50k/500k"


def test_first_last_index_empty():
    from groket.session.turns import TurnSegment

    seg = TurnSegment(turn_index=1, turn_number=1, events=[])
    assert seg.first_index is None
    assert seg.last_index is None


def test_turn_label_no_outcome_no_open():
    """Closed turn without outcome → plain label."""
    from groket.session.turns import TurnSegment

    seg = TurnSegment(turn_index=3, turn_number=3, open=False, outcome="")
    assert seg.label == "turn 3"


def test_harness_zero_based_preserved():
    """Harness turn_number=0 is not renumbered to 1."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "turn_ended", "turn ended  outcome=success"),
        _ev(2, "turn_started", "turn started  turn_number=1"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1]
    assert [s.turn_number for s in segs] == [0, 1]


def test_events_between_turns_attach_to_previous_segment() -> None:
    """Late assistant after turn_ended must not become a fake Turn 1 alone."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="agent_message_chunk", content="reply A"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=success"),
        # Late/out-of-order stream chunk after end — must stay with turn 0
        TraceEvent(index=4, event_type="agent_message_chunk", content="late chunk of turn 0"),
        TraceEvent(index=5, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=6, event_type="user_message_chunk", content="follow-up"),
        TraceEvent(index=7, event_type="agent_message_chunk", content="reply B"),
        TraceEvent(index=8, event_type="turn_ended", content="Turn ended outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert any(e.content == "late chunk of turn 0" for e in segs[0].events)
    assert not any(e.content == "late chunk of turn 0" for e in segs[1].events)
    assert any(e.content == "follow-up" for e in segs[1].events)


def test_follow_up_user_before_next_turn_started_is_own_turn() -> None:
    """Interactive follow-up user msg before turn_started must not merge into turn 0."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="agent_message_chunk", content="reply A"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=success"),
        TraceEvent(index=4, event_type="user_message_chunk", content="follow-up prompt"),
        TraceEvent(index=5, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=6, event_type="agent_message_chunk", content="reply B"),
        TraceEvent(index=7, event_type="turn_ended", content="Turn ended outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert any(e.content == "follow-up prompt" for e in segs[1].events)
    assert not any(e.content == "follow-up prompt" for e in segs[0].events)
    assert segs[1].turn_number == 1


def test_segments_preserve_non_contiguous_prompt_indexes() -> None:
    events = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=1"),
        TraceEvent(
            index=1,
            event_type="user_message_chunk",
            content="first",
            prompt_index=4,
        ),
        TraceEvent(index=2, event_type="turn_ended", content="turn ended  outcome=success"),
        TraceEvent(index=3, event_type="turn_started", content="turn started  turn_number=2"),
        TraceEvent(
            index=4,
            event_type="user_message_chunk",
            content="second",
            prompt_index=9,
        ),
        TraceEvent(index=5, event_type="turn_ended", content="turn ended  outcome=success"),
    ]

    segments = segment_timeline_turns(events)

    assert [segment.turn_index for segment in segments] == [0, 1]
    assert [segment.prompt_index for segment in segments] == [4, 9]


def test_background_task_completion_turns_merge_into_parent() -> None:
    """Grok emits extra turn_started for background-task completions — fold into parent.

    Operator only issued the interactive prompt; synthetic turns with
    ``Background task "…"`` user chrome (or no operator user) belong under that turn.
    """
    from groket.models import TraceEvent

    bg_user = (
        '<system-reminder>\nBackground task "call-05172712-9431-4be8-bdf0-6a58f7cdb30a-162" '
        "completed.\n</system-reminder>"
    )
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="refactor the module"),
        TraceEvent(index=2, event_type="tool_call", tool_name="spawn_subagent"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=completed"),
        # Synthetic harness turns after background tasks finish
        TraceEvent(index=4, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=5, event_type="user_message_chunk", content=bg_user),
        TraceEvent(index=6, event_type="agent_message_chunk", content="task summary"),
        TraceEvent(index=7, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=8, event_type="turn_started", content="Turn started turn_number=2"),
        TraceEvent(index=9, event_type="agent_message_chunk", content="more completion chrome"),
        TraceEvent(index=10, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].turn_number == 0
    assert any(e.content == "refactor the module" for e in segs[0].events)
    assert any(e.content == bg_user for e in segs[0].events)
    assert any(e.content == "more completion chrome" for e in segs[0].events)


def test_task_completed_call_user_is_background_chrome() -> None:
    """``task-completed-call-…`` user payloads merge into the parent turn."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="operator"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(
            index=4, event_type="user_message_chunk", content="task-completed-call-abc-123 done"
        ),
        TraceEvent(index=5, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert any("task-completed-call-" in (e.content or "") for e in segs[0].events)


def test_background_user_between_turns_attaches_to_previous() -> None:
    """Background-task user chrome between turn_ended and turn_started stays on parent."""
    from groket.models import TraceEvent

    bg = 'Background task "x" completed.'
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="operator"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="user_message_chunk", content=bg),
        TraceEvent(index=4, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=5, event_type="user_message_chunk", content="real follow-up"),
        TraceEvent(index=6, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert any(e.content == bg for e in segs[0].events)
    assert any(e.content == "real follow-up" for e in segs[1].events)


def test_open_background_tail_keeps_parent_open() -> None:
    """Merging an open background-only segment marks the parent open."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="operator"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(
            index=4, event_type="user_message_chunk", content='Background task "y" completed.'
        ),
        # no turn_ended — open completion tail
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].open is True


def test_blank_user_event_is_not_operator() -> None:
    """Whitespace-only user rows do not count as operator prompts."""
    from groket.session.turns import TurnSegment, _segment_has_operator_user

    seg = TurnSegment(
        turn_index=0,
        turn_number=0,
        events=[TraceEvent(index=0, event_type="user_message_chunk", content="   ")],
    )
    assert _segment_has_operator_user(seg) is False


def test_real_follow_up_after_background_merge_stays_separate() -> None:
    """A real operator follow-up after a parent turn is still its own segment."""
    from groket.models import TraceEvent

    bg_user = 'Background task "call-abc" completed.'
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first prompt"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=4, event_type="user_message_chunk", content=bg_user),
        TraceEvent(index=5, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=6, event_type="user_message_chunk", content="real follow-up from host"),
        TraceEvent(index=7, event_type="turn_started", content="Turn started turn_number=2"),
        TraceEvent(index=8, event_type="agent_message_chunk", content="reply"),
        TraceEvent(index=9, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert any(e.content == "first prompt" for e in segs[0].events)
    assert any(e.content == bg_user for e in segs[0].events)
    assert any(e.content == "real follow-up from host" for e in segs[1].events)


def test_turn_index_for_event_mid_timeline() -> None:
    """Selected mid-session event maps to its segment, not the last turn."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "tool_call"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
        _ev(4, "turn_started", "turn started  turn_number=1"),
        _ev(5, "user_message_chunk", "second"),
        _ev(6, "tool_call"),
        _ev(7, "turn_ended", "turn ended  outcome=success"),
    ]
    tl[2] = TraceEvent(index=2, event_type="tool_call", tool_name="grep", timestamp=1_000_020)
    tl[6] = TraceEvent(index=6, event_type="tool_call", tool_name="bash", timestamp=1_000_060)
    segs = segment_timeline_turns(tl)
    assert turn_index_for_event(segs, 2) == 0
    assert turn_index_for_event(segs, 6) == 1
    assert turn_index_for_event(segs, 99) is None
