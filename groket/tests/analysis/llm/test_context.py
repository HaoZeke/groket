"""Tests for session context pack and runtime policy."""

from __future__ import annotations

from pathlib import Path

from groket.analysis.llm.context import (
    RuntimePolicy,
    build_session_context_pack,
    build_timeline_digest,
    is_operator_user,
    load_runtime_policy,
    operator_instructions_block,
)
from groket.models import SessionMeta, TraceEvent
from groket.session.turns import TurnSegment


def test_runtime_policy_bullets_and_constraints() -> None:
    p = RuntimePolicy(
        permission_mode="always-approve",
        yolo=True,
        sandbox_profile="off",
        non_interactive=True,
        memory_enabled=False,
        model_id="m",
        reasoning_effort="high",
        agent_name="a",
        plugins_enabled=("x",),
        bash_background=True,
        plan_mode_used=True,
        working_directory="/w",
        compact_mode=True,
        config_file="groket-config.toml",
        context_window_usage_pct=50,
        tools_used=("read_file",),
    )
    bullets = p.as_bullet_lines()
    assert any("always-approve" in b for b in bullets)
    constraints = p.review_constraints()
    assert any("approval" in c.lower() for c in constraints)


def test_operator_user_filters_background() -> None:
    assert is_operator_user(TraceEvent(index=1, event_type="user_message_chunk", content="do it"))
    assert not is_operator_user(
        TraceEvent(
            index=2,
            event_type="user_message_chunk",
            content="<system-reminder>\nBackground task done",
        )
    )
    assert not is_operator_user(TraceEvent(index=3, event_type="tool_call", tool_name="x"))


def test_digest_and_operator_block() -> None:
    timeline = [
        TraceEvent(index=1, event_type="turn_started", content="turn started"),
        TraceEvent(index=2, event_type="user_message_chunk", content="setup"),
        TraceEvent(index=3, event_type="tool_call", tool_name="read_file"),
        TraceEvent(index=4, event_type="tool_call", tool_name="read_file"),
        TraceEvent(
            index=5,
            event_type="tool_call",
            tool_name="search_replace",
            tool_call_id="c1",
        ),
        TraceEvent(
            index=6,
            event_type="tool_call_update",
            tool_name="search_replace",
            is_error=True,
            content="fail",
        ),
        TraceEvent(index=7, event_type="agent_message_chunk", content="done"),
    ]
    turns = [
        TurnSegment(turn_index=0, turn_number=0, events=timeline[1:]),
    ]
    digest, trunc = build_timeline_digest(timeline, turns, max_chars=50_000)
    assert "USER" in digest
    assert "READS" in digest or "read_file" in digest
    assert "ERR" in digest
    ops = operator_instructions_block(timeline, turns)
    assert "setup" in ops


def test_build_pack_from_files(tmp_path: Path) -> None:
    # Minimal events.jsonl-less: parse_timeline returns empty if no files
    (tmp_path / "summary.json").write_text(
        '{"info":{"id":"sid","cwd":"/workspace"},'
        '"current_model_id":"m1","reasoning_effort":"high",'
        '"sandbox_profile":"off","agent_name":"ag"}',
        encoding="utf-8",
    )
    (tmp_path / "prompt_context.json").write_text(
        '{"is_non_interactive":true,"memory_enabled":false,"working_directory":"/workspace"}',
        encoding="utf-8",
    )
    (tmp_path / "signals.json").write_text(
        '{"contextWindowUsage":40,"toolsUsed":["read_file"]}',
        encoding="utf-8",
    )
    (tmp_path / "plan_mode.json").write_text(
        '{"state":"Inactive","was_previously_active":true}',
        encoding="utf-8",
    )
    (tmp_path / "resources_state.json").write_text(
        '{"params":{"grok_build.Bash":{"enabled_background":true}}}',
        encoding="utf-8",
    )
    parent = tmp_path.parent
    (parent / "groket-config.toml").write_text(
        '[ui]\npermission_mode = "always-approve"\nyolo = false\n'
        'compact_mode = true\n\n[plugins]\nenabled = ["p1", "p2"]\n',
        encoding="utf-8",
    )
    pack = build_session_context_pack(tmp_path)
    assert pack.runtime.permission_mode == "always-approve"
    assert pack.runtime.non_interactive is True
    assert "p1" in pack.runtime.plugins_enabled
    assert pack.format_meta()
    assert "always-approve" in pack.format_runtime()
    assert pack.format_constraints()
    assert pack.format_timeline_digest()
    assert pack.format_operator_notes() == ""
    assert pack.operator_notes.notes == []


def test_build_pack_includes_operator_notes(tmp_path: Path) -> None:
    from groket.notes import NoteEntry, NotesDoc, save_notes

    (tmp_path / "summary.json").write_text(
        '{"info":{"id":"sid"}}',
        encoding="utf-8",
    )
    doc = NotesDoc(session_id=tmp_path.name)
    doc.upsert(
        NoteEntry.new(
            turn_index=1,
            fields={
                "summary": "missed tests",
                "detail": "should have run make test",
            },
            event_indices=[4, 9],
            note_id="n-focus",
        )
    )
    save_notes(tmp_path, doc)
    pack = build_session_context_pack(tmp_path)
    assert len(pack.operator_notes.notes) == 1
    formatted = pack.format_operator_notes()
    assert "OPERATOR NOTES" in formatted
    assert "missed tests" in formatted
    assert "make test" in formatted
    assert "#4" in formatted
    assert "n-focus" in formatted


def test_load_runtime_inferred_permission(tmp_path: Path) -> None:
    (tmp_path / "prompt_context.json").write_text(
        '{"is_non_interactive":true}',
        encoding="utf-8",
    )
    meta = SessionMeta(session_id="s", session_dir=tmp_path, model_id="m")
    pol = load_runtime_policy(tmp_path, meta)
    assert "always-approve" in pol.permission_mode
