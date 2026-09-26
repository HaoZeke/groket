"""Modal for creating, editing, and deleting turn-linked operator notes."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, SelectionList, Static, TextArea

from ...notes import (
    FieldSpec,
    NoteEntry,
    NotesSchema,
    decode_many_choices,
    encode_many_choices,
)
from .. import text as U
from ..bindings import FORM_SAVE
from ..forms import select_is_blank, select_null, selection_list_selected_ids
from ..i18n import join_ui, t
from ..quit_actions import QuitActions

_PREVIEW_MAX = 60
_DEFAULT_FIELD_FTL = {
    "summary": "notes-field-summary",
    "detail": "notes-field-detail",
}


def note_field_label(spec: FieldSpec) -> str:
    """Operator label, or Fluent for package-default field ids when label is empty."""
    lab = (spec.label or "").strip()
    if lab:
        return lab
    mid = _DEFAULT_FIELD_FTL.get(spec.id)
    if mid is not None:
        return t(mid)
    return spec.id


def _note_preview_label(note: NoteEntry) -> str:
    """Turn label plus first non-empty field value (truncated)."""
    turn = t("turn-filter-n", n=note.turn_index)
    preview = next((v.strip() for v in note.fields.values() if (v or "").strip()), "")
    if not preview:
        return turn
    if len(preview) > _PREVIEW_MAX:
        preview = preview[: _PREVIEW_MAX - 1] + "…"
    return join_ui(turn, "—", preview)


def _field_widget_id(field_id: str) -> str:
    return f"note-field-{field_id}"


class NotesModal(QuitActions, ModalScreen):
    """Modal dialog for one operator note (create or edit; schema-driven fields)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(
        self,
        *,
        schema: NotesSchema,
        turn_options: list[tuple[str, str]],
        default_turn: int = 0,
        event_indices: list[int] | None = None,
        existing: NoteEntry | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.schema = schema
        self.turn_options = turn_options or [(t("turn-filter-n", n=0), "0")]
        self.default_turn = int(existing.turn_index if existing is not None else default_turn)
        self.event_indices = list(
            existing.event_indices if existing is not None else (event_indices or [])
        )
        self.existing = existing

    def compose(self) -> ComposeResult:
        title = U.edit_note_title() if self.existing is not None else U.new_note_title()
        turn_val = str(self.default_turn)
        turn_choices = {v for _, v in self.turn_options}
        if turn_val not in turn_choices:
            self.turn_options = list(self.turn_options) + [
                (t("turn-filter-n", n=self.default_turn), turn_val)
            ]
            turn_choices.add(turn_val)
        turn_value = turn_val if turn_val in turn_choices else self.turn_options[0][1]
        with Vertical(id="modal-container"):
            with VerticalScroll(id="notes-modal-body"):
                yield Static(f"[bold]{rich_escape(title)}[/bold]")
                yield Label(U.turn_label())
                yield Select(
                    self.turn_options,
                    value=turn_value,
                    id="note-turn-select",
                    classes="field-select",
                    allow_blank=False,
                )
                if self.event_indices:
                    yield Static(
                        f"[dim]{rich_escape(U.notes_events_hint(', '.join(str(i) for i in self.event_indices)))}[/dim]"
                    )
                yield Static("")
                for spec in self.schema.fields:
                    yield from self._field_widgets(spec)
            with Horizontal(id="note-buttons", classes="modal-footer"):
                yield Button(U.save(), variant="primary", id="save-note")
                if self.existing is not None:
                    yield Button(U.delete(), variant="error", id="delete-note")
                yield Button(U.cancel(), id="cancel-note")

    def _field_widgets(self, spec: FieldSpec) -> ComposeResult:
        label = note_field_label(spec)
        yield Label(label if label.endswith(":") else f"{label}:")
        initial = self.existing.fields.get(spec.id, "") if self.existing else ""
        wid = _field_widget_id(spec.id)
        if not spec.constrained:
            yield TextArea(initial, id=wid)
            return
        if spec.pick_many:
            selected = set(decode_many_choices(initial))
            items: list[tuple[str, str, bool]] = [
                (choice, choice, choice in selected) for choice in spec.choices
            ]
            known = set(spec.choices)
            for extra in decode_many_choices(initial):
                if extra not in known:
                    items.append((extra, extra, True))
            yield SelectionList[str](*items, id=wid, classes="note-field-choices")
            return
        # one-of — empty initial must use Select.NULL (Textual 8+). Select.BLANK
        # is the bool False and raises InvalidSelectValueError.
        options: list[tuple[str, str]] = [(c, c) for c in spec.choices]
        initial_s = (initial or "").strip()
        if initial_s and initial_s not in {c for c in spec.choices}:
            options = [(initial_s, initial_s), *options]
        if initial_s and any(v == initial_s for _, v in options):
            value: object = initial_s
        else:
            value = select_null()
        yield Select(
            options,
            value=value,
            id=wid,
            classes="field-select",
            allow_blank=True,
        )

    def _read_field_value(self, spec: FieldSpec) -> str:
        """Read one schema field from the mounted widget."""
        wid = f"#{_field_widget_id(spec.id)}"
        if not spec.constrained:
            return self.query_one(wid, TextArea).text.strip()
        if spec.pick_many:
            selected = selection_list_selected_ids(self.query_one(wid, SelectionList))
            return encode_many_choices(selected, spec.choices)
        raw = self.query_one(wid, Select).value
        if select_is_blank(raw):
            return ""
        return str(raw).strip()

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._commit_save()

    def _commit_save(self) -> None:
        turn_sel = self.query_one("#note-turn-select", Select)
        raw_turn = turn_sel.value
        if select_is_blank(raw_turn):
            self.notify(U.note_turn_invalid(), severity="error")
            return
        try:
            turn_index = int(str(raw_turn))
        except (TypeError, ValueError):
            self.notify(U.note_turn_invalid(), severity="error")
            return
        form_fields = {spec.id: self._read_field_value(spec) for spec in self.schema.fields}
        if self.existing is not None:
            fields = dict(self.existing.fields)
            fields.update(form_fields)
            entry = NoteEntry(
                id=self.existing.id,
                turn_index=turn_index,
                fields=fields,
                event_indices=list(self.existing.event_indices),
                created_at=self.existing.created_at,
                updated_at=datetime.now(UTC).isoformat(),
            )
        else:
            entry = NoteEntry.new(
                turn_index=turn_index,
                fields=form_fields,
                event_indices=list(self.event_indices),
            )
        self.dismiss(("save", entry))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-note":
            self._commit_save()
        elif event.button.id == "delete-note" and self.existing is not None:
            self.dismiss(("delete", self.existing.id))
        elif event.button.id == "cancel-note":
            self.dismiss(None)


class NotesPickModal(QuitActions, ModalScreen):
    """Minimal picker when several operator notes exist."""

    def __init__(self, notes: list[NoteEntry], **kwargs) -> None:
        super().__init__(**kwargs)
        self.notes = list(notes)
        self._by_id = {n.id: n for n in self.notes}

    def compose(self) -> ComposeResult:
        options = [(_note_preview_label(n), n.id) for n in self.notes]
        default: object = options[0][1] if options else select_null()
        with Vertical(id="modal-container"):
            yield Static(f"[bold]{rich_escape(U.pick_note_title())}[/bold]")
            yield Select(
                options,
                value=default,
                id="pick-note-select",
                classes="field-select",
                allow_blank=not bool(options),
            )
            with Horizontal(id="pick-note-buttons", classes="modal-footer"):
                yield Button(U.done(), variant="primary", id="pick-note-ok")
                yield Button(U.cancel(), id="pick-note-cancel")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def _commit_pick(self) -> None:
        sel = self.query_one("#pick-note-select", Select)
        raw = sel.value
        if select_is_blank(raw):
            self.dismiss(None)
            return
        note = self._by_id.get(str(raw))
        self.dismiss(note)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pick-note-ok":
            self._commit_pick()
        elif event.button.id == "pick-note-cancel":
            self.dismiss(None)
