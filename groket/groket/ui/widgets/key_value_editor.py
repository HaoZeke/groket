"""Key/value editor for env vars (and similar maps) — one Input pair per row."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from ..i18n import t


class KeyValueEditor(Vertical):
    """Editable list of KEY / value rows (no ``KEY=value`` free text)."""

    DEFAULT_CSS = """
    KeyValueEditor {
        height: auto;
        min-height: 8;
    }
    KeyValueEditor #kv-rows {
        height: auto;
        max-height: 16;
    }
    KeyValueEditor .kv-row {
        height: 3;
        width: 100%;
    }
    KeyValueEditor .kv-key {
        width: 1fr;
        margin-right: 1;
    }
    KeyValueEditor .kv-val {
        width: 2fr;
        margin-right: 1;
    }
    KeyValueEditor .kv-del {
        width: 5;
        min-width: 5;
    }
    KeyValueEditor #kv-add-row {
        height: 3;
        margin-top: 1;
    }
    """

    def __init__(self, initial: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._initial = {str(k): str(v) for k, v in (initial or {}).items() if str(k).strip()}

    def compose(self) -> ComposeResult:
        yield Static(t("kv-editor-hint"), classes="pe-field-hint")
        yield Vertical(id="kv-rows")
        with Horizontal(id="kv-add-row"):
            yield Button(t("kv-editor-add"), id="kv-add", variant="default")

    def on_mount(self) -> None:
        self.set_values(self._initial)

    def set_values(self, env: dict[str, str]) -> None:
        rows = self.query_one("#kv-rows", Vertical)
        rows.remove_children()
        items = sorted((env or {}).items(), key=lambda kv: kv[0].lower())
        if not items:
            items = [("", "")]
        for key, val in items:
            self._mount_row(rows, key, val)

    def _mount_row(self, rows: Vertical, key: str = "", val: str = "") -> None:
        row = Horizontal(classes="kv-row")
        rows.mount(row)
        row.mount(
            Input(value=key, placeholder=t("kv-editor-key-placeholder"), classes="kv-key"),
            Input(value=val, placeholder=t("kv-editor-value-placeholder"), classes="kv-val"),
            Button("×", classes="kv-del", variant="error"),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        classes = set(event.button.classes)
        if bid == "kv-add" or "kv-add" in classes:
            self._mount_row(self.query_one("#kv-rows", Vertical))
            event.stop()
            return
        if "kv-del" in classes:
            row = event.button.parent
            rows = self.query_one("#kv-rows", Vertical)
            # remove() is async in Textual — count before scheduling removal
            n_rows = len(list(rows.query(".kv-row")))
            if isinstance(row, Horizontal):
                row.remove()
            if n_rows <= 1:
                self._mount_row(rows)
            event.stop()

    def get_values(self) -> dict[str, str]:
        """Return non-empty keys (last row wins on duplicate keys)."""
        out: dict[str, str] = {}
        for row in self.query(".kv-row"):
            inputs = list(row.query(Input))
            key = (inputs[0].value or "").strip()
            if not key:
                continue
            out[key] = inputs[1].value or ""
        return out
