"""Session document projections for editor clients (Org, Markdown, JSON)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .. import event_types as et
from ..models import JsonObject, JsonValue
from ..notes import FieldSpec, NoteEntry, NotesSchema, load_schema, notes_snapshot
from ..parser import load_session_meta, parse_timeline
from ..session.turns import TurnSegment, is_operator_user_event, segment_timeline_turns

_FENCE_RUN = re.compile(r"`+")
_ORG_ESCAPE_RE = re.compile(r"^,*(\*|#\+)")

EditorFormat = Literal["org", "markdown", "json"]

SUPPORTED_FORMATS: tuple[EditorFormat, ...] = ("org", "markdown", "json")
CONTENT_TYPES: dict[str, str] = {
    "org": "text/org",
    "markdown": "text/markdown",
    "json": "application/json",
}


@dataclass(frozen=True)
class EditorDocument:
    """Rendered editor document and identities needed for synchronization."""

    session_id: str
    notes_revision: str
    prompt_indexes: tuple[int, ...]
    text: str
    format: str = "org"
    content_type: str = "text/org"


def _one_line(text: str) -> str:
    return " ".join((text or "").replace("\r", "").splitlines()).strip()


def _split_lines(text: str) -> list[str]:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _org_fixed_lines(text: str) -> list[str]:
    return [f": {line}" if line else ":" for line in _split_lines(text)]


def _org_escape_src_line(line: str) -> str:
    """Comma-escape lines Org treats as structure inside a src block.

    Mirrors ``org-escape-code-in-region``: headlines (``*``), every ``#+``
    keyword (block delimiters included), and already-escaped lines, whose
    comma run grows by one so the original text stays recoverable. A bare
    ``* bullet`` in transcript Markdown would otherwise register as an Org
    headline and derail outline navigation (note saves then resolve the
    wrong turn).
    """
    if _ORG_ESCAPE_RE.match(line.lstrip()):
        return f",{line}"
    return line


def _org_transcript_lines(text: str, *, lang: str = "markdown") -> list[str]:
    """Wrap user/assistant bodies in an Org source block for Markdown fontification.

    Note field bodies stay on fixed-width lines (``_org_fixed_lines``) so Emacs
    can still edit/save them.
    """
    return [
        f"#+begin_src {lang}",
        *(_org_escape_src_line(line) for line in _split_lines(text or "")),
        "#+end_src",
    ]


def _md_fixed_lines(text: str) -> list[str]:
    """Indent note field bodies so they cannot form Markdown headings or tags."""
    out: list[str] = []
    for line in _split_lines(text):
        out.append(f"    {line}" if line else "")
    return out


def _md_fence_ticks(text: str) -> str:
    """Return a fence longer than any backtick run in *text* (at least 3)."""
    longest = 0
    for match in _FENCE_RUN.finditer(text or ""):
        longest = max(longest, len(match.group(0)))
    return "`" * max(3, longest + 1)


def _md_transcript_lines(text: str, *, lang: str = "markdown") -> list[str]:
    """Wrap user/assistant bodies in a fenced Markdown block.

    Language tag ``markdown`` lets Neovim treesitter inject nested MD (tables,
    code fences). Outer fence length exceeds any inner backtick run so nested
    ````` blocks stay intact. Note fields still use :func:`_md_fixed_lines`.
    """
    body = text or ""
    fence = _md_fence_ticks(body)
    open_fence = f"{fence}{lang}" if lang else fence
    return [open_fence, *_split_lines(body), fence]


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


def _md_comment(**attrs: str | int) -> str:
    parts = [f"{key}={value}" for key, value in attrs.items() if value is not None and value != ""]
    return f"<!-- groket:{' '.join(parts)} -->"


def _render_note_org(note: NoteEntry, schema: NotesSchema) -> list[str]:
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
        # Fixed lines so field bodies cannot form Org headlines (*** …).
        lines.extend(
            [
                f"**** {spec.label or spec.id}",
                ":PROPERTIES:",
                f":GROKET_FIELD_ID: {spec.id}",
                ":END:",
                *_org_fixed_lines(value),
                "",
            ]
        )
    return lines


def _render_note_md(note: NoteEntry, schema: NotesSchema) -> list[str]:
    summary = _one_line(note.fields.get("summary", "")) or note.id
    meta: dict[str, str | int] = {"note-id": note.id}
    if note.event_indices:
        meta["event-indices"] = ",".join(str(i) for i in note.event_indices)
    if note.created_at:
        meta["created"] = note.created_at
    if note.updated_at:
        meta["updated"] = note.updated_at
    lines = [f"#### {summary}", _md_comment(**meta), ""]
    for spec, value in _field_order(note, schema):
        # Indent only (not fenced): fields stay editable and strip cleanly on save.
        lines.extend(
            [
                f"##### {spec.label or spec.id}",
                _md_comment(**{"field-id": spec.id, "note-id": note.id}),
                "",
                *_md_fixed_lines(value),
                "",
            ]
        )
    return lines


def _render_segment_org(
    segment: TurnSegment, notes: list[NoteEntry], schema: NotesSchema
) -> list[str]:
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
        if is_operator_user_event(event):
            lines.extend(["** User", "", *_org_transcript_lines(event.content), ""])
        elif event.event_type in et.AGENT_TYPES:
            lines.extend(["** Assistant", "", *_org_transcript_lines(event.content), ""])
    lines.extend(["** Operator notes", ""])
    for note in notes:
        lines.extend(_render_note_org(note, schema))
    return lines


def _render_segment_md(
    segment: TurnSegment, notes: list[NoteEntry], schema: NotesSchema
) -> list[str]:
    prompt_index = _prompt_index(segment)
    lines = [
        f"## Prompt {prompt_index}",
        _md_comment(**{"prompt-index": prompt_index, "turn-index": segment.turn_index}),
        "",
    ]
    for event in segment.events:
        if is_operator_user_event(event):
            lines.extend(["### User", "", *_md_transcript_lines(event.content), ""])
        elif event.event_type in et.AGENT_TYPES:
            lines.extend(["### Assistant", "", *_md_transcript_lines(event.content), ""])
    lines.extend(["### Operator notes", ""])
    for note in notes:
        lines.extend(_render_note_md(note, schema))
    return lines


def _notes_by_turn(notes: list[NoteEntry]) -> dict[int, list[NoteEntry]]:
    by_turn: dict[int, list[NoteEntry]] = {}
    for note in notes:
        by_turn.setdefault(note.turn_index, []).append(note)
    return by_turn


def _load_session_bundle(
    session_dir: Path,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    tuple[int, ...],
    list[TurnSegment],
    dict[int, list[NoteEntry]],
    NotesSchema,
    int,
]:
    session_dir = Path(session_dir)
    meta = load_session_meta(session_dir)
    timeline = parse_timeline(session_dir)
    segments = segment_timeline_turns(timeline)
    snapshot = notes_snapshot(session_dir)
    schema = load_schema()
    session_id = meta.session_id or session_dir.name
    title = _one_line(meta.title or session_id) or session_dir.name
    prompt_indexes = tuple(_prompt_index(segment) for segment in segments)
    return (
        session_id,
        snapshot.revision,
        title,
        meta.model_display,
        meta.turn_outcome or "unknown",
        prompt_indexes,
        segments,
        _notes_by_turn(list(snapshot.doc.sorted_notes())),
        schema,
        meta.num_events or len(timeline),
    )


def _render_org(
    session_id: str,
    revision: str,
    title: str,
    model: str,
    outcome: str,
    event_count: int,
    segments: list[TurnSegment],
    notes_by_turn: dict[int, list[NoteEntry]],
    schema: NotesSchema,
) -> str:
    lines = [
        f"#+TITLE: {title}",
        f"#+PROPERTY: GROKET_SESSION_ID {session_id}",
        f"#+PROPERTY: GROKET_NOTES_REVISION {revision}",
        "",
        "* Session",
        ":PROPERTIES:",
        f":GROKET_SESSION_ID: {session_id}",
        ":END:",
        "",
        f"- Model: {model}",
        f"- Outcome: {outcome}",
        f"- Events: {event_count}",
        "",
    ]
    for segment in segments:
        lines.extend(
            _render_segment_org(segment, notes_by_turn.get(segment.turn_index, []), schema)
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_markdown(
    session_id: str,
    revision: str,
    title: str,
    model: str,
    outcome: str,
    event_count: int,
    segments: list[TurnSegment],
    notes_by_turn: dict[int, list[NoteEntry]],
    schema: NotesSchema,
) -> str:
    lines = [
        "---",
        f"groket_session_id: {session_id}",
        f"groket_notes_revision: {revision}",
        f"title: {_yaml_escape(title)}",
        "---",
        "",
        _md_comment(**{"session-id": session_id, "notes-revision": revision}),
        "",
        f"# {title}",
        "",
        f"- Model: {model}",
        f"- Outcome: {outcome}",
        f"- Events: {event_count}",
        "",
    ]
    for segment in segments:
        lines.extend(_render_segment_md(segment, notes_by_turn.get(segment.turn_index, []), schema))
    return "\n".join(lines).rstrip() + "\n"


# Quote unless the scalar is unambiguously plain: YAML indicators such as
# ``[``, ``*``, ``&``, ``!``, ``%``, ``|``, ``>``, ``@`` or a backtick at the
# start of a title otherwise change how the whole front matter parses.
_YAML_PLAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._/()-]*[A-Za-z0-9._/()-])?$")


def _yaml_escape(value: str) -> str:
    if _YAML_PLAIN_RE.match(value):
        return value
    return json.dumps(value)


def _note_json(note: NoteEntry) -> JsonObject:
    return {
        "id": note.id,
        "turnIndex": note.turn_index,
        "fields": dict(note.fields),
        "eventIndices": list(note.event_indices),
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
    }


def _render_json(
    session_id: str,
    revision: str,
    title: str,
    model: str,
    outcome: str,
    event_count: int,
    prompt_indexes: tuple[int, ...],
    segments: list[TurnSegment],
    notes_by_turn: dict[int, list[NoteEntry]],
) -> str:
    prompts: list[JsonValue] = []
    for segment in segments:
        messages: list[JsonValue] = []
        for event in segment.events:
            if event.event_type in et.USER_TYPES:
                messages.append({"role": "user", "text": event.content or ""})
            elif event.event_type in et.AGENT_TYPES:
                messages.append({"role": "assistant", "text": event.content or ""})
        notes_json: list[JsonValue] = [
            _note_json(note) for note in notes_by_turn.get(segment.turn_index, [])
        ]
        prompts.append(
            {
                "promptIndex": _prompt_index(segment),
                "turnIndex": segment.turn_index,
                "messages": messages,
                "notes": notes_json,
            }
        )
    payload: JsonObject = {
        "sessionId": session_id,
        "notesRevision": revision,
        "title": title,
        "model": model,
        "outcome": outcome,
        "eventCount": event_count,
        "promptIndexes": list(prompt_indexes),
        "prompts": prompts,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_editor_document(
    session_dir: Path,
    *,
    format: str = "org",
) -> EditorDocument:
    """Render one session for an editor client.

    :param session_dir: Session directory on disk.
    :param format: ``org`` (Emacs), ``markdown`` (Neovim), or ``json`` (tools).
    :returns: Projection text plus session/revision identities.
    :raises ValueError: When *format* is not supported.
    """
    fmt = (format or "org").strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        supported = ", ".join(SUPPORTED_FORMATS)
        msg = f"unsupported editor format {format!r} (supported: {supported})"
        raise ValueError(msg)

    (
        session_id,
        revision,
        title,
        model,
        outcome,
        prompt_indexes,
        segments,
        notes_by_turn,
        schema,
        event_count,
    ) = _load_session_bundle(session_dir)

    if fmt == "org":
        text = _render_org(
            session_id,
            revision,
            title,
            model,
            outcome,
            event_count,
            segments,
            notes_by_turn,
            schema,
        )
    elif fmt == "markdown":
        text = _render_markdown(
            session_id,
            revision,
            title,
            model,
            outcome,
            event_count,
            segments,
            notes_by_turn,
            schema,
        )
    else:
        text = _render_json(
            session_id,
            revision,
            title,
            model,
            outcome,
            event_count,
            prompt_indexes,
            segments,
            notes_by_turn,
        )

    return EditorDocument(
        session_id=session_id,
        notes_revision=revision,
        prompt_indexes=prompt_indexes,
        text=text,
        format=fmt,
        content_type=CONTENT_TYPES[fmt],
    )
