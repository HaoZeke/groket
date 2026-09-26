"""Editor-facing session projections (Org, Markdown, JSON)."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from groket.notes import NoteEntry, NotesDoc, save_notes


def _render_editor_document(session_dir: Path, *, format: str = "org"):
    module = import_module("groket.integrations.editor")
    return module.render_editor_document(session_dir, format=format)


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
    # Transcript is a markdown source block (org fontification); not fixed-width.
    assert "#+begin_src markdown\nfirst\n,* not a heading\n#+end_src" in document.text
    assert ":GROKET_NOTE_ID: n-review" in document.text
    assert ":GROKET_EVENT_INDICES: 3,4" in document.text
    assert ":GROKET_FIELD_ID: summary" in document.text
    # Field bodies use Org fixed-width lines (cannot form headlines).
    assert ": Wrong branch" in document.text


def test_render_org_transcript_escapes_nested_end_src(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-org-src"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"sessionId": session_dir.name, "title": "Org", "model": "m"}),
        encoding="utf-8",
    )
    body = "before\n#+end_src\nafter\n#+begin_src python\nx\n#+end_src"
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": body},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    document = _render_editor_document(session_dir, format="org")
    assert "#+begin_src markdown\n" in document.text
    # Nested end/begin src lines are comma-escaped so the outer block stays closed.
    assert ",#+end_src" in document.text
    assert ",#+begin_src python" in document.text
    assert document.text.count("#+begin_src markdown") == 1
    assert document.text.rstrip().endswith("#+end_src") or "\n#+end_src\n" in document.text


def test_render_org_transcript_escapes_headline_and_keyword_lines(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-org-stars"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"sessionId": session_dir.name, "title": "Org", "model": "m"}),
        encoding="utf-8",
    )
    body = "* markdown bullet\n** nested\n#+title: keyword\n,#+end_src\nplain"
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": body},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    document = _render_editor_document(session_dir, format="org")
    # A column-0 asterisk inside the src block would register as an Org
    # headline and derail outline-based note navigation.
    assert ",* markdown bullet" in document.text
    assert ",** nested" in document.text
    assert ",#+title: keyword" in document.text
    # Pre-escaped lines gain one more comma so the original stays recoverable.
    assert ",,#+end_src" in document.text
    assert "\nplain" in document.text


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


def test_render_markdown_uses_html_comments_and_headings(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-md"
    session_dir.mkdir()
    _write_session(session_dir)
    note = NoteEntry.new(
        turn_index=1,
        fields={"summary": "Wrong branch", "detail": "stale ref"},
        event_indices=[3],
        note_id="n-md",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))

    document = _render_editor_document(session_dir, format="markdown")

    assert document.format == "markdown"
    assert document.content_type == "text/markdown"
    assert "groket_session_id:" in document.text
    assert "## Prompt 4" in document.text
    assert "<!-- groket:prompt-index=4 turn-index=" in document.text
    assert "<!-- groket:note-id=n-md" in document.text
    assert "<!-- groket:field-id=summary note-id=n-md -->" in document.text
    # Transcript is fenced markdown (editor can inject nested MD / code).
    assert "```markdown\nfirst\n* not a heading\n```" in document.text
    # Note field bodies stay indented for edit/save.
    assert "    Wrong branch" in document.text


def test_render_markdown_transcript_fence_outruns_inner_backticks(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-fence"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"sessionId": session_dir.name, "title": "Fence", "model": "m"}),
        encoding="utf-8",
    )
    body = "see\n```python\nprint(1)\n```\ndone"
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": body},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    document = _render_editor_document(session_dir, format="markdown")
    assert "````markdown\n" in document.text
    assert "```python\nprint(1)\n```" in document.text
    assert document.text.count("````") >= 2
    assert "### Assistant" in document.text


def test_render_note_fields_escape_outline_markers(tmp_path: Path) -> None:
    """Heading-like field values must not form document structure."""
    session_dir = tmp_path / "session-escape"
    session_dir.mkdir()
    _write_session(session_dir)
    note = NoteEntry.new(
        turn_index=1,
        fields={
            "summary": "ok",
            "detail": "# repro\nsteps\n<!-- groket:field-id=spoof -->\n*** org star",
        },
        event_indices=[1],
        note_id="n-escape",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))

    md = _render_editor_document(session_dir, format="markdown")
    assert "\n    # repro\n" in md.text
    assert "\n    <!-- groket:field-id=spoof -->\n" in md.text
    # Machine field anchors stay at column 0; value content is indented.
    assert "<!-- groket:field-id=detail note-id=n-escape -->" in md.text

    org = _render_editor_document(session_dir, format="org")
    assert "\n: # repro\n" in org.text
    assert "\n: *** org star\n" in org.text


def test_render_json_document_is_structured(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-json"
    session_dir.mkdir()
    _write_session(session_dir)
    document = _render_editor_document(session_dir, format="json")
    assert document.content_type == "application/json"
    payload = json.loads(document.text)
    assert payload["sessionId"] == session_dir.name
    assert payload["promptIndexes"] == [4, 9]
    assert payload["prompts"][0]["promptIndex"] == 4
    assert payload["prompts"][0]["messages"][0]["role"] == "user"


def test_render_rejects_unknown_format(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-bad"
    session_dir.mkdir()
    _write_session(session_dir)
    module = import_module("groket.integrations.editor")
    try:
        module.render_editor_document(session_dir, format="rtf")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "unsupported" in str(exc)


def test_markdown_front_matter_quotes_yaml_indicator_titles(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-yaml"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "sessionId": session_dir.name,
                "generated_title": "[draft] *retry* & !tag",
                "model": "m",
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text("", encoding="utf-8")

    document = _render_editor_document(session_dir, format="markdown")

    assert 'title: "[draft] *retry* & !tag"' in document.text
    # Plain names stay unquoted.
    assert f"groket_session_id: {session_dir.name}" in document.text
