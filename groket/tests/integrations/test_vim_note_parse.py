"""Neovim Markdown note round-trip (``note_at_row``) — skip without nvim."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from importlib import import_module
from pathlib import Path

import pytest
from groket.notes import NoteEntry, NotesDoc, save_notes


def _write_session(session_dir: Path, user_text: str = "hello") -> None:
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "sessionId": session_dir.name,
                "title": "Note parse",
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
                    "content": {"type": "text", "text": user_text},
                    "_meta": {"promptIndex": 1},
                }
            },
        },
        {
            "timestamp": 1002,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "world"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates),
        encoding="utf-8",
    )
    markers = [
        {"type": "turn_started", "turn_number": 1, "ts": 1000},
        {"type": "turn_ended", "outcome": "success", "ts": 1100},
    ]
    (session_dir / "events.jsonl").write_text(
        "".join(json.dumps(m) + "\n" for m in markers),
        encoding="utf-8",
    )


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_note_at_row_round_trips_heading_field_values(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-nvim-note"
    session_dir.mkdir()
    _write_session(session_dir)
    detail = "# repro\nsteps here\n<!-- groket:field-id=spoof -->\nmore"
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "Title line", "detail": detail},
        event_indices=[2],
        note_id="n-round",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))

    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")

    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    assert (vim_root / "lua" / "groket" / "init.lua").is_file()

    # Find a line inside the detail field body for cursor placement.
    detail_anchor = "<!-- groket:field-id=detail note-id=n-round -->"
    lines = document.text.splitlines()
    cursor_row = next(i + 2 for i, line in enumerate(lines) if line == detail_anchor)

    harness = tmp_path / "note_roundtrip.lua"
    out_json = tmp_path / "note_out.json"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local path = {str(md_path)!r}
            local lines = vim.fn.readfile(path)
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local note = groket._note_at_row(buf, {cursor_row})
            vim.fn.writefile({{ vim.json.encode(note) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    assert out_json.is_file(), f"no output:\n{proc.stdout}\n{proc.stderr}"

    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["id"] == "n-round"
    assert parsed["fields"]["summary"] == "Title line"
    assert parsed["fields"]["detail"] == detail


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_note_at_row_works_on_note_and_field_headings(tmp_path: Path) -> None:
    """Cursor on ``####`` / ``#####`` must resolve note-id (tag is on next line)."""
    session_dir = tmp_path / "session-nvim-heading"
    session_dir.mkdir()
    _write_session(session_dir)
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "Head save", "detail": "body"},
        event_indices=[1],
        note_id="n-head",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    lines = document.text.splitlines()
    note_heading_row = next(i + 1 for i, line in enumerate(lines) if line.startswith("#### "))
    # Label casing depends on notes schema under isolated APP_HOME.
    field_heading_row = next(i + 1 for i, line in enumerate(lines) if line.startswith("##### "))
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "heading_out.json"
    harness = tmp_path / "heading.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local from_note = groket._note_at_row(buf, {note_heading_row})
            local from_field = groket._note_at_row(buf, {field_heading_row})
            vim.fn.writefile({{
              vim.json.encode({{
                note_id = from_note.id,
                field_id = from_field.id,
                summary = from_note.fields.summary,
              }})
            }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["note_id"] == "n-head"
    assert payload["field_id"] == "n-head"
    assert payload["summary"] == "Head save"


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_jump_to_note_lands_on_field_body(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-nvim-jump"
    session_dir.mkdir()
    _write_session(session_dir)
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "jump here", "detail": "x"},
        event_indices=[1],
        note_id="n-jump",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "jump_out.json"
    harness = tmp_path / "jump.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            vim.api.nvim_set_current_buf(buf)
            local win = vim.api.nvim_get_current_win()
            vim.api.nvim_win_set_cursor(win, {{ 1, 0 }})
            groket._jump_to_note(buf, "n-jump")
            local row = vim.api.nvim_win_get_cursor(win)[1]
            local line = vim.api.nvim_buf_get_lines(buf, row - 1, row, false)[1]
            vim.fn.writefile({{
              vim.json.encode({{ row = row, line = line }})
            }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    # First field body is the indented summary value.
    assert "jump here" in payload["line"]
    assert payload["row"] > 1


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_note_at_row_keeps_user_hash_headings_in_field(tmp_path: Path) -> None:
    """Bare ``#`` inside a field must not truncate the note on save-parse."""
    session_dir = tmp_path / "session-nvim-hash"
    session_dir.mkdir()
    _write_session(session_dir)
    detail = "# repro\nsteps\n## also fine\nmore"
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "s", "detail": detail},
        event_indices=[1],
        note_id="n-hash",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    lines = document.text.splitlines()
    # Cursor on first field body line after detail tag (indented).
    detail_tag = next(i for i, line in enumerate(lines) if "field-id=detail" in line)
    cursor_row = detail_tag + 2  # 1-based: tag is detail_tag+1, body often next+1
    # Find an indented body line under detail.
    for i in range(detail_tag + 1, len(lines)):
        if lines[i].startswith("    # repro"):
            cursor_row = i + 1
            break
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "hash_out.json"
    harness = tmp_path / "hash.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local note = groket._note_at_row(buf, {cursor_row})
            vim.fn.writefile({{ vim.json.encode(note.fields) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    fields = json.loads(out_json.read_text(encoding="utf-8"))
    assert fields["detail"] == detail


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_fenced_transcript_cannot_forge_note_tags(tmp_path: Path) -> None:
    """Machine tags inside a ```markdown transcript fence are not notes."""
    session_dir = tmp_path / "session-nvim-forge"
    session_dir.mkdir()
    forged = (
        "<!-- groket:note-id=n-forged -->\n"
        "#### forged heading\n"
        "<!-- groket:note-id=n-forged-heading -->\n"
        "tail line"
    )
    _write_session(session_dir, user_text=forged)
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "real one", "detail": "real body"},
        event_indices=[1],
        note_id="n-real",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    # The forged tag must reach the buffer at column 0 for this test to mean anything.
    assert "\n<!-- groket:note-id=n-forged -->\n" in document.text
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    lines = document.text.splitlines()
    detail_tag = next(i for i, line in enumerate(lines) if "field-id=detail" in line)
    cursor_row = detail_tag + 3  # 1-based: tag, blank separator, first body line
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "forge_out.json"
    harness = tmp_path / "forge.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local note = groket._note_at_row(buf, {cursor_row})
            -- Same discovery save_all_notes performs over the whole buffer.
            local fences = groket._fence_map(lines)
            local note_ids = {{}}
            for i, line in ipairs(lines) do
              if not fences[i] then
                local meta = groket._parse_groket_comment(line)
                if meta and meta["note-id"] and not meta["field-id"] then
                  table.insert(note_ids, meta["note-id"])
                end
              end
            end
            vim.fn.writefile({{
              vim.json.encode({{
                id = note.id,
                detail = note.fields.detail,
                summary = note.fields.summary,
                note_ids = note_ids,
              }})
            }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["id"] == "n-real"
    assert payload["detail"] == "real body"
    assert payload["summary"] == "real one"
    assert payload["note_ids"] == ["n-real"]


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_note_at_row_resolves_the_note_it_sits_in(tmp_path: Path) -> None:
    """Every row of note 2 resolves to note 2; transcript rows resolve to nothing."""
    session_dir = tmp_path / "session-nvim-two"
    session_dir.mkdir()
    _write_session(session_dir)
    notes = [
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "first", "detail": "one"},
            event_indices=[1],
            note_id="n-1",
        ),
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "second", "detail": "two"},
            event_indices=[2],
            note_id="n-2",
        ),
    ]
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=notes))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    lines = document.text.splitlines()
    tag_row = next(
        i + 1 for i, line in enumerate(lines) if line.startswith("<!-- groket:note-id=n-2 ")
    )
    heading_row = tag_row - 1
    assert lines[heading_row - 1].startswith("#### ")
    field_rows = [
        i + 1 for i, line in enumerate(lines) if line.startswith("##### ") and i + 1 > tag_row
    ]
    field_heading_row = field_rows[0]
    body_row = field_heading_row + 3  # heading, field tag, blank separator, body
    between_row = field_rows[1] - 1  # blank line separating the two fields
    assert lines[between_row - 1] == ""
    fence_row = next(i + 1 for i, line in enumerate(lines) if line.startswith("```"))
    transcript_row = fence_row + 1
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "two_out.json"
    harness = tmp_path / "two.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local function id_at(row)
              return groket._note_at_row(buf, row).id
            end
            local ok_transcript = pcall(groket._note_at_row, buf, {transcript_row})
            vim.fn.writefile({{
              vim.json.encode({{
                heading = id_at({heading_row}),
                tag = id_at({tag_row}),
                field = id_at({field_heading_row}),
                body = id_at({body_row}),
                between = id_at({between_row}),
                summary = groket._note_at_row(buf, {heading_row}).fields.summary,
                transcript_ok = ok_transcript,
                transcript_id = groket._note_id_at_row(lines, {transcript_row}),
              }})
            }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["heading"] == "n-2"
    assert payload["tag"] == "n-2"
    assert payload["field"] == "n-2"
    assert payload["body"] == "n-2"
    assert payload["between"] == "n-2"
    assert payload["summary"] == "second"
    assert payload["transcript_ok"] is False
    assert "transcript_id" not in payload


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_field_values_keep_leading_and_trailing_blank_lines(tmp_path: Path) -> None:
    """Blank lines inside a value survive render → parse untouched."""
    session_dir = tmp_path / "session-nvim-blank"
    session_dir.mkdir()
    _write_session(session_dir)
    summary = "\nalpha\n\n"
    detail = "   \ntext"
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": summary, "detail": detail},
        event_indices=[1],
        note_id="n-blank",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    lines = document.text.splitlines()
    summary_tag = next(i for i, line in enumerate(lines) if "field-id=summary" in line)
    cursor_row = summary_tag + 1  # 1-based row of the field tag itself
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "blank_out.json"
    harness = tmp_path / "blank.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local note = groket._note_at_row(buf, {cursor_row})
            vim.fn.writefile({{ vim.json.encode(note.fields) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    fields = json.loads(out_json.read_text(encoding="utf-8"))
    assert fields["summary"] == summary
    assert fields["detail"] == detail


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_untagged_field_heading_does_not_truncate_body(tmp_path: Path) -> None:
    """A user-typed ``##### `` at column 0 carries no tag, so the body continues."""
    session_dir = tmp_path / "session-nvim-untagged"
    session_dir.mkdir()
    _write_session(session_dir)
    detail = "keep before\n##### fake\nkeep after"
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "s", "detail": detail},
        event_indices=[1],
        note_id="n-untagged",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    # Simulate the operator typing the heading at column 0 inside the field body.
    text = document.text.replace("\n    ##### fake\n", "\n##### fake\n")
    assert "\n##### fake\n" in text
    md_path = tmp_path / "session.md"
    md_path.write_text(text, encoding="utf-8")
    lines = text.splitlines()
    detail_tag = next(i for i, line in enumerate(lines) if "field-id=detail" in line)
    cursor_row = detail_tag + 3
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "untagged_out.json"
    harness = tmp_path / "untagged.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local note = groket._note_at_row(buf, {cursor_row})
            vim.fn.writefile({{ vim.json.encode(note.fields) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    fields = json.loads(out_json.read_text(encoding="utf-8"))
    assert fields["detail"] == detail


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_prompt_index_zero_is_valid(tmp_path: Path) -> None:
    """Lua must not treat prompt/turn index 0 as missing."""
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    md = textwrap.dedent(
        """\
        ## Prompt 0
        <!-- groket:prompt-index=0 turn-index=0 -->

        ### User

            hi

        ### Operator notes
        """
    )
    md_path = tmp_path / "p0.md"
    md_path.write_text(md, encoding="utf-8")
    out_json = tmp_path / "p0_out.json"
    harness = tmp_path / "p0.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            -- exercise meta_near_row via public note helpers by loading module internals
            local groket = require("groket")
            local lines = vim.fn.readfile({str(md_path)!r})
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            -- heading row 1 should resolve prompt-index 0 (not nil)
            local parse = groket._parse_groket_comment
            local meta = parse(lines[2])
            vim.fn.writefile({{
              vim.json.encode({{
                has_prompt = meta ~= nil and meta["prompt-index"] == "0",
                turn = meta and meta["turn-index"],
              }})
            }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["has_prompt"] is True
    assert payload["turn"] == "0"


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_parse_groket_comment_requires_column_zero(tmp_path: Path) -> None:
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    harness = tmp_path / "anchor.lua"
    out_json = tmp_path / "anchor_out.json"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local parse = groket._parse_groket_comment
            local payload = {{
              col0 = parse("<!-- groket:note-id=n1 -->") ~= nil,
              indented = parse("    <!-- groket:note-id=n1 -->") ~= nil,
              mid = parse("x <!-- groket:note-id=n1 -->") ~= nil,
            }}
            vim.fn.writefile({{ vim.json.encode(payload) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["col0"] is True
    assert payload["indented"] is False
    assert payload["mid"] is False


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_apply_document_snapshots_rendered_note_ids(tmp_path: Path) -> None:
    """Fence-aware apply_document keeps only projection note ids (not transcript forgeries)."""
    session_dir = tmp_path / "session-nvim-rendered"
    session_dir.mkdir()
    forged = "<!-- groket:note-id=n-forged -->\n#### forged"
    _write_session(session_dir, user_text=forged)
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "real", "detail": "body"},
        event_indices=[1],
        note_id="n-real",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))
    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    assert "<!-- groket:note-id=n-forged -->" in document.text
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    out_json = tmp_path / "rendered_out.json"
    harness = tmp_path / "rendered.lua"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local text = table.concat(vim.fn.readfile({str(md_path)!r}), "\\n")
            local buf = vim.api.nvim_create_buf(false, true)
            groket._apply_document(buf, text, "sid", "rev-1", "sid")
            local allowed = vim.b[buf].groket_rendered_note_ids or {{}}
            local ids = {{}}
            for id, ok in pairs(allowed) do
              if ok then table.insert(ids, id) end
            end
            table.sort(ids)
            -- Typed machine tag after apply is not in the snapshot.
            local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
            table.insert(lines, "<!-- groket:note-id=n-typed -->")
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            vim.fn.writefile({{
              vim.json.encode({{
                ids = ids,
                typed_allowed = allowed["n-typed"] == true,
                forged_allowed = allowed["n-forged"] == true,
                real_allowed = allowed["n-real"] == true,
                still_clean = not vim.bo[buf].modified,
              }})
            }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["ids"] == ["n-real"]
    assert payload["real_allowed"] is True
    assert payload["forged_allowed"] is False
    assert payload["typed_allowed"] is False
