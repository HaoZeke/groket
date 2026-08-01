"""Org document projection for editor clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import event_types as et
from ..notes import FieldSpec, NoteEntry, NotesSchema, load_schema, notes_snapshot
from ..parser import load_session_meta, parse_timeline
from ..session.turns import TurnSegment, segment_timeline_turns


@dataclass(frozen=True)
class EditorDocument:
    """Rendered editor document and identities needed for synchronization."""

    session_id: str
    notes_revision: str
    prompt_indexes: tuple[int, ...]
    text: str


def _one_line(text: str) -> str:
    return " ".join((text or "").replace("\r", "").splitlines()).strip()


def _fixed_lines(text: str) -> list[str]:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [f": {line}" if line else ":" for line in lines]


def _prompt_index(segment: TurnSegment) -> int:
    return segment.prompt_index if segment.prompt_index is not None else segment.turn_index


def _field_order(note: NoteEntry, schema: NotesSchema) -> list[tuple[FieldSpec, str]]:
    specs = {spec.id: spec for spec in schema.fields}
    ordered: list[tuple[FieldSpec, str]] = []
    for spec in schema.fields:
        if spec.id in note.fields:
            ordered.append((spec, note.fields[spec.id]))
    for field_id in sorted(set(note.fields) - set(specs)):
        ordered.append((FieldSpec(id=field_id, label=field_id), note.fields[field_id]))
    return ordered


def _render_note(note: NoteEntry, schema: NotesSchema) -> list[str]:
    summary = _one_line(note.fields.get("summary", "")) or note.id
    lines = [f"*** {summary}", ":PROPERTIES:", f":GROKET_NOTE_ID: {note.id}"]
    if note.event_indices:
        joined = ",".join(str(index) for index in note.event_indices)
        lines.append(f":GROKET_EVENT_INDICES: {joined}")
    if note.created_at:
        lines.append(f":GROKET_CREATED_AT: {note.created_at}")
    if note.updated_at:
        lines.append(f":GROKET_UPDATED_AT: {note.updated_at}")
    lines.extend([":END:", ""])
    for spec, value in _field_order(note, schema):
        lines.extend(
            [
                f"**** {spec.label or spec.id}",
                ":PROPERTIES:",
                f":GROKET_FIELD_ID: {spec.id}",
                ":END:",
                value,
                "",
            ]
        )
    return lines


def _render_segment(segment: TurnSegment, notes: list[NoteEntry], schema: NotesSchema) -> list[str]:
    prompt_index = _prompt_index(segment)
    lines = [
        f"* Prompt {prompt_index}",
        ":PROPERTIES:",
        f":GROKET_PROMPT_INDEX: {prompt_index}",
        f":GROKET_TURN_INDEX: {segment.turn_index}",
        ":END:",
        "",
    ]
    for event in segment.events:
        if event.event_type in et.USER_TYPES:
            lines.extend(["** User", "", *_fixed_lines(event.content), ""])
        elif event.event_type in et.AGENT_TYPES:
            lines.extend(["** Assistant", "", *_fixed_lines(event.content), ""])
    lines.extend(["** Operator notes", ""])
    for note in notes:
        lines.extend(_render_note(note, schema))
    return lines


def render_editor_document(session_dir: Path) -> EditorDocument:
    """Render one session as an editable-note Org projection."""
    session_dir = Path(session_dir)
    meta = load_session_meta(session_dir)
    segments = segment_timeline_turns(parse_timeline(session_dir))
    snapshot = notes_snapshot(session_dir)
    schema = load_schema()
    title = _one_line(meta.title or meta.session_id or session_dir.name) or session_dir.name
    prompt_indexes = tuple(_prompt_index(segment) for segment in segments)
    lines = [
        f"#+TITLE: {title}",
        f"#+PROPERTY: GROKET_SESSION_ID {meta.session_id or session_dir.name}",
        f"#+PROPERTY: GROKET_NOTES_REVISION {snapshot.revision}",
        "",
        "* Session",
        ":PROPERTIES:",
        f":GROKET_SESSION_ID: {meta.session_id or session_dir.name}",
        ":END:",
        "",
        f"- Model: {meta.model_display}",
        f"- Outcome: {meta.turn_outcome or 'unknown'}",
        f"- Events: {meta.num_events or len(parse_timeline(session_dir))}",
        "",
    ]
    notes_by_turn: dict[int, list[NoteEntry]] = {}
    for note in snapshot.doc.sorted_notes():
        notes_by_turn.setdefault(note.turn_index, []).append(note)
    for segment in segments:
        lines.extend(_render_segment(segment, notes_by_turn.get(segment.turn_index, []), schema))
    text = "\n".join(lines).rstrip() + "\n"
    return EditorDocument(
        session_id=meta.session_id or session_dir.name,
        notes_revision=snapshot.revision,
        prompt_indexes=prompt_indexes,
        text=text,
    )
