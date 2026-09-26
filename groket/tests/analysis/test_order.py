"""Turn-based finding order for Findings / Report surfaces."""

from __future__ import annotations

from groket.analysis.base import Finding
from groket.analysis.order import (
    order_report_markdown_by_turn,
    parse_turn_hint,
    sort_findings_by_turn,
)
from groket.models import Severity, TraceEvent


def _finding(
    fid: str,
    *,
    severity: Severity = Severity.MEDIUM,
    event_indices: list[int] | None = None,
    tool_call_ids: list[str] | None = None,
    title: str | None = None,
    extras: dict | None = None,
) -> Finding:
    return Finding(
        id=fid,
        plugin_id="p",
        severity=severity,
        title=title or fid,
        event_indices=list(event_indices or []),
        tool_call_ids=list(tool_call_ids or []),
        extras=dict(extras or {}),
    )


def test_parse_turn_hint() -> None:
    assert parse_turn_hint("Turn 3") == 3
    assert parse_turn_hint("Turns 2–4") == 2
    assert parse_turn_hint("Turns 2-4") == 2
    assert parse_turn_hint("turn 0") == 0
    assert parse_turn_hint("") is None
    assert parse_turn_hint(None) is None
    assert parse_turn_hint("no numbers") is None


def test_sort_by_turn_then_severity() -> None:
    timeline = [
        TraceEvent(index=0, event_type="session", content="Turn started turn_number=1"),
        TraceEvent(index=1, event_type="user_message_chunk", content="t1"),
        TraceEvent(index=2, event_type="tool_call", tool_name="bash", tool_call_id="a"),
        TraceEvent(index=3, event_type="session", content="Turn ended outcome=success"),
        TraceEvent(index=10, event_type="session", content="Turn started turn_number=2"),
        TraceEvent(index=11, event_type="user_message_chunk", content="t2"),
        TraceEvent(index=12, event_type="tool_call", tool_name="bash", tool_call_id="b"),
        TraceEvent(index=13, event_type="session", content="Turn ended outcome=success"),
    ]
    late_high = _finding("late-high", severity=Severity.HIGH, event_indices=[12])
    early_low = _finding("early-low", severity=Severity.LOW, event_indices=[2])
    early_high = _finding("early-high", severity=Severity.HIGH, event_indices=[1])
    unlinked = _finding("none", severity=Severity.HIGH)

    ordered = sort_findings_by_turn(
        [late_high, unlinked, early_low, early_high],
        timeline,
    )
    assert [f.id for f in ordered] == ["early-high", "early-low", "late-high", "none"]


def test_sort_prefers_extras_turn_over_event() -> None:
    """MF free-text Turn: wins when evidence maps to a different turn."""
    timeline = [
        TraceEvent(index=0, event_type="session", content="Turn started turn_number=1"),
        TraceEvent(index=1, event_type="user_message_chunk", content="t1"),
        TraceEvent(index=2, event_type="session", content="Turn ended outcome=success"),
        TraceEvent(index=3, event_type="session", content="Turn started turn_number=5"),
        TraceEvent(index=4, event_type="user_message_chunk", content="t5"),
        TraceEvent(index=5, event_type="session", content="Turn ended outcome=success"),
    ]
    # Evidence in turn 1, but form says Turn 5
    a = _finding("labeled-5", event_indices=[1], extras={"turn": "Turn 5"})
    b = _finding("labeled-1", event_indices=[4], extras={"turn": "Turn 1"})
    ordered = sort_findings_by_turn([a, b], timeline)
    assert [f.id for f in ordered] == ["labeled-1", "labeled-5"]


def test_sort_via_tool_call_id_when_no_event_indices() -> None:
    """Tool-call anchors map to chronological display turns (segment order).

    Harness ``turn_number`` may skip or reorder; findings sort by sequential
    turn_index after segmentation (first segment first).
    """
    timeline = [
        TraceEvent(index=0, event_type="session", content="Turn started turn_number=5"),
        TraceEvent(index=1, event_type="tool_call", tool_name="bash", tool_call_id="c-late"),
        TraceEvent(index=2, event_type="session", content="Turn ended outcome=success"),
        TraceEvent(index=3, event_type="session", content="Turn started turn_number=1"),
        TraceEvent(index=4, event_type="tool_call", tool_name="bash", tool_call_id="c-early"),
        TraceEvent(index=5, event_type="session", content="Turn ended outcome=success"),
    ]
    a = _finding("via-late", tool_call_ids=["c-late"])
    b = _finding("via-early", tool_call_ids=["c-early"])
    ordered = sort_findings_by_turn([a, b], timeline)
    assert [f.id for f in ordered] == ["via-late", "via-early"]


def test_sort_empty() -> None:
    assert sort_findings_by_turn([], []) == []


def test_order_report_markdown_by_turn() -> None:
    md = """# Model Feedback form drafts — sess

## Session summary

Overall notes.

## Issue 1: Late problem

### Form fields (copy line-by-line)

```
Model Name: m
Turn: Turn 5
```

### Issue (copy into the Issue box)

```
What: late
Where: Turn 5
```

## Issue 2: Early problem

### Form fields (copy line-by-line)

```
Model Name: m
Turn: Turn 1
```

### Issue (copy into the Issue box)

```
What: early
Where: Turn 1
```

---

_Tip: submit the highest-severity issue first._
"""
    ordered = order_report_markdown_by_turn(md)
    early_at = ordered.index("Early problem")
    late_at = ordered.index("Late problem")
    assert early_at < late_at
    assert "## Issue 1: Early problem" in ordered
    assert "## Issue 2: Late problem" in ordered
    assert ordered.index("Session summary") < early_at
    assert ordered.index("_Tip:") > late_at


def test_order_report_markdown_noop_single_issue() -> None:
    md = "## Issue 1: Only one\n\nTurn: Turn 9\n"
    assert "Only one" in order_report_markdown_by_turn(md)
