"""Operator notes load/save and export collect."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import groket.notes as notes_mod
import pytest
from groket.notes import (
    NOTES_FILENAME,
    NoteEntry,
    NotesDoc,
    collect_notes_for_export,
    load_notes,
    notes_mtime,
    parse_toml,
    save_notes,
)


def test_load_empty(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = load_notes(sd)
    assert doc.notes == []
    assert doc.session_id == "sess"
    assert notes_mtime(sd) == 0.0


def test_empty_notes_snapshot_has_stable_content_revision(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()

    snapshot = notes_mod.notes_snapshot(sd)

    assert snapshot.doc.session_id == "sess"
    assert snapshot.doc.notes == []
    assert snapshot.revision == sha256(b"").hexdigest()


def test_upsert_note_changes_revision_and_persists_entry(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    before = notes_mod.notes_snapshot(sd)
    entry = NoteEntry.new(
        turn_index=3,
        fields={"summary": "inspect this turn"},
        note_id="n-editor",
    )

    after = notes_mod.upsert_note(sd, entry, expected_revision=before.revision)

    assert after.revision != before.revision
    assert after.doc.notes == [entry]
    assert notes_mod.notes_snapshot(sd).revision == after.revision
    assert load_notes(sd).notes[0].fields["summary"] == "inspect this turn"


def test_stale_upsert_rejects_without_overwriting_notes(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    empty = notes_mod.notes_snapshot(sd)
    first = notes_mod.upsert_note(
        sd,
        NoteEntry.new(turn_index=1, fields={"summary": "first"}, note_id="n-one"),
        expected_revision=empty.revision,
    )

    with pytest.raises(notes_mod.NotesConflict) as caught:
        notes_mod.upsert_note(
            sd,
            NoteEntry.new(turn_index=1, fields={"summary": "stale"}, note_id="n-one"),
            expected_revision=empty.revision,
        )

    assert caught.value.current_revision == first.revision
    stored = notes_mod.notes_snapshot(sd)
    assert stored.revision == first.revision
    assert stored.doc.notes[0].fields["summary"] == "first"


def test_delete_note_requires_current_revision(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    empty = notes_mod.notes_snapshot(sd)
    saved = notes_mod.upsert_note(
        sd,
        NoteEntry.new(turn_index=2, fields={"summary": "remove"}, note_id="n-remove"),
        expected_revision=empty.revision,
    )

    deleted = notes_mod.delete_note(sd, "n-remove", expected_revision=saved.revision)

    assert deleted.doc.notes == []
    assert deleted.revision != saved.revision


def test_delete_missing_note_keeps_revision(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    before = notes_mod.notes_snapshot(sd)

    after = notes_mod.delete_note(sd, "n-missing", expected_revision=before.revision)

    assert after.revision == before.revision


def test_save_load_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess-a"
    sd.mkdir()
    doc = NotesDoc(schema_id="default", session_id="sess-a")
    entry = NoteEntry.new(
        turn_index=2,
        fields={"summary": "missed check", "detail": "should have run tests"},
        event_indices=[4, 7],
        note_id="n-test1",
    )
    doc.upsert(entry)
    path = save_notes(sd, doc)
    assert path.name == NOTES_FILENAME
    assert path.is_file()

    loaded = load_notes(sd)
    assert len(loaded.notes) == 1
    n = loaded.notes[0]
    assert n.id == "n-test1"
    assert n.turn_index == 2
    assert n.fields["summary"] == "missed check"
    assert n.event_indices == [4, 7]
    assert notes_mtime(sd) > 0


def test_multi_note_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "a"}, note_id="n-a"))
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "b"}, note_id="n-b"))
    save_notes(sd, doc)
    loaded = load_notes(sd)
    assert {n.id for n in loaded.notes} == {"n-a", "n-b"}
    assert loaded.notes[0].fields["summary"] in ("a", "b")


def test_quoted_field_keys_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = NotesDoc(session_id="sess")
    doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "x", "custom_field": "keep"},
            note_id="n-1",
        )
    )
    save_notes(sd, doc)
    text = (sd / NOTES_FILENAME).read_text(encoding="utf-8")
    assert '"custom_field"' in text or "custom_field" in text
    loaded = load_notes(sd)
    assert loaded.notes[0].fields["custom_field"] == "keep"


def test_unknown_fields_preserved(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / NOTES_FILENAME).write_text(
        """
schema_id = "custom"
session_id = "sess"

[[notes]]
id = "n-1"
turn_index = 0
event_indices = [1]

[notes.fields]
"summary" = "a"
"extra_custom" = "keep me"
""",
        encoding="utf-8",
    )
    doc = load_notes(sd)
    assert doc.notes[0].fields["extra_custom"] == "keep me"
    save_notes(sd, doc)
    raw = parse_toml((sd / NOTES_FILENAME).read_text(encoding="utf-8"))
    assert raw["notes"][0]["fields"]["extra_custom"] == "keep me"


def test_upsert_and_remove(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = load_notes(sd)
    a = NoteEntry.new(turn_index=0, fields={"summary": "one"}, note_id="n-a")
    doc.upsert(a)
    a2 = NoteEntry(
        id="n-a",
        turn_index=0,
        fields={"summary": "updated"},
        event_indices=[],
        created_at=a.created_at,
        updated_at="later",
    )
    doc.upsert(a2)
    assert len(doc.notes) == 1
    assert doc.notes[0].fields["summary"] == "updated"
    assert doc.remove("n-a") is True
    assert doc.notes == []


def test_remove_and_save_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "keep"}, note_id="n-keep"))
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "drop"}, note_id="n-drop"))
    save_notes(sd, doc)
    loaded = load_notes(sd)
    assert loaded.remove("n-drop") is True
    save_notes(sd, loaded)
    again = load_notes(sd)
    assert [n.id for n in again.notes] == ["n-keep"]
    assert again.notes[0].fields["summary"] == "keep"


def test_fallback_on_permission_error(tmp_path: Path, monkeypatch) -> None:
    sd = tmp_path / "readonly-sess"
    sd.mkdir()
    doc = NotesDoc(session_id=sd.name)
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "x"}, note_id="n-1"))

    import groket.notes as notes_mod
    import groket.paths as paths_mod

    monkeypatch.setattr(paths_mod, "APP_HOME", tmp_path / "fakehome" / ".groket")
    monkeypatch.setattr(notes_mod, "app_home", lambda: tmp_path / "fakehome" / ".groket")

    def fail_primary(path: Path, text: str) -> None:
        if path.parent == sd:
            raise PermissionError("read-only")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(notes_mod, "_atomic_write", fail_primary)
    path = save_notes(sd, doc)
    assert "notes" in path.parts
    assert path.is_file()
    loaded = load_notes(sd)
    assert len(loaded.notes) == 1


def test_host_session_skips_primary_write(tmp_path: Path, monkeypatch) -> None:
    """Host Grok sessions must not get operator_notes in ~/.grok/sessions."""
    import groket.notes as notes_mod
    import groket.paths as paths_mod
    import groket.session.sources as sources_mod

    home = tmp_path / "home" / ".groket"
    monkeypatch.setattr(paths_mod, "APP_HOME", home)
    monkeypatch.setattr(notes_mod, "app_home", lambda: home)

    host_root = tmp_path / "host-sessions"
    host = host_root / "%2Fproj" / "sess-id"
    host.mkdir(parents=True)
    (host / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sources_mod, "host_grok_sessions_root", lambda: host_root)

    doc = NotesDoc(session_id=host.name)
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "host"}, note_id="n-host"))
    path = save_notes(host, doc)
    assert path == home / "notes" / "sess-id" / NOTES_FILENAME
    assert path.is_file()
    assert not (host / NOTES_FILENAME).exists()
    loaded = load_notes(host)
    assert loaded.notes[0].fields["summary"] == "host"


def test_symlinked_session_skips_primary_write(tmp_path: Path, monkeypatch) -> None:
    """Symlinked session dirs also write notes only under ~/.groket/notes."""
    import groket.notes as notes_mod
    import groket.paths as paths_mod

    home = tmp_path / "home" / ".groket"
    monkeypatch.setattr(paths_mod, "APP_HOME", home)
    monkeypatch.setattr(notes_mod, "app_home", lambda: home)

    real = tmp_path / "real-sess"
    real.mkdir()
    link = tmp_path / "link-sess"
    link.symlink_to(real)

    doc = NotesDoc(session_id=link.name)
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "linked"}, note_id="n-link"))
    path = save_notes(link, doc)
    assert path == home / "notes" / "link-sess" / NOTES_FILENAME
    assert not (real / NOTES_FILENAME).exists()


def test_prefer_newer_fallback(tmp_path: Path, monkeypatch) -> None:
    import time

    import groket.notes as notes_mod
    import groket.paths as paths_mod

    home = tmp_path / "home"
    monkeypatch.setattr(paths_mod, "APP_HOME", home)
    monkeypatch.setattr(notes_mod, "app_home", lambda: home)

    sd = tmp_path / "sess"
    sd.mkdir()
    primary = sd / NOTES_FILENAME
    primary.write_text(
        'schema_id = "default"\nsession_id = "sess"\n\n[[notes]]\nid = "n-old"\n'
        'turn_index = 0\n\n[notes.fields]\n"summary" = "old"\n',
        encoding="utf-8",
    )
    fb = home / "notes" / "sess"
    fb.mkdir(parents=True)
    fb_file = fb / NOTES_FILENAME
    time.sleep(0.02)
    fb_file.write_text(
        'schema_id = "default"\nsession_id = "sess"\n\n[[notes]]\nid = "n-new"\n'
        'turn_index = 0\n\n[notes.fields]\n"summary" = "new"\n',
        encoding="utf-8",
    )
    loaded = load_notes(sd)
    assert loaded.notes[0].id == "n-new"
    assert loaded.notes[0].fields["summary"] == "new"


def test_collect_notes_for_export_copies_file(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    staging = tmp_path / "staging" / "notes"
    assert collect_notes_for_export(sd, staging) == []

    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "hi"}, note_id="n-1"))
    save_notes(sd, doc)
    written = collect_notes_for_export(sd, staging)
    assert written == [f"notes/{NOTES_FILENAME}"]
    assert (staging / NOTES_FILENAME).is_file()
    assert "hi" in (staging / NOTES_FILENAME).read_text(encoding="utf-8")
    assert not (staging / "schema.toml").exists()


def test_multiline_field_roundtrip_byte_identical(tmp_path: Path) -> None:
    """Multiline values must not gain a trailing newline on dump/load."""
    sd = tmp_path / "sess"
    sd.mkdir()
    no_trailing = "line1\nline2"
    with_trailing = "line1\nline2\n"
    with_escapes = 'said "hello" and path C:\\tmp\\n\nsecond'
    doc = NotesDoc(session_id="sess")
    doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={
                "summary": no_trailing,
                "detail": with_trailing,
                "extra": with_escapes,
            },
            note_id="n-ml",
        )
    )
    save_notes(sd, doc)
    loaded = load_notes(sd)
    fields = loaded.notes[0].fields
    assert fields["summary"] == no_trailing
    assert fields["detail"] == with_trailing
    assert fields["extra"] == with_escapes
    # Double dump/load must stay stable (no progressive trailing NL growth).
    save_notes(sd, loaded)
    again = load_notes(sd).notes[0].fields
    assert again["summary"] == no_trailing
    assert again["detail"] == with_trailing
    assert again["extra"] == with_escapes


def test_multiline_embedded_triple_quotes_roundtrip(tmp_path: Path) -> None:
    """Embedded \"\"\" in multiline fields must roundtrip through TOML dump/load."""
    sd = tmp_path / "sess"
    sd.mkdir()
    value = 'line1\nsaid """hello"""\nline3'
    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": value}, note_id="n-qq"))
    save_notes(sd, doc)
    loaded = load_notes(sd)
    assert loaded.notes[0].fields["summary"] == value


def test_control_characters_in_field_values_round_trip(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    gnarly = "bell\x07 and\x01\ncarriage\rreturn\x7f"
    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": gnarly}, note_id="n-ctl"))

    save_notes(sd, doc)

    loaded = load_notes(sd)
    assert loaded.notes[0].fields["summary"] == gnarly


def test_lone_carriage_return_round_trips(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "a\rb"}, note_id="n-cr"))

    save_notes(sd, doc)

    assert load_notes(sd).notes[0].fields["summary"] == "a\rb"
