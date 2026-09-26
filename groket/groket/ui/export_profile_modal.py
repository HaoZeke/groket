"""Pick an export profile (command palette / export-with-profile)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static

from ..session.export_spec import (
    ExportSpec,
    default_export_profile_id,
    list_export_profiles,
)
from .forms import select_is_blank, select_null
from .i18n import t
from .quit_actions import QuitActions


def _profile_label(spec: ExportSpec) -> str:
    name = (spec.name or spec.profile_id).strip()
    bits = [spec.profile_id, spec.renderer, spec.packaging.value]
    return f"{name}  ·  {', '.join(bits)}"


class ExportProfileModal(QuitActions, ModalScreen[str | None]):
    """Choose an export profile id; dismiss ``None`` on cancel."""

    def __init__(self, *, profiles: dict[str, ExportSpec] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._profiles = dict(profiles) if profiles is not None else list_export_profiles()
        self._default = default_export_profile_id()
        if self._default not in self._profiles and self._profiles:
            self._default = next(iter(sorted(self._profiles)))

    def compose(self) -> ComposeResult:
        options = [(_profile_label(self._profiles[pid]), pid) for pid in sorted(self._profiles)]
        default: object = self._default if self._default in self._profiles else select_null()
        if options and select_is_blank(default):
            default = options[0][1]
        with Vertical(id="modal-container"):
            yield Static("[bold]" + t("export-profile-title") + "[/bold]")
            yield Static(t("export-profile-hint"), classes="dim")
            yield Select(
                options,
                value=default,
                id="export-profile-select",
                classes="field-select",
                allow_blank=not bool(options),
            )
            with Horizontal(id="export-profile-buttons", classes="modal-footer"):
                yield Button(t("export-profile-export"), variant="primary", id="export-profile-ok")
                yield Button(t("ui-cancel"), id="export-profile-cancel")

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def _commit(self) -> None:
        sel = self.query_one("#export-profile-select", Select)
        raw = sel.value
        if select_is_blank(raw):
            self.dismiss(None)
            return
        pid = str(raw).strip()
        self.dismiss(pid if pid in self._profiles else None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export-profile-ok":
            self._commit()
        elif event.button.id == "export-profile-cancel":
            self.dismiss(None)
