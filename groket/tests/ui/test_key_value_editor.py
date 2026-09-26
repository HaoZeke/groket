"""KeyValueEditor and env/inline-skill modals."""

from __future__ import annotations

import pytest
from groket.ui.env_modals import (
    EnvEditorModal,
    InlineSkillModal,
    build_skill_md,
    parse_skill_md,
    sanitize_skill_id,
    validate_skill_id,
)
from groket.ui.i18n import setup_i18n
from groket.ui.widgets.key_value_editor import KeyValueEditor
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, TextArea

from .pilot_helpers import wait_until


@pytest.fixture(autouse=True)
def _i18n() -> None:
    setup_i18n("en")


def test_skill_id_sanitize_and_validate() -> None:
    assert sanitize_skill_id("Run Skill!") == "run-skill"
    assert validate_skill_id("run-skill")
    assert not validate_skill_id("x")
    assert not validate_skill_id("-bad")


def test_build_and_parse_skill_md_roundtrip() -> None:
    md = build_skill_md(
        skill_id="hint",
        description="Use when the agent needs a short hint.",
        body="# Hint\n\nBe brief.\n",
    )
    assert md.startswith("---\nname: hint\n")
    name, desc, body = parse_skill_md(md)
    assert name == "hint"
    assert "short hint" in desc
    assert "Be brief" in body


class _KvApp(App[None]):
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        super().__init__()
        self._initial = initial or {}

    def compose(self) -> ComposeResult:
        yield KeyValueEditor(self._initial, id="kv")


@pytest.mark.asyncio
async def test_key_value_editor_get_set_values() -> None:
    async with _KvApp({"B": "2", "A": "1"}).run_test() as pilot:
        ed = pilot.app.query_one("#kv", KeyValueEditor)
        assert ed.get_values() == {"A": "1", "B": "2"}
        ed.set_values({"X": "y"})
        await pilot.pause()
        assert ed.get_values() == {"X": "y"}
        await pilot.click("#kv-add")
        await pilot.pause()
        assert ed.get_values() == {"X": "y"}


@pytest.mark.asyncio
async def test_key_value_editor_empty_starts_with_blank_row() -> None:
    async with _KvApp({}).run_test() as pilot:
        ed = pilot.app.query_one("#kv", KeyValueEditor)
        assert ed.get_values() == {}


@pytest.mark.asyncio
async def test_key_value_editor_delete_row_keeps_one_blank() -> None:
    async with _KvApp({"ONLY": "1"}).run_test() as pilot:
        ed = pilot.app.query_one("#kv", KeyValueEditor)
        assert ed.get_values() == {"ONLY": "1"}
        del_btn = ed.query_one(".kv-del", Button)
        ed.on_button_pressed(Button.Pressed(del_btn))
        await pilot.pause()
        assert ed.get_values() == {}
        assert list(ed.query(".kv-row"))


@pytest.mark.asyncio
async def test_env_editor_modal_save_and_cancel() -> None:
    results: list[dict[str, str] | None] = []

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(EnvEditorModal({"K": "V"}), results.append)

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, EnvEditorModal),
            description="EnvEditorModal shown",
        )
        modal = app.screen
        assert isinstance(modal, EnvEditorModal)
        modal.action_save()
        await wait_until(pilot, lambda: bool(results), description="env saved")
        assert results[-1] == {"K": "V"}

    results.clear()

    class H2(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(EnvEditorModal({"A": "1"}), results.append)

    app2 = H2()
    async with app2.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app2.screen, EnvEditorModal),
            description="EnvEditorModal shown",
        )
        modal = app2.screen
        assert isinstance(modal, EnvEditorModal)
        modal.action_cancel()
        await wait_until(pilot, lambda: bool(results), description="env cancelled")
        assert results[-1] is None


@pytest.mark.asyncio
async def test_env_editor_modal_button_handlers() -> None:
    results: list[dict[str, str] | None] = []

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(EnvEditorModal({"X": "y"}), results.append)

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, EnvEditorModal),
            description="EnvEditorModal shown",
        )
        modal = app.screen
        assert isinstance(modal, EnvEditorModal)
        modal.query_one("#env-save", Button).press()
        await wait_until(pilot, lambda: bool(results), description="save btn")
        assert results[-1] == {"X": "y"}

    results.clear()

    class H2(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(EnvEditorModal({"Z": "9"}), results.append)

    app2 = H2()
    async with app2.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app2.screen, EnvEditorModal),
            description="EnvEditorModal shown",
        )
        modal = app2.screen
        assert isinstance(modal, EnvEditorModal)
        modal.query_one("#env-cancel", Button).press()
        await wait_until(pilot, lambda: bool(results), description="cancel btn")
        assert results[-1] is None


@pytest.mark.asyncio
async def test_inline_skill_modal_save_requires_fields() -> None:
    results: list[tuple[str, str] | None] = []

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(InlineSkillModal(name="", body=""), results.append)

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, InlineSkillModal),
            description="InlineSkillModal shown",
        )
        modal = app.screen
        assert isinstance(modal, InlineSkillModal)
        modal.action_save()
        await pilot.pause()
        assert not results
        modal.query_one("#inline-skill-name", Input).value = "run skill!"
        modal.action_save()
        await pilot.pause()
        assert not results
        modal.query_one(
            "#inline-skill-description", Input
        ).value = "Use when testing inline skills in eval runs."
        modal.query_one("#inline-skill-body", TextArea).load_text("# Steps\n\nDo the thing.\n")
        modal.query_one("#inline-skill-save", Button).press()
        await wait_until(pilot, lambda: bool(results), description="skill saved")
        assert results[-1] is not None
        name, body = results[-1]
        assert name == "run-skill"
        assert body.startswith("---\n")
        assert "name: run-skill" in body
        assert "Use when testing inline skills" in body
        assert "Do the thing" in body


@pytest.mark.asyncio
async def test_inline_skill_modal_cancel() -> None:
    results: list[tuple[str, str] | None] = []

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(InlineSkillModal(name="x"), results.append)

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, InlineSkillModal),
            description="InlineSkillModal shown",
        )
        modal = app.screen
        assert isinstance(modal, InlineSkillModal)
        modal.query_one("#inline-skill-cancel", Button).press()
        await wait_until(pilot, lambda: bool(results), description="cancelled")
        assert results[-1] is None
