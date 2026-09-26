"""Pilot: PersonasScreen, PersonaEditorModal, picker modals.

Uses Textual ``App.run_test()`` + Pilot. Synchronisation via ``wait_until``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.runs.personas import Persona, PersonaStore
from groket.ui.data_table import restore_cursor
from groket.ui.screens.personas import (
    McpConfigureModal,
    McpPickerModal,
    PersonaEditorModal,
    PersonasScreen,
    PluginPickerModal,
    SkillsPickerModal,
    _ids_from_text,
    _ids_to_text,
    _slug_id,
)
from textual.app import App
from textual.widgets import DataTable, Input

from .pilot_helpers import wait_until


def _make_work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    return work


def _seed_personas(work: Path) -> list[Persona]:
    store = PersonaStore(work)
    store.ensure_defaults()
    p1 = Persona(
        persona_id="test-alpha",
        name="Alpha",
        github_write=True,
        github_token="ghp_1234567890",
        env_vars={"API_KEY": "val"},
        mcp_servers=["slack"],
        skills=["bash"],
        plugins=["chrome-devtools"],
    )
    store.save(p1)
    p2 = Persona(persona_id="test-beta", name="Beta")
    store.save(p2)
    return [p1, p2]


# ── Pure helper tests ────────────────────────────────────────────────────


def test_slug_id() -> None:
    assert _slug_id("Hello World!") == "hello-world"
    assert _slug_id("") == "persona"
    assert _slug_id("x" * 100) == "x" * 48


def test_ids_roundtrip() -> None:
    ids = ["alpha", "beta", "gamma"]
    text = _ids_to_text(ids)
    parsed = _ids_from_text(text)
    assert parsed == ids


def test_ids_from_text_deduplicates() -> None:
    assert _ids_from_text("a\nb\na") == ["a", "b"]


def test_ids_from_text_comma_separated() -> None:
    assert _ids_from_text("a, b, c") == ["a", "b", "c"]


# ── PersonasScreen ───────────────────────────────────────────────────────


class _PersonasHarness(App[None]):
    def __init__(self, work: Path) -> None:
        super().__init__()
        self._work = work

    async def on_mount(self) -> None:
        self.push_screen(PersonasScreen(self._work))


async def _wait_personas(pilot, app: App) -> PersonasScreen:
    def ready() -> bool:
        scr = app.screen
        if not isinstance(scr, PersonasScreen):
            return False
        try:
            scr.query_one("#pb-table", DataTable)
            return True
        except Exception:
            return False

    await wait_until(pilot, ready, description="PersonasScreen table ready")
    scr = app.screen
    assert isinstance(scr, PersonasScreen)
    return scr


@pytest.mark.asyncio
async def test_personas_screen_mounts_with_defaults(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        table = scr.query_one("#pb-table", DataTable)
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_personas_screen_with_custom(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_personas(work)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        table = scr.query_one("#pb-table", DataTable)
        assert table.row_count >= 3


@pytest.mark.asyncio
async def test_personas_refresh(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        scr.action_refresh_context()
        await pilot.pause()


@pytest.mark.asyncio
async def test_personas_detail_shown(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_personas(work)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        assert scr._selected_id is not None
        scr._show_detail(scr._selected_id)
        await pilot.pause()


@pytest.mark.asyncio
async def test_personas_delete(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_personas(work)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        table = scr.query_one("#pb-table", DataTable)
        before = table.row_count
        restore_cursor(table, "test-beta")
        scr._selected_id = "test-beta"
        # First press arms pending; second commits (same as sessions/configs).
        scr.action_delete_persona()
        await pilot.pause()
        assert scr._delete_pending_ids is not None
        assert table.row_count == before
        scr.action_delete_persona()
        await pilot.pause()
        scr._reload_table()
        await pilot.pause()
        assert table.row_count < before
        assert scr._delete_pending_ids is None


@pytest.mark.asyncio
async def test_personas_new_opens_editor(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        scr.action_new_persona()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, PersonaEditorModal) for s in app.screen_stack),
            description="PersonaEditorModal opened",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_personas_edit_opens_editor(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_personas(work)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        scr._selected_id = "test-alpha"
        scr.action_edit_persona()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, PersonaEditorModal) for s in app.screen_stack),
            description="PersonaEditorModal opened for edit",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_personas_go_back(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        scr.action_go_back()
        await pilot.pause()


@pytest.mark.asyncio
async def test_personas_path_button(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _PersonasHarness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_personas(pilot, app)
        scr._btn_path()
        await pilot.pause()


# ── PersonaEditorModal ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editor_new_persona_save(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(work, persona=None, is_new=True))

    app = H()
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PersonaEditorModal),
            description="PersonaEditorModal shown",
        )
        editor = app.screen
        assert isinstance(editor, PersonaEditorModal)
        editor.query_one("#pe-id", Input).value = "new-persona-1"
        editor.query_one("#pe-name", Input).value = "New Persona"
        editor.query_one("#pe-desc", Input).value = "A test persona"
        editor.action_save()
        await pilot.pause()
        store = PersonaStore(work)
        saved = store.get("new-persona-1")
        assert saved is not None
        assert saved.name == "New Persona"


@pytest.mark.asyncio
async def test_editor_save_empty_id(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(work, persona=None, is_new=True))

    app = H()
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PersonaEditorModal),
            description="PersonaEditorModal shown",
        )
        editor = app.screen
        assert isinstance(editor, PersonaEditorModal)
        editor.action_save()
        await pilot.pause()
        # Should still be on editor (error notification, not dismissed)
        assert isinstance(app.screen, PersonaEditorModal)


@pytest.mark.asyncio
async def test_editor_cancel(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(work, persona=None, is_new=True))

    app = H()
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PersonaEditorModal),
            description="PersonaEditorModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_editor_tab_switching(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    p = Persona(persona_id="tab-test", name="Tab Test")

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(work, persona=p, is_new=False))

    app = H()
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PersonaEditorModal),
            description="PersonaEditorModal shown",
        )
        editor = app.screen
        assert isinstance(editor, PersonaEditorModal)
        for action in (
            editor.action_tab_github,
            editor.action_tab_env,
            editor.action_tab_mcp,
            editor.action_tab_skills,
            editor.action_tab_plugins,
            editor.action_tab_identity,
        ):
            action()
            await pilot.pause()
        editor.action_tab_next()
        await pilot.pause()
        editor.action_tab_prev()
        await pilot.pause()


@pytest.mark.asyncio
async def test_editor_edit_existing(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_personas(work)
    store = PersonaStore(work)
    p = store.get("test-alpha")
    assert p is not None

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonaEditorModal(work, persona=p, is_new=False))

    app = H()
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PersonaEditorModal),
            description="PersonaEditorModal for edit",
        )
        editor = app.screen
        assert isinstance(editor, PersonaEditorModal)
        editor.query_one("#pe-name", Input).value = "Alpha Renamed"
        editor.action_save()
        await pilot.pause()
        updated = store.get("test-alpha")
        assert updated is not None
        assert updated.name == "Alpha Renamed"


# ── SkillsPickerModal ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_picker_mount_and_toggle(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(SkillsPickerModal(work, selected=["bash"]))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SkillsPickerModal),
            description="SkillsPickerModal shown",
        )
        modal = app.screen
        assert isinstance(modal, SkillsPickerModal)
        assert "bash" in modal._selected
        modal.action_toggle_select()
        await pilot.pause()
        modal.action_done()
        await pilot.pause()


@pytest.mark.asyncio
async def test_skills_picker_cancel(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(SkillsPickerModal(work))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SkillsPickerModal),
            description="SkillsPickerModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_skills_picker_tab_actions(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(SkillsPickerModal(work))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SkillsPickerModal),
            description="SkillsPickerModal shown",
        )
        modal = app.screen
        assert isinstance(modal, SkillsPickerModal)
        # Pickers are not tabbed panes (TabPaneNavigation is only on PersonaEditorModal).
        assert not hasattr(modal, "TAB_PANES") or not getattr(modal, "TAB_PANES", ())


# ── PluginPickerModal ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugin_picker_mount_and_toggle(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PluginPickerModal(work, selected=["plug-a"]))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PluginPickerModal),
            description="PluginPickerModal shown",
        )
        modal = app.screen
        assert isinstance(modal, PluginPickerModal)
        assert "plug-a" in modal._selected
        modal.action_toggle_select()
        await pilot.pause()
        modal.action_done()
        await pilot.pause()


@pytest.mark.asyncio
async def test_plugin_picker_cancel(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PluginPickerModal(work))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PluginPickerModal),
            description="PluginPickerModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


# ── McpPickerModal ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_picker_local_mode(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpPickerModal(work, selected=["test-mcp"]))

    app = H()
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpPickerModal),
            description="McpPickerModal shown",
        )
        modal = app.screen
        assert isinstance(modal, McpPickerModal)
        modal.action_local_mode()
        await pilot.pause()
        modal.action_toggle_select()
        await pilot.pause()
        modal.action_done()
        await pilot.pause()


@pytest.mark.asyncio
async def test_mcp_picker_cancel(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpPickerModal(work))

    app = H()
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpPickerModal),
            description="McpPickerModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_mcp_picker_tab_actions(tmp_path: Path) -> None:
    work = _make_work(tmp_path)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpPickerModal(work))

    app = H()
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpPickerModal),
            description="McpPickerModal shown",
        )
        modal = app.screen
        assert isinstance(modal, McpPickerModal)
        assert not getattr(modal, "TAB_PANES", ())
        modal.action_registry_search()
        await pilot.pause()


# ── McpConfigureModal ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_configure_save(tmp_path: Path) -> None:
    defn = {
        "id": "slack",
        "title": "Slack MCP",
        "description": "Slack integration",
        "transport": "http",
        "url": "https://mcp.example.com",
        "command": "",
        "args": [],
        "needs_env": ["SLACK_TOKEN"],
        "headers": {},
    }

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpConfigureModal(defn))

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpConfigureModal),
            description="McpConfigureModal shown",
        )
        modal = app.screen
        assert isinstance(modal, McpConfigureModal)
        modal.action_save()
        await pilot.pause()


@pytest.mark.asyncio
async def test_mcp_configure_cancel(tmp_path: Path) -> None:
    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpConfigureModal({"id": "test"}))

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpConfigureModal),
            description="McpConfigureModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_mcp_configure_empty_id(tmp_path: Path) -> None:
    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpConfigureModal({"id": ""}))

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpConfigureModal),
            description="McpConfigureModal shown",
        )
        modal = app.screen
        assert isinstance(modal, McpConfigureModal)
        modal.query_one("#mcp-cfg-id", Input).value = ""
        modal.action_save()
        await pilot.pause()
        # Should not dismiss — error
        assert isinstance(app.screen, McpConfigureModal)


@pytest.mark.asyncio
async def test_mcp_configure_with_docs_links(tmp_path: Path) -> None:
    defn = {
        "id": "github",
        "registry_name": "github-mcp",
        "description": "GitHub API",
        "transport": "http",
        "url": "https://mcp.github.com",
        "version": "1.2.3",
        "status": "stable",
        "repository_url": "https://github.com/modelcontextprotocol/github",
        "registry_url": "https://registry.mcp.io/github",
        "docs_links": [
            {"label": "Docs", "url": "https://docs.example.com"},
        ],
        "needs_env": [],
        "headers": {"Authorization": "Bearer token"},
    }

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(McpConfigureModal(defn, title="Configure GitHub"))

    app = H()
    async with app.run_test(size=(120, 50)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, McpConfigureModal),
            description="McpConfigureModal with docs shown",
        )
        modal = app.screen
        assert isinstance(modal, McpConfigureModal)
        modal.action_save()
        await pilot.pause()
