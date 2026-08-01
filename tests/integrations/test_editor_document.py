"""Editor-facing Org session projection."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from groket.notes import NoteEntry, NotesDoc, save_notes


def _render_editor_document(session_dir: Path):
    module = import_module("groket.integrations.editor")
    return module.render_editor_document(session_dir)


def _write_session(session_dir: Path) -> None:
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "sessionId": session_dir.name,
                "title": "Live parser review",
                "model": "test-model",
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "first\n* not a heading"},
                    "_meta": {"promptIndex": 4},
                }
            },
        },
        {
            "timestamp": 1002,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "first answer"},
                }
            },
        },
        {
            "timestamp": 2001,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "second"},
                    "_meta": {"promptIndex": 9},
                }
            },
        },
        {
            "timestamp": 2002,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "second answer"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(update) + "\n" for update in updates),
        encoding="utf-8",
    )
    markers = [
        {"type": "turn_started", "turn_number": 1, "ts": 1000},
        {"type": "turn_ended", "outcome": "success", "ts": 1100},
        {"type": "turn_started", "turn_number": 2, "ts": 2000},
        {"type": "turn_ended", "outcome": "success", "ts": 2100},
    ]
    (session_dir / "events.jsonl").write_text(
        "".join(json.dumps(marker) + "\n" for marker in markers),
        encoding="utf-8",
    )


def test_render_editor_document_uses_prompt_indexes_and_note_properties(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-editor"
    session_dir.mkdir()
    _write_session(session_dir)
    note = NoteEntry.new(
        turn_index=1,
        fields={"summary": "Wrong branch", "detail": "The check used a stale ref."},
        event_indices=[3, 4],
        note_id="n-review",
    )
    save_notes(
        session_dir,
        NotesDoc(session_id=session_dir.name, notes=[note]),
    )

    document = _render_editor_document(session_dir)

    assert document.session_id == session_dir.name
    assert document.prompt_indexes == (4, 9)
    assert len(document.notes_revision) == 64
    assert f"#+PROPERTY: GROKET_SESSION_ID {session_dir.name}" in document.text
    assert "* Prompt 4" in document.text
    assert "* Prompt 9" in document.text
    assert ":GROKET_PROMPT_INDEX: 9" in document.text
    assert ": * not a heading" in document.text
    assert ":GROKET_NOTE_ID: n-review" in document.text
    assert ":GROKET_EVENT_INDICES: 3,4" in document.text
    assert ":GROKET_FIELD_ID: summary" in document.text
    assert "Wrong branch" in document.text


def test_render_editor_document_uses_turn_index_when_prompt_metadata_is_absent(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "legacy-session"
    session_dir.mkdir()
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "legacy"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    document = _render_editor_document(session_dir)

    assert document.prompt_indexes == (0,)
    assert "* Prompt 0" in document.text
