"""Header small mark, help mark, and paths banner."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.ui.app import TraceEvalApp
from groket.ui.brand_mark import (
    COMPLETE,
    FAILED,
    RUNNING,
    AppChrome,
    AppFooter,
    HelpBrand,
    help_mark,
    paths_banner,
)
from groket.ui.i18n import t
from groket.ui.widgets.activity_bar import ActivityBar
from groket.ui.widgets.help_modal import HelpModal
from textual.app import App, ComposeResult
from textual.widgets import Static

from .pilot_helpers import assert_static_contains, static_plain, wait_until


def _styles(mark) -> str:
    return "".join(str(span.style) for span in mark.spans)


def test_paths_banner_is_eval_line() -> None:
    line = paths_banner(Path("/tmp/work/runs/traces"))
    assert "Eval" in line
    assert "█" not in line
    both = paths_banner(Path("/e"), Path("/h"))
    assert "Eval" in both
    assert "Host" in both
    assert " │ " in both
    assert "·" not in both


def test_help_mark_is_three_slats() -> None:
    mark = help_mark()
    assert mark.plain.splitlines() == ["██████", "██████", "██████"]
    styles = _styles(mark)
    assert COMPLETE in styles
    assert FAILED in styles
    assert RUNNING in styles


def _make_app(tmp_path: Path) -> TraceEvalApp:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    return TraceEvalApp(work_dir=work, traces_path=traces)


@pytest.mark.asyncio
async def test_home_chrome_is_one_row(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        chrome = app.query_one(AppChrome)
        assert chrome.size.height == 1
        assert "groket" in static_plain(chrome.query_one("#app-chrome-title", Static)).lower()
        assert chrome.query_one(ActivityBar)
        footer = app.query_one(AppFooter)
        assert footer.has_class("-compact")
        banner = chrome.query_one("#session-paths", Static)
        assert "Eval" in static_plain(banner)
        kids = [w.id for w in chrome.children]
        assert kids[0] == "session-paths"
        assert "app-chrome-title" in kids
        assert kids[-1] == "activity-bar"
        assert app.sub_title == ""


@pytest.mark.asyncio
async def test_home_chrome_hides_paths_when_narrow(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        banner = app.query_one("#session-paths", Static)
        assert static_plain(banner) == ""


@pytest.mark.asyncio
async def test_help_modal_shows_brand() -> None:
    class H(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("main")

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(HelpModal())
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query(HelpBrand))),
            description="HelpBrand mounted",
        )
        brand = app.screen.query_one(HelpBrand)
        assert_static_contains(brand.query_one("#help-brand-name", Static), t("help-brand-name"))
        assert_static_contains(
            brand.query_one("#help-brand-tagline", Static), t("help-brand-tagline")
        )
        assert "█" in static_plain(brand.query_one("#help-brand-mark", Static))
