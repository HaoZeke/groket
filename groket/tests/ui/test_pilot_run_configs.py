"""Pilot: RunConfigsScreen — table, select, detail, delete, cursor helpers.

Uses Textual ``App.run_test()`` + Pilot. Synchronisation via ``wait_until``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.runs.run_configs import RunConfig, RunConfigStore
from groket.runs.run_manager import RunManager
from groket.ui.screens.run_configs import RunConfigsScreen, _BatchLaunchModal, _ModelsOverrideModal
from textual.app import App
from textual.widgets import DataTable, Static

from .pilot_helpers import wait_until


def _make_work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    return work


def _seed_configs(work: Path, n: int = 3) -> list[RunConfig]:
    """Create *n* configs on disk and return them."""
    store = RunConfigStore(work)
    cfgs: list[RunConfig] = []
    for i in range(n):
        cfg = store.create(
            prompt=f"Fix bug #{i}",
            setup_instructions=f"echo setup-{i}",
            docker_image="fully-loaded",
            models=[f"model-{i}"],
            name=f"config-{i}",
        )
        cfg.task_id = f"task-{i}"
        cfg.category = f"cat-{i}"
        cfg.repo_url = f"https://github.com/org/repo-{i}"
        store.save(cfg)
        cfgs.append(cfg)
    return cfgs


class _Harness(App[None]):
    """Minimal host for RunConfigsScreen."""

    def __init__(self, work: Path) -> None:
        super().__init__()
        self._work = work
        self.run_manager = RunManager(work)

    async def on_mount(self) -> None:
        self.push_screen(RunConfigsScreen(self._work, run_manager=self.run_manager))


async def _wait_table(pilot, app: App) -> RunConfigsScreen:
    """Wait until RunConfigsScreen table has rendered."""

    def ready() -> bool:
        scr = app.screen
        if not isinstance(scr, RunConfigsScreen):
            return False
        try:
            scr.query_one("#rc-table", DataTable)
            return True
        except Exception:
            return False

    await wait_until(pilot, ready, description="RunConfigsScreen table ready")
    scr = app.screen
    assert isinstance(scr, RunConfigsScreen)
    return scr


# ── Mount ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_configs_mounts_empty(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        table = scr.query_one("#rc-table", DataTable)
        assert table.row_count == 0
        detail = scr.query_one("#rc-detail", Static)
        from .pilot_helpers import static_plain

        text = static_plain(detail)
        assert (
            text.strip() == ""
            or "config" in text.lower()
            or "select" in text.lower()
            or "no " in text.lower()
        )


@pytest.mark.asyncio
async def test_run_configs_mounts_with_data(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    cfgs = _seed_configs(work, n=3)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        table = scr.query_one("#rc-table", DataTable)
        assert table.row_count == len(cfgs)


# ── Select / deselect ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_select_and_select_all(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=3)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        assert len(scr._selected) == 0
        scr.action_toggle_select()
        await pilot.pause()
        assert len(scr._selected) == 1

        scr.action_select_all_toggle()
        await pilot.pause()
        assert len(scr._selected) == 3

        scr.action_select_all_toggle()
        await pilot.pause()
        assert len(scr._selected) == 0


# ── Detail / cursor ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_shows_detail(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=2)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr._show_detail_for_cursor()
        await pilot.pause()
        assert scr._selected_id is not None


# ── Refresh ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_context(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=1)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_refresh_context()
        await pilot.pause()
        assert scr.query_one("#rc-table", DataTable).row_count == 1


# ── Delete ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_config_double_press(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=2)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        table = scr.query_one("#rc-table", DataTable)
        assert table.row_count == 2

        # First press — sets pending
        scr.action_delete_config()
        await pilot.pause()
        assert scr._delete_pending_ids is not None

        # Second press — confirms delete
        scr.action_delete_config()
        await pilot.pause()
        table = scr.query_one("#rc-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_delete_no_config(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_delete_config()
        await pilot.pause()


# ── Navigation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_go_back(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_go_back()
        await pilot.pause()


@pytest.mark.asyncio
async def test_open_in_runner_no_config(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_open_in_runner()
        await pilot.pause()


@pytest.mark.asyncio
async def test_open_in_runner_with_config(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=1)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_open_in_runner()
        await pilot.pause()


# ── Launch (no auth → error toast) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_config_no_auth(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=1)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_launch_config()
        await pilot.pause()


@pytest.mark.asyncio
async def test_launch_selected_no_selection(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr.action_launch_selected()
        await pilot.pause()


# ── Button handlers ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_button_handlers(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=1)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        scr._btn_open()
        await pilot.pause()


# ── Cursor helper (static method) ────────────────────────────────────────


def test_cursor_key_after_deletes_various() -> None:
    keys = ["a", "b", "c", "d"]
    assert RunConfigsScreen._cursor_key_after_deletes(keys, "b", {"b"}) == "c"
    assert RunConfigsScreen._cursor_key_after_deletes(keys, "d", {"d"}) == "c"
    assert RunConfigsScreen._cursor_key_after_deletes(keys, "a", {"a", "b"}) == "c"
    assert RunConfigsScreen._cursor_key_after_deletes(keys, None, {"a"}) == "b"
    assert RunConfigsScreen._cursor_key_after_deletes([], "x", set()) is None
    assert RunConfigsScreen._cursor_key_after_deletes(keys, "x", {"a", "b", "c", "d"}) is None


# ── ModelsOverrideModal ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_models_override_modal_cancel(tmp_path: Path) -> None:
    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(_ModelsOverrideModal(["m1", "m2"]))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, _ModelsOverrideModal),
            description="ModelsOverrideModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_models_override_modal_submit_empty(tmp_path: Path) -> None:
    """Submit with no models selected notifies error."""

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(_ModelsOverrideModal([]))

    app = H()
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, _ModelsOverrideModal),
            description="ModelsOverrideModal shown",
        )
        app.screen.action_submit()
        await pilot.pause()


# ── BatchLaunchModal ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_launch_modal_cancel(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    cfgs = _seed_configs(work, n=2)

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(_BatchLaunchModal(cfgs, ["m1"]))

    app = H()
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, _BatchLaunchModal),
            description="BatchLaunchModal shown",
        )
        app.screen.action_cancel()
        await pilot.pause()


# ── Highlight callback ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_row_highlight_updates_detail(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    _seed_configs(work, n=3)
    app = _Harness(work)
    async with app.run_test(size=(140, 40)) as pilot:
        scr = await _wait_table(pilot, app)
        table = scr.query_one("#rc-table", DataTable)
        if table.row_count > 1:
            table.move_cursor(row=1, animate=False)
            await pilot.pause()
        scr._show_detail_for_cursor()
        await pilot.pause()
        assert scr._selected_id is not None
