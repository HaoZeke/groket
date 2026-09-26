"""Turn-linked operator notes (session TOML + optional schema).

Schema: ``~/.groket/notes_schema.toml`` (default fields: summary, detail).
Optional per-field ``choices`` with ``pick`` = ``one-of`` | ``many`` for
constrained values (dropdown / multi-select). Session file:
``<session_dir>/operator_notes.toml``, fallback under
``~/.groket/notes/<session_id>/``. Host Grok sessions under ``~/.grok/sessions``
(and any symlinked session dir) always write the fallback so the live host
tree stays clean.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4

from .models import JsonObject, as_json_object, json_as_object
from .paths import app_home

logger = logging.getLogger(__name__)

NOTES_FILENAME = "operator_notes.toml"
SCHEMA_FILENAME = "notes_schema.toml"
_FIELD_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

# Constrained field cardinality (only when ``choices`` is non-empty).
PICK_ONE_OF = "one-of"
PICK_MANY = "many"
_VALID_PICKS = frozenset({PICK_ONE_OF, PICK_MANY})


@dataclass(frozen=True)
class FieldSpec:
    """Schema field. Empty *label* means the TUI uses Fluent ``notes-field-{id}``.

    Free text when *choices* is empty (backward compatible). With *choices*:
    ``pick`` is ``one-of`` (single select) or ``many`` (multi-select); default
    ``one-of`` when omitted.
    """

    id: str
    label: str = ""
    choices: tuple[str, ...] = ()
    pick: str = PICK_ONE_OF

    @property
    def constrained(self) -> bool:
        """True when the field has a non-empty allowed-value list."""
        return bool(self.choices)

    @property
    def pick_many(self) -> bool:
        """True when the field is multi-select among *choices*."""
        return bool(self.choices) and self.pick == PICK_MANY


@dataclass
class NotesSchema:
    """Field layout for operator notes."""

    schema_id: str = "default"
    fields: list[FieldSpec] = field(default_factory=list)


def normalize_pick(value: object) -> str:
    """Return ``one-of`` or ``many``; unknown / empty → ``one-of``."""
    raw = str(value or "").strip().lower()
    if raw in _VALID_PICKS:
        return raw
    return PICK_ONE_OF


def parse_choices(raw: object) -> tuple[str, ...]:
    """Unique non-empty choice strings, order preserved."""
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip() if item is not None else ""
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return tuple(out)


def decode_many_choices(stored: str) -> list[str]:
    """Split a stored multi-select value into tokens (newline-separated)."""
    if not (stored or "").strip():
        return []
    text = stored.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        tok = line.strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def encode_many_choices(selected: Sequence[str], choices: Sequence[str]) -> str:
    """Join selected tokens for storage: schema order first, then extras.

    :param selected: Operator-selected values (any order).
    :param choices: Schema allowed list (defines preferred order).
    :returns: Newline-joined string (empty when nothing selected).
    """
    sel: list[str] = []
    seen: set[str] = set()
    for item in selected:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            sel.append(s)
    if not sel:
        return ""
    allowed = list(choices)
    allowed_set = set(allowed)
    ordered = [c for c in allowed if c in seen]
    ordered.extend(s for s in sel if s not in allowed_set)
    return "\n".join(ordered)


@dataclass
class NoteEntry:
    """One operator note, usually linked to a turn."""

    id: str
    turn_index: int
    fields: dict[str, str] = field(default_factory=dict)
    event_indices: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(
        cls,
        *,
        turn_index: int,
        fields: dict[str, str] | None = None,
        event_indices: list[int] | None = None,
        note_id: str | None = None,
    ) -> NoteEntry:
        now = datetime.now(UTC).isoformat()
        return cls(
            id=note_id or f"n-{uuid4().hex[:12]}",
            turn_index=int(turn_index),
            fields=dict(fields or {}),
            event_indices=list(event_indices or []),
            created_at=now,
            updated_at=now,
        )


@dataclass
class NotesDoc:
    """All operator notes for one session."""

    schema_id: str = "default"
    session_id: str = ""
    notes: list[NoteEntry] = field(default_factory=list)

    def upsert(self, entry: NoteEntry) -> None:
        for i, n in enumerate(self.notes):
            if n.id == entry.id:
                self.notes[i] = entry
                return
        self.notes.append(entry)

    def remove(self, note_id: str) -> bool:
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.id != note_id]
        return len(self.notes) < before

    def sorted_notes(self) -> list[NoteEntry]:
        return sorted(self.notes, key=lambda n: (n.turn_index, n.created_at, n.id))


@dataclass(frozen=True)
class NotesSnapshot:
    """Canonical notes document and its content revision."""

    doc: NotesDoc
    revision: str


class NotesConflict(RuntimeError):
    """The canonical notes changed after an editor rendered its snapshot."""

    def __init__(self, current_revision: str) -> None:
        super().__init__("operator notes changed")
        self.current_revision = current_revision


_notes_locks_guard = Lock()
_notes_locks: dict[str, RLock] = {}


_DEFAULT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(id="summary"),
    FieldSpec(id="detail"),
)


def default_schema() -> NotesSchema:
    """Built-in schema (summary + detail; labels via Fluent in the TUI)."""
    return NotesSchema(schema_id="default", fields=list(_DEFAULT_FIELDS))


def notes_schema_path() -> Path:
    """``~/.groket/notes_schema.toml``."""
    return app_home() / SCHEMA_FILENAME


def load_schema(*, path: Path | None = None) -> NotesSchema:
    """Load schema from *path* or config home; defaults when missing/invalid."""
    fp = Path(path) if path is not None else notes_schema_path()
    if not fp.is_file():
        return default_schema()
    try:
        raw = parse_toml(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read notes schema %s: %s", fp, exc)
        return default_schema()
    return schema_from_mapping(raw)


def schema_from_mapping(data: JsonObject) -> NotesSchema:
    """Build :class:`NotesSchema` from a TOML/JSON mapping.

    Backward compatible: fields with only ``id`` / ``label`` stay free text.
    Optional ``choices`` (string array) plus ``pick`` (``one-of`` | ``many``,
    default ``one-of``) enable constrained fields.
    """
    schema_id = str(data.get("schema_id") or "default").strip() or "default"
    fields: list[FieldSpec] = []
    seen: set[str] = set()
    raw_fields = data.get("fields")
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or "").strip()
            if not fid or not _FIELD_ID_RE.match(fid) or fid in seen:
                continue
            seen.add(fid)
            label = str(item.get("label") or "").strip()
            choices = parse_choices(item.get("choices"))
            pick = normalize_pick(item.get("pick")) if choices else PICK_ONE_OF
            fields.append(
                FieldSpec(
                    id=fid,
                    label=label or fid,
                    choices=choices,
                    pick=pick,
                )
            )
    if not fields:
        fields = list(_DEFAULT_FIELDS)
    return NotesSchema(schema_id=schema_id, fields=fields)


def parse_toml(text: str) -> JsonObject:
    """Parse TOML text into a JSON-shaped object."""
    data = tomllib.loads(text or "")
    if not isinstance(data, dict):
        return {}
    return as_json_object(data)


def dump_notes_toml(doc: NotesDoc) -> str:
    """Serialize a notes document to constrained TOML."""
    lines: list[str] = [
        f"schema_id = {_toml_str(doc.schema_id)}",
        f"session_id = {_toml_str(doc.session_id)}",
        "",
    ]
    for note in doc.notes:
        lines.append("[[notes]]")
        lines.append(f"id = {_toml_str(note.id)}")
        lines.append(f"turn_index = {int(note.turn_index)}")
        if note.created_at:
            lines.append(f"created_at = {_toml_str(note.created_at)}")
        if note.updated_at:
            lines.append(f"updated_at = {_toml_str(note.updated_at)}")
        if note.event_indices:
            inner = ", ".join(str(int(i)) for i in note.event_indices)
            lines.append(f"event_indices = [{inner}]")
        if note.fields:
            lines.append("")
            lines.append("[notes.fields]")
            for fk, fv in sorted(note.fields.items()):
                lines.append(f"{_toml_str(str(fk))} = {_toml_str(str(fv))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# TOML basic strings forbid raw control characters (tab and, in the multiline
# form, newline excepted); unescaped ones make the whole document unparseable
# and _try_load then silently reverts the session's notes to empty.
_TOML_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _toml_escape_control(s: str) -> str:
    return _TOML_CONTROL_RE.sub(lambda m: f"\\u{ord(m.group(0)):04X}", s)


def _toml_str(s: str) -> str:
    if "\n" in s:
        escaped = _toml_escape_control(s.replace("\\", "\\\\")).replace('"""', '\\"""')
        return f'"""\n{escaped}"""'
    return f'"{_toml_escape_control(s.replace("\\", "\\\\").replace('"', '\\"'))}"'


def _notes_paths(session_dir: Path) -> tuple[Path, Path]:
    """Primary session path and config-home fallback."""
    session_dir = Path(session_dir)
    primary = session_dir / NOTES_FILENAME
    fallback = app_home() / "notes" / session_dir.name / NOTES_FILENAME
    return primary, fallback


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else -1.0
    except OSError:
        return -1.0


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def notes_from_mapping(data: JsonObject) -> NotesDoc:
    """Build :class:`NotesDoc` from a TOML/JSON mapping."""
    schema_id = str(data.get("schema_id") or "default").strip() or "default"
    session_id = str(data.get("session_id") or "").strip()
    notes: list[NoteEntry] = []
    raw_notes = data.get("notes")
    if isinstance(raw_notes, list):
        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            entry = _note_from_mapping(json_as_object(item))
            if entry is not None:
                notes.append(entry)
    return NotesDoc(schema_id=schema_id, session_id=session_id, notes=notes)


def _note_from_mapping(item: JsonObject) -> NoteEntry | None:
    nid = str(item.get("id") or "").strip()
    turn_index = _coerce_int(item.get("turn_index"))
    if not nid or turn_index is None:
        return None
    fields_raw = item.get("fields")
    fields: dict[str, str] = {}
    if isinstance(fields_raw, dict):
        for k, v in fields_raw.items():
            if v is not None:
                fields[str(k)] = str(v)
    event_indices: list[int] = []
    ev_raw = item.get("event_indices")
    if isinstance(ev_raw, list):
        for x in ev_raw:
            n = _coerce_int(x)
            if n is not None:
                event_indices.append(n)
    return NoteEntry(
        id=nid,
        turn_index=turn_index,
        fields=fields,
        event_indices=event_indices,
        created_at=str(item.get("created_at") or ""),
        updated_at=str(item.get("updated_at") or ""),
    )


def _try_load(path: Path, session_id: str) -> NotesDoc | None:
    if not path.is_file():
        return None
    try:
        doc = notes_from_mapping(parse_toml(path.read_text(encoding="utf-8")))
        if not doc.session_id:
            doc.session_id = session_id
        return doc
    except (OSError, ValueError) as exc:
        logger.debug("Failed to load notes from %s: %s", path, exc)
        return None


def _load_notes_source(session_dir: Path) -> tuple[NotesDoc, Path | None]:
    """Return the canonical document and source selected by load precedence."""
    session_dir = Path(session_dir)
    sid = session_dir.name
    primary, fallback = _notes_paths(session_dir)
    primary_doc = _try_load(primary, sid)
    fallback_doc = _try_load(fallback, sid)
    if primary_doc is None:
        if fallback_doc is not None:
            return fallback_doc, fallback
        return NotesDoc(schema_id=load_schema().schema_id, session_id=sid), None
    if fallback_doc is None or _mtime(primary) >= _mtime(fallback):
        return primary_doc, primary
    return fallback_doc, fallback


def load_notes(session_dir: Path) -> NotesDoc:
    """Load notes for *session_dir*; prefer newer of primary vs fallback.

    :param session_dir: Grok session directory.
    :returns: Parsed :class:`NotesDoc` (may be empty).
    """
    return _load_notes_source(Path(session_dir))[0]


def _content_revision(path: Path | None) -> str:
    data = b""
    if path is not None:
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
    return sha256(data).hexdigest()


def notes_snapshot(session_dir: Path) -> NotesSnapshot:
    """Load canonical notes with a revision suitable for guarded mutation."""
    doc, source = _load_notes_source(Path(session_dir))
    return NotesSnapshot(doc=doc, revision=_content_revision(source))


def _notes_lock(session_dir: Path) -> RLock:
    key = str(Path(session_dir).expanduser().absolute())
    with _notes_locks_guard:
        lock = _notes_locks.get(key)
        if lock is None:
            lock = RLock()
            _notes_locks[key] = lock
        return lock


def upsert_note(
    session_dir: Path,
    entry: NoteEntry,
    *,
    expected_revision: str,
) -> NotesSnapshot:
    """Upsert one note when *expected_revision* is still canonical."""
    session_dir = Path(session_dir)
    with _notes_lock(session_dir):
        current = notes_snapshot(session_dir)
        if current.revision != expected_revision:
            raise NotesConflict(current.revision)
        current.doc.upsert(entry)
        save_notes(session_dir, current.doc)
        return notes_snapshot(session_dir)


def delete_note(
    session_dir: Path,
    note_id: str,
    *,
    expected_revision: str,
) -> NotesSnapshot:
    """Delete one note when *expected_revision* is still canonical."""
    session_dir = Path(session_dir)
    with _notes_lock(session_dir):
        current = notes_snapshot(session_dir)
        if current.revision != expected_revision:
            raise NotesConflict(current.revision)
        if not current.doc.remove(note_id):
            return current
        save_notes(session_dir, current.doc)
        return notes_snapshot(session_dir)


def notes_mtime(session_dir: Path) -> float:
    """Newest mtime of session or fallback notes files (0 if none).

    Used by analysis cache invalidation so operator-note edits refresh
    deferred automated reviews.

    :param session_dir: Grok session directory.
    :returns: Unix mtime, or ``0.0`` when no notes file exists.
    """
    primary, fallback = _notes_paths(Path(session_dir))
    return max(_mtime(primary), _mtime(fallback), 0.0)


def save_notes(session_dir: Path, doc: NotesDoc) -> Path:
    """Write *doc* beside the session; fall back under ``~/.groket/notes``.

    Host Grok sessions and symlinked session dirs skip the primary path so
    ``~/.grok/sessions`` is not modified.

    :raises OSError: When both primary and fallback writes fail.
    """
    from .session.sources import is_under_host_grok_sessions

    session_dir = Path(session_dir)
    if not doc.session_id:
        doc.session_id = session_dir.name
    text = dump_notes_toml(doc)
    primary, fallback = _notes_paths(session_dir)
    try:
        skip_primary = session_dir.is_symlink() or is_under_host_grok_sessions(session_dir)
    except OSError:
        skip_primary = False
    if not skip_primary:
        try:
            _atomic_write(primary, text)
            return primary
        except OSError as exc:
            logger.debug("Primary notes path not writable (%s): %s", primary, exc)
    _atomic_write(fallback, text)
    return fallback


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def collect_notes_for_export(session_dir: Path, staging_notes_dir: Path) -> list[str]:
    """Write loaded notes into *staging_notes_dir* for the export tarball.

    :returns: Relative member paths under ``notes/`` (may be empty).
    """
    doc = load_notes(session_dir)
    if not doc.notes:
        return []
    staging = Path(staging_notes_dir)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / NOTES_FILENAME).write_text(dump_notes_toml(doc), encoding="utf-8")
    return [f"notes/{NOTES_FILENAME}"]
