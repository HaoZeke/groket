"""RulesScreen Pilot tests (user rules only — no built-in catalog)."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.engine.detectors import clear_detectors
from groket.engine.loader import reload_config
from groket.ui.app import TraceEvalApp
from groket.ui.screens.rules import RulesScreen
from textual.widgets import DataTable

from .pilot_helpers import wait_until


@pytest.fixture()
def rules_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(__file__).resolve().parents[2]
    det = tmp_path / "detectors"
    rules = tmp_path / "rules"
    plugins = tmp_path / "plugins"
    det.mkdir()
    rules.mkdir()
    plugins.mkdir()
    for name in ("demo_detector.py",):
        src = root / "examples" / "detection" / "minimal" / "detectors" / name
        (det / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (rules / "demo_rule.yaml").write_text(
        (root / "examples" / "detection" / "minimal" / "rules" / "demo_rule.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    for mod in ("groket.paths", "groket.engine.loader"):
        monkeypatch.setattr(f"{mod}.user_detectors_dir", lambda d=det: d)
        monkeypatch.setattr(f"{mod}.user_rules_dir", lambda r=rules: r)
        monkeypatch.setattr(f"{mod}.user_analysis_plugins_dir", lambda p=plugins: p)
    clear_detectors()
    reload_config()
    return tmp_path


def _make_app(tmp_path: Path) -> TraceEvalApp:
    work = tmp_path / "w"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    return TraceEvalApp(work_dir=work, traces_path=traces)


@pytest.mark.asyncio
async def test_rules_screen_mounts_table(rules_home: Path, tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(RulesScreen())
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, RulesScreen),
            description="RulesScreen mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#rule-table"))),
            description="rule-table mounted",
        )
        table = app.screen.query_one("#rule-table", DataTable)
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_rules_screen_toggle_rule(rules_home: Path, tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(RulesScreen())
        await wait_until(
            pilot,
            lambda: (
                app.screen.query_one("#rule-table", DataTable).row_count > 0
                if list(app.screen.query("#rule-table"))
                else False
            ),
            description="rules loaded",
        )
        screen = app.screen
        assert isinstance(screen, RulesScreen)
        screen.action_toggle_rule()
        await pilot.pause()


@pytest.mark.asyncio
async def test_rules_screen_enable_disable_all(rules_home: Path, tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(RulesScreen())
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#rule-table"))),
            description="rule-table ready",
        )
        screen = app.screen
        assert isinstance(screen, RulesScreen)
        screen.action_enable_all()
        await pilot.pause()
        screen.action_disable_all()
        await pilot.pause()
