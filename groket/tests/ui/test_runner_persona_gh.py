"""Runner persona GitHub hint and combined capability summary."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.runs.personas import Persona, PersonaStore
from groket.runs.run_manager import RunManager
from groket.ui.forms import PERSONA_NONE, persona_select_value
from groket.ui.screens.runner import RunnerScreen
from textual.app import App
from textual.widgets import Select, Static, TabbedContent

from .pilot_helpers import assert_static_contains, static_plain, wait_until


class _Harness(App[None]):
    def __init__(self, work: Path) -> None:
        super().__init__()
        self._work = work
        self.run_manager = RunManager(work)

    async def on_mount(self) -> None:
        self.push_screen(RunnerScreen(self._work, run_manager=self.run_manager))


async def _runner(pilot, app: App) -> RunnerScreen:
    await wait_until(
        pilot,
        lambda: isinstance(app.screen, RunnerScreen) and bool(app.screen.query(TabbedContent)),
        description="runner ready",
    )
    scr = app.screen
    assert isinstance(scr, RunnerScreen)
    return scr


def _save_gh_persona(work: Path) -> None:
    store = PersonaStore(work)
    store.save(
        Persona(
            persona_id="gh-p",
            name="gh-p",
            github_write=True,
            github_token="tok",
            mcp_servers=["persona-mcp"],
            skills=["persona-skill"],
            plugins=["persona-plugin"],
            env_vars={"P_ENV": "1"},
        )
    )


@pytest.mark.asyncio
async def test_selecting_gh_persona_via_widget_shows_gh_on(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    _save_gh_persona(work)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _runner(pilot, app)
        # Rebuild options then set Select like a user pick (fires Changed).
        scr._refresh_persona_select(select_id=None)
        await pilot.pause()
        sel = scr.query_one("#persona-select", Select)
        sel.value = persona_select_value("gh-p")
        # Select.Changed is async; a single pause can return before the handler
        # rebuilds the runtime panel (CI flake under load).
        await wait_until(
            pilot,
            lambda: scr._persona_id == "gh-p",
            description="persona gh-p selected",
        )
        assert scr._persona_id_from_form() == "gh-p"
        panel = scr.query_one("#runtime-launch-panel", Static)
        text = assert_static_contains(panel, "gh on", msg="runtime-launch-panel after select")
        assert "gh off" not in text.lower()


@pytest.mark.asyncio
async def test_selecting_none_clears_previous_persona(tmp_path: Path) -> None:
    """None must not fall back to a stale persona_id (tree-sitter MCP leak)."""
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    _save_gh_persona(work)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _runner(pilot, app)
        scr._refresh_persona_select(select_id="gh-p")
        await wait_until(
            pilot,
            lambda: scr._persona_id_from_form() == "gh-p",
            description="persona gh-p selected",
        )
        sel = scr.query_one("#persona-select", Select)
        sel.value = PERSONA_NONE
        # Select.Changed is async; a single pause can return before the handler
        # clears _persona_id (CI flake under load).
        await wait_until(
            pilot,
            lambda: scr._persona_id == "" and scr._persona_id_from_form() == "",
            description="persona cleared to none",
        )
        caps = scr._persona_capability_snapshot()
        assert caps == ([], [], [], {})


@pytest.mark.asyncio
async def test_run_caps_summary_shows_persona_and_run_merged(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    _save_gh_persona(work)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _runner(pilot, app)
        scr._refresh_persona_select(select_id="gh-p")
        scr._run_mcp_ids = ["run-mcp"]
        scr._run_skills_ids = ["run-skill"]
        scr._run_plugins_ids = ["run-plugin"]
        scr._run_env_vars = {"R_ENV": "2"}
        scr._run_inline_skills = [("inline-a", "body")]
        scr._rebuild_run_capability_lists()
        await pilot.pause()
        summary = static_plain(scr.query_one("#run-caps-summary", Static))
        assert "persona-mcp" in summary
        assert "run-mcp" in summary
        assert "persona-skill" in summary
        assert "run-skill" in summary
        assert "persona-plugin" in summary
        assert "run-plugin" in summary
        assert "inline-a" in summary
        runtime = static_plain(scr.query_one("#runtime-launch-panel", Static))
        # Unified Runtime panel: persona + effective plugins/skills/inline/mcp/env
        assert "persona-plugin" in runtime
        assert "run-plugin" in runtime
        assert "persona-skill" in runtime
        assert "run-skill" in runtime
        assert "persona-mcp" in runtime
        assert "run-mcp" in runtime
        assert "inline-a" in runtime
        assert "P_ENV" in runtime
        assert "R_ENV" in runtime
        low = runtime.lower()
        assert "plugins" in low
        assert "skills" in low
        assert "mcp" in low
        assert "inline" in low
        assert "env" in low


def test_inline_skills_roundtrip_in_run_config(tmp_path: Path) -> None:
    from groket.runs.run_configs import RunConfigStore

    work = tmp_path / "work"
    (work / "runs" / "run_configs").mkdir(parents=True)
    store = RunConfigStore(work)
    cfg = store.save_from_launch(
        prompt="do the thing",
        setup_instructions="",
        docker_image="fully-loaded",
        repo_url="",
        repo_branch="",
        models=["m1"],
        parallelism=1,
        run_id="r1",
        name="with-inline",
        run_plugins=["superpowers"],
        run_inline_skills=[("hint", "skill body here")],
    )
    loaded = store.get(cfg.config_id)
    assert loaded is not None
    assert loaded.run_plugins == ["superpowers"]
    assert loaded.run_inline_skills == [{"id": "hint", "content": "skill body here"}]
    pre = loaded.to_runner_prefill()
    assert pre.run_plugins == ["superpowers"]
    assert pre.run_inline_skills == [("hint", "skill body here")]
