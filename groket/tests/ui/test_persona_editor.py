"""Mount persona editor / runner widgets and exercise handlers (catches NameError etc.)."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.runs.personas import Persona
from groket.ui.screens.personas import PersonaEditorModal, PluginPickerModal
from groket.ui.screens.runner import RunnerScreen

# Helpers that must never appear unless defined (botched renames from agent edits).
_FORBIDDEN_UNDEFINED = (
    "_text_to_ids",  # real name is _ids_from_text
    "_ids_to_list",
)


def test_personas_screen_no_forbidden_undefined_helpers() -> None:
    root = Path(__file__).resolve().parents[2]
    src = (root / "groket" / "ui" / "screens" / "personas.py").read_text(encoding="utf-8")
    for name in _FORBIDDEN_UNDEFINED:
        assert f"def {name}" in src or name not in src, (
            f"{name} is used in personas.py but not defined "
            f"(did you mean _ids_from_text / _ids_to_text?)"
        )


def test_runner_screen_source_has_plugin_button_id() -> None:
    root = Path(__file__).resolve().parents[2]
    src = (root / "groket" / "ui" / "screens" / "runner.py").read_text(encoding="utf-8")
    assert "run-plugins-pick-btn" in src
    assert "_run_plugins_from_form" in src


@pytest.mark.asyncio
async def test_persona_editor_plugins_pick_handler(tmp_path: Path) -> None:
    """Regression: _pe_plugins_pick must not NameError on helper name."""
    from textual.app import App

    persona = Persona(persona_id="t1", name="Test", plugins=["already"])

    class Harness(App[None]):
        CSS_PATH = None

        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(tmp_path, persona, is_new=False))

    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.screen
        assert isinstance(editor, PersonaEditorModal)
        # Must not raise NameError
        editor._pe_plugins_pick()
        await pilot.pause()
        top = app.screen
        assert isinstance(top, PluginPickerModal)
        assert "already" in top._selected
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_persona_editor_save_reads_plugins_field(tmp_path: Path) -> None:
    from textual.app import App

    persona = Persona(persona_id="t2", name="Test2", plugins=[])

    class Harness(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(tmp_path, persona, is_new=False))

    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.screen
        assert isinstance(editor, PersonaEditorModal)
        editor.query_one("#pe-plugins-ids").load_text("chrome-devtools-mcp\n")
        # action_save builds Persona — must not NameError on plugins=
        # May fail on disk save if personas dir issues; catch and inspect
        try:
            editor.action_save()
        except Exception as exc:
            # NameError is always a fail; OSError on save is ok for this smoke
            assert not isinstance(exc, NameError), exc
        await pilot.pause()


@pytest.mark.asyncio
async def test_mcp_picker_registry_mode_searches(tmp_path: Path, monkeypatch) -> None:
    """Registry mode must call search_registry (not only local catalog)."""
    from groket.capabilities.registry import RegistryServerHit
    from groket.ui.screens.personas import McpPickerModal
    from textual.app import App

    calls: list[str] = []

    def _fake_search(q: str, **_kwargs):
        calls.append(q)
        hit = RegistryServerHit(name="test/org/slack", title="Slack", description="x")
        return [hit], ""

    monkeypatch.setattr("groket.capabilities.registry.search_registry", _fake_search)
    monkeypatch.setattr("groket.capabilities.search_registry", _fake_search)

    class Harness(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpPickerModal(tmp_path, initial_query="slack", auto_registry=True))

    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Worker may need a tick
        for _ in range(20):
            if calls:
                break
            await pilot.pause()
        assert calls, "expected search_registry to run in registry mode"
        assert "slack" in calls[0].lower() or calls[0] == "slack"


@pytest.mark.asyncio
async def test_runner_screen_mounts(tmp_path: Path) -> None:
    from textual.app import App

    class Harness(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(RunnerScreen(tmp_path))

    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, RunnerScreen)
        app.screen.query_one("#prompt-input")
        app.screen.query_one("#run-plugins-pick-btn")
        app.screen.query_one("#runner-tabs")
        # Invoke plugin picker handler without NameError
        app.screen._run_plugins_pick_btn()
        await pilot.pause()
