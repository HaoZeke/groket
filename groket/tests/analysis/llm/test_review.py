"""Tests for review mapping, incompleteness, and report rendering."""

from __future__ import annotations

from pathlib import Path

from groket.analysis.llm.context import SessionContextPack
from groket.analysis.llm.review import (
    is_incomplete_review,
    map_review_findings,
    render_prompt_envelope,
    render_review_report,
)
from groket.models import SessionMeta, Severity, TraceEvent


def test_incomplete_offloaded_summary() -> None:
    assert is_incomplete_review(
        {
            "summary": "Reading the full offloaded prompt before producing the review.",
            "all_clear": False,
            "findings": [],
        }
    )


def test_incomplete_drafting_placeholder() -> None:
    assert is_incomplete_review(
        {
            "summary": (
                "Full timeline was truncated in the chat; reading the offloaded "
                "prompt so the review can cite complete turn evidence."
            ),
            "all_clear": False,
            "findings": [],
        }
    )


def test_incomplete_empty_findings_not_all_clear() -> None:
    assert is_incomplete_review(
        {
            "summary": "Something wrong",
            "all_clear": False,
            "findings": [],
        }
    )


def test_complete_all_clear() -> None:
    assert not is_incomplete_review(
        {
            "summary": "Looks fine.",
            "all_clear": True,
            "findings": [],
        }
    )


def test_complete_with_findings() -> None:
    assert not is_incomplete_review(
        {
            "summary": "Bad.",
            "all_clear": False,
            "findings": [
                {
                    "id": "a",
                    "severity": "high",
                    "title": "t",
                    "what_model_did": "did",
                    "what_should_have_done": "should",
                    "why_mistake": "why",
                    "evidence": [],
                }
            ],
        }
    )


def test_map_and_report(tmp_path: Path) -> None:
    # Minimal fake session via empty timeline pack construction helpers
    meta = SessionMeta(session_id="s1", session_dir=tmp_path)
    timeline = [
        TraceEvent(index=2, event_type="user_message_chunk", content="do x"),
        TraceEvent(
            index=4,
            event_type="tool_call",
            tool_name="search_replace",
            tool_call_id="c1",
            update_index=3,
        ),
    ]
    from groket.analysis.llm.context import RuntimePolicy
    from groket.session.turns import TurnSegment

    pack = SessionContextPack(
        session_dir=tmp_path,
        meta=meta,
        timeline=timeline,
        turns=[TurnSegment(turn_index=0, turn_number=0, events=timeline)],
        operator_instructions="#2 USER | do x",
        timeline_digest="#2 USER | do x\n#4 TOOL search_replace id=c1",
        digest_truncated=False,
        runtime=RuntimePolicy(permission_mode="always-approve"),
    )
    payload = {
        "summary": "Violated ask.",
        "all_clear": False,
        "findings": [
            {
                "id": "v1",
                "severity": "high",
                "title": "Did wrong thing",
                "category": "Instruction",
                "what_model_did": "Edited wrong file",
                "what_should_have_done": "Do x only",
                "why_mistake": "Ignored user",
                "evidence": [
                    {"event_index": 2, "note": "user"},
                    {"event_index": 4, "tool_call_id": "c1", "note": "edit"},
                    {"event_index": 999},
                ],
            }
        ],
    }
    findings = map_review_findings(payload, timeline, plugin_id="feedback")
    assert len(findings) == 1
    f = findings[0]
    assert f.event_indices == [2, 4]
    assert "c1" in f.tool_call_ids
    assert "\n" not in f.detail  # one_line
    assert f.severity == Severity.HIGH
    report = render_review_report(payload, findings, pack)
    assert "What the model did" in report
    assert "What it should have done" in report
    assert "#2" in report or "#4" in report
    env = render_prompt_envelope(pack, "Rubric here.")
    assert "Rubric here." in env
    assert "always-approve" in env
    assert "#2 USER" in env or "do x" in env
    assert "OPERATOR NOTES" in env  # guidance in preamble even when none stored
    assert "<operator_notes>" not in env


def test_prompt_envelope_includes_operator_notes(tmp_path: Path) -> None:
    from groket.analysis.llm.context import RuntimePolicy
    from groket.notes import NoteEntry, NotesDoc
    from groket.session.turns import TurnSegment

    meta = SessionMeta(session_id="s-notes", session_dir=tmp_path)
    notes = NotesDoc(session_id="s-notes")
    notes.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "check shell risk", "detail": "rm -rf looked broad"},
            event_indices=[12],
            note_id="n-shell",
        )
    )
    pack = SessionContextPack(
        session_dir=tmp_path,
        meta=meta,
        timeline=[],
        turns=[TurnSegment(turn_index=0, turn_number=0, events=[])],
        operator_instructions="#1 USER | fix it",
        timeline_digest="#1 USER | fix it",
        digest_truncated=False,
        runtime=RuntimePolicy(),
        operator_notes=notes,
    )
    env = render_prompt_envelope(pack, "Rubric.")
    assert "<operator_notes>" in env
    assert "check shell risk" in env
    assert "rm -rf looked broad" in env
    assert "#12" in env


def test_map_full_fields_detail() -> None:
    timeline = [TraceEvent(index=1, event_type="user_message_chunk", content="hi")]
    payload = {
        "findings": [
            {
                "id": "x",
                "severity": "low",
                "title": "T",
                "what_model_did": "A",
                "what_should_have_done": "B",
                "why_mistake": "C",
                "evidence": [{"event_index": 1}],
            }
        ],
    }
    findings = map_review_findings(payload, timeline, plugin_id="p", detail_mode="full_fields")
    assert "What the model did" in findings[0].detail
    assert "\n" in findings[0].detail


def test_map_empty_payload() -> None:
    assert map_review_findings({}, [], plugin_id="p") == []
    assert map_review_findings({"findings": [None, "x"]}, [], plugin_id="p") == []


def test_map_orders_findings_by_turn() -> None:
    timeline = [
        TraceEvent(index=0, event_type="session", content="Turn started turn_number=1"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="session", content="Turn ended outcome=success"),
        TraceEvent(index=3, event_type="session", content="Turn started turn_number=2"),
        TraceEvent(index=4, event_type="user_message_chunk", content="second"),
        TraceEvent(index=5, event_type="session", content="Turn ended outcome=success"),
    ]
    payload = {
        "summary": "Two issues",
        "all_clear": False,
        "findings": [
            {
                "id": "later",
                "severity": "high",
                "title": "Later turn issue",
                "what_model_did": "bad later",
                "what_should_have_done": "ok later",
                "why_mistake": "why later",
                "evidence": [{"event_index": 4}],
            },
            {
                "id": "earlier",
                "severity": "low",
                "title": "Earlier turn issue",
                "what_model_did": "bad early",
                "what_should_have_done": "ok early",
                "why_mistake": "why early",
                "evidence": [{"event_index": 1}],
            },
        ],
    }
    findings = map_review_findings(payload, timeline, plugin_id="feedback")
    assert [f.title for f in findings] == ["Earlier turn issue", "Later turn issue"]

    from groket.analysis.llm.context import RuntimePolicy, SessionContextPack
    from groket.session.turns import TurnSegment

    pack = SessionContextPack(
        session_dir=Path("/tmp"),
        meta=SessionMeta(session_id="s-order", session_dir=Path("/tmp")),
        timeline=timeline,
        turns=[
            TurnSegment(turn_index=0, turn_number=1, events=timeline[0:3]),
            TurnSegment(turn_index=1, turn_number=2, events=timeline[3:6]),
        ],
        operator_instructions="",
        timeline_digest="",
        digest_truncated=False,
        runtime=RuntimePolicy(),
    )
    report = render_review_report(payload, findings, pack)
    earlier_at = report.index("Earlier turn issue")
    later_at = report.index("Later turn issue")
    assert earlier_at < later_at
    assert "## 1. Earlier turn issue" in report
    assert "## 2. Later turn issue" in report
