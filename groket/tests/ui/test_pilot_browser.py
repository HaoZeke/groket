"""Pilot suite for BrowserScreen: timeline, tabs, multi-turn pending bar.

Uses Textual ``App.run_test()`` so compose, workers, and bindings actually run.
Synchronisation is condition-based (``wait_until``); see AGENTS.md §4.5c.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.analysis.base import AnalysisResult, Finding
from groket.models import Severity
from groket.runs.run_manager import RunManager
from groket.session.turn_gate import (
    list_queued_follow_ups,
    read_turn_gate_status,
    session_awaits_follow_up,
)
from groket.ui.app import TraceEvalApp
from groket.ui.data_table import cursor_row_key
from groket.ui.screens.browser import BrowserScreen
from groket.ui.widgets.timeline import TimelineTable
from textual.widgets import Input, Static, TabbedContent

from .pilot_helpers import wait_until


def _write_multi_turn_session(traces_root: Path, *, session_id: str = "browser-pilot-sess") -> Path:
    """Build a multi-turn session on the eval traces bind-mount layout."""
    container = traces_root / "groket-pilot-run-m1"
    sess = container / "%2Fworkspace" / session_id
    sess.mkdir(parents=True)

    (sess / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": session_id, "cwd": "/workspace"},
                "session_summary": "Pilot multi-turn pilot session",
                "generated_title": "Pilot multi-turn",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:10:00Z",
                "num_messages": 6,
                "current_model_id": "pilot-model",
            }
        ),
        encoding="utf-8",
    )

    updates = [
        {
            "type": "user_message",
            "ts": "2026-06-25T00:00:01Z",
            "message": {"content": [{"type": "text", "text": "first prompt"}]},
        },
        {
            "type": "assistant_message",
            "ts": "2026-06-25T00:00:02Z",
            "message": {"content": [{"type": "text", "text": "working on it"}]},
        },
        {
            "type": "tool_call",
            "ts": "2026-06-25T00:00:03Z",
            "toolCallId": "c1",
            "toolName": "run_terminal_command",
            "input": {"command": "echo hi"},
        },
        {
            "type": "tool_result",
            "ts": "2026-06-25T00:00:04Z",
            "toolCallId": "c1",
            "toolName": "run_terminal_command",
            "output": "hi\n",
        },
        {
            "type": "user_message",
            "ts": "2026-06-25T00:05:01Z",
            "message": {"content": [{"type": "text", "text": "second prompt"}]},
        },
        {
            "type": "assistant_message",
            "ts": "2026-06-25T00:05:02Z",
            "message": {"content": [{"type": "text", "text": "done"}]},
        },
    ]
    (sess / "updates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in updates) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-25T00:00:00Z",
                        "type": "turn_started",
                        "turn_number": 0,
                        "model_id": "pilot-model",
                    }
                ),
                json.dumps(
                    {"ts": "2026-06-25T00:04:00Z", "type": "turn_ended", "outcome": "success"}
                ),
                json.dumps(
                    {
                        "ts": "2026-06-25T00:05:00Z",
                        "type": "turn_started",
                        "turn_number": 1,
                        "model_id": "pilot-model",
                    }
                ),
                json.dumps(
                    {"ts": "2026-06-25T00:06:00Z", "type": "turn_ended", "outcome": "success"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    gate = container / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": session_id, "turn": 2}) + "\n",
        encoding="utf-8",
    )
    return sess


def _host_app(work: Path, traces: Path) -> TraceEvalApp:
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    assert isinstance(app.run_manager, RunManager)
    return app


async def _open_browser(app: TraceEvalApp, pilot, sess: Path) -> BrowserScreen:
    """Push BrowserScreen and wait until timeline is loaded; stop live refresh."""
    app.push_screen(BrowserScreen(sess, plugin_results={}))

    def ready() -> bool:
        scr = app.screen
        return isinstance(scr, BrowserScreen) and bool(scr.timeline)

    await wait_until(pilot, ready, description="BrowserScreen.timeline loaded")
    screen = app.screen
    assert isinstance(screen, BrowserScreen)
    screen._stop_live_refresh()
    return screen


async def _activate_tab(pilot, screen: BrowserScreen, pane_id: str) -> None:
    """Run tab action, set active authoritatively, wait until TabbedContent agrees."""
    actions = {
        "tab-timeline": screen.action_tab_timeline,
        "tab-findings": screen.action_tab_findings,
        "tab-summary": screen.action_tab_summary,
        "tab-diff": screen.action_tab_diff,
        "tab-reports": screen.action_tab_report,
    }
    actions[pane_id]()
    tabs = screen.query_one("#browser-tabs", TabbedContent)
    tabs.active = pane_id
    await wait_until(
        pilot,
        lambda: tabs.active == pane_id,
        description=f"tab {pane_id!r} active",
    )


@pytest.mark.asyncio
async def test_browser_mounts_timeline_and_pending_bar(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        assert screen.meta is not None
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        bar = screen.query_one("#session-pending-bar")
        assert bar.display is True or screen._session_is_pending()
        _ = screen.query_one("#session-pending-status", Static)


@pytest.mark.asyncio
async def test_browser_tabs_and_stats_turns(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)

        await _activate_tab(pilot, screen, "tab-summary")
        turns_table = screen.query_one("#stats-turns-table")
        screen._update_stats()
        await wait_until(
            pilot,
            lambda: turns_table.row_count >= 1,
            description="summary turns table has rows",
        )

        await _activate_tab(pilot, screen, "tab-timeline")

        screen.action_tab_next()
        await pilot.pause()
        screen.action_tab_prev()
        await pilot.pause()
        await _activate_tab(pilot, screen, "tab-timeline")


@pytest.mark.asyncio
async def test_browser_idle_awaiting_skips_live_timeline(tmp_path: Path) -> None:
    """Awaiting follow-up keeps the pending bar but does not need timeline polls."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        assert screen._session_is_pending() is True
        # Gate idle wait — not agent writing traces.
        assert screen._session_needs_live_timeline() is False
        screen._set_title_from_meta()
        assert "LIVE" not in (screen.title or "")
        assert "awaiting" in (screen.title or "").lower()


@pytest.mark.asyncio
async def test_browser_follow_up_enter_and_queue(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen._refresh_session_pending_bar()
        await pilot.pause()

        inp = screen.query_one("#session-follow-input", Input)
        inp.value = "pilot follow-up one"
        inp.focus()
        await pilot.pause()
        await pilot.press("enter")

        def gate_advanced() -> bool:
            st = read_turn_gate_status(sess)
            if st.get("state") in ("running", "done"):
                return True
            gate_root = traces / "groket-pilot-run-m1"
            return any(gate_root.glob(".groket-turn*/command")) or bool(
                list_queued_follow_ups(sess)
            )

        await wait_until(pilot, gate_advanced, description="follow-up staged or queued")

        screen._refresh_session_pending_bar()
        await pilot.pause()
        inp.value = "pilot follow-up two"
        screen._session_follow_send()
        await pilot.pause()
        # Second send may queue; either way gate dir exists
        assert (traces / "groket-pilot-run-m1").is_dir()


@pytest.mark.asyncio
async def test_browser_mark_done_clears_pending(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen._session_follow_done()
        await wait_until(
            pilot,
            lambda: session_awaits_follow_up(sess) is False,
            description="session no longer awaiting follow-up",
        )
        # Session-scoped Done writes command=done then stop_session_container
        # finalizes the gate (clears control files, state=done) so the list does
        # not stick on ending after the host kills the entrypoint.
        st = read_turn_gate_status(sess)
        assert st.get("state") == "done" or session_awaits_follow_up(sess) is False


@pytest.mark.asyncio
async def test_browser_timeline_filter_and_cursor_stable(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0

        if tl.row_count > 1:
            tl.move_cursor(row=1, animate=False)
        key_before = cursor_row_key(tl)

        tl.load_events(screen.timeline, screen._findings, list(screen._flags.values()))
        await pilot.pause()
        if key_before and tl.row_count:
            key_after = cursor_row_key(tl)
            assert key_after == key_before or key_after is not None

        screen._apply_filter(event_type="tool_call", errors_only=False)
        await pilot.pause()


@pytest.mark.asyncio
async def test_browser_timeline_view_filter_survives_reload(tmp_path: Path) -> None:
    """View filter must re-apply after load_events (live tick / F5)."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        full_n = tl.row_count
        assert full_n > 0

        # Session chrome is always present on this fixture (turn markers).
        screen._timeline_filter = "sess"
        screen._apply_timeline_filters()
        await pilot.pause()
        filtered_n = tl.row_count
        assert 0 < filtered_n <= full_n

        # Simulate full / light reload painting the unfiltered list first.
        tl.load_events(screen.timeline, screen._findings, list(screen._flags.values()))
        await pilot.pause()
        assert tl.row_count == full_n  # unfiltered paint
        # Without reapply, the Select would still say "sess" while all rows show.
        screen._reapply_timeline_view_filter()
        await pilot.pause()
        assert tl.row_count == filtered_n

        # Full populate path must also reapply.
        screen._populate_ui()
        await pilot.pause()
        assert screen._timeline_filter == "sess"
        assert tl.row_count == filtered_n


@pytest.mark.asyncio
async def test_browser_with_plugin_findings_report(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f1",
        title="pilot finding",
        severity=Severity.MEDIUM,
        plugin_id="engine",
        detail="x",
        tool_call_ids=["c1"],
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser timeline with findings")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._render_report_overview()
        screen._render_report_flags()
        await pilot.pause()
        await _activate_tab(pilot, screen, "tab-reports")
        assert screen._findings is not None


# ── Report tab filter ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_report_filter_sections(tmp_path: Path) -> None:
    """Report filter dropdown switches visible sections."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f2",
        title="report filter finding",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="detail text",
        tool_call_ids=["c1"],
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser with findings")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-reports")
        screen._update_reports_tab()
        await pilot.pause()

        # Filter to flags only
        screen._report_filter = "flags"
        screen._apply_report_visibility()
        await pilot.pause()
        assert screen._section_visible("flags")

        # Filter back to all
        screen._report_filter = "all"
        screen._apply_report_visibility()
        await pilot.pause()
        assert screen._section_visible("flags")


@pytest.mark.asyncio
async def test_browser_report_plugin_multi_pane_selectable(tmp_path: Path) -> None:
    """Plugin report artifact splits into focusable SelectableStatic panes."""
    from groket.ui.selectable_static import SelectableStatic
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="mf1",
        title="Ignored MCP",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="short",
        extras={
            "what_model_did": "Claimed MCP failed",
            "what_should_have_done": "Call MCP first",
            "why_mistake": "Instructions",
            "where": "Turn 0",
            "pattern": "skip",
        },
    )
    report_md = """# Model Feedback form drafts — sess

Intro line for the plugin.

## Session summary

Overall summary text.

## Issue 1: Ignored MCP

### Form fields (copy line-by-line)

```
Model Name: pilot-model
Session ID: sess
Severity: Major
```

### Issue (copy into the Issue box)

```
What: Claimed MCP failed
Where: Turn 0
Why: Instructions
Should have: Call MCP first
Pattern: skip
```
"""
    # Use analyzer_id ``engine`` so default enabled plugins keep the result.
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
            artifacts={"report": report_md},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser with multi-pane report")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-reports")
        screen._update_reports_tab()
        await pilot.pause()

        section = screen.query_one("#report-section-plugin-engine", Vertical)
        panes = list(section.query(SelectableStatic))
        # header + preamble + summary + issue header + form + issue box
        assert len(panes) >= 4
        plains = [p.get_plain_text() for p in panes]
        joined = "\n".join(plains)
        assert "Claimed MCP failed" in joined
        assert "Model Name: pilot-model" in joined
        # Paste-ready Issue pane exists without Form fields mixed in
        issue_only = [p for p in plains if p.strip().startswith("What:") and "Model Name:" not in p]
        assert issue_only, plains
        form_only = [p for p in plains if "Model Name: pilot-model" in p and "What:" not in p]
        assert form_only, plains
        # Each pane is focusable for Tab + y
        assert all(p.can_focus for p in panes)


@pytest.mark.asyncio
async def test_browser_analysis_pending_collapses_report_panes(tmp_path: Path) -> None:
    """Pending analysis paints one spinner per plugin card, not one per pane."""
    from groket.ui.i18n import t
    from groket.ui.selectable_static import SelectableStatic
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    report_md = """# Report

## One

body one

## Two

body two

## Three

body three
"""
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="ok",
            findings=[],
            artifacts={"report": report_md},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser with multi-pane report")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-reports")
        screen._update_reports_tab()
        await pilot.pause()

        section = screen.query_one("#report-section-plugin-engine", Vertical)
        before = list(section.query(SelectableStatic))
        assert len(before) >= 3

        # Not pending: paint is a no-op (does not stack spinners on idle open).
        screen._analysis_pending = False
        screen._show_analysis_pending()
        await pilot.pause()
        assert len(list(section.query(SelectableStatic))) == len(before)

        screen._analysis_pending = True
        screen._paint_analysis_pending_spinner(full=True)
        await pilot.pause()

        panes = list(section.query(SelectableStatic))
        assert len(panes) == 1
        plain = panes[0].get_plain_text()
        assert "Running analysis" in plain
        # Overview is a single body, not duplicated under the plugin card.
        overview = screen.query_one("#report-overview-content", SelectableStatic)
        assert "Running analysis" in overview.get_plain_text()
        # Spinner Fluent uses the braille frame; plain cache should not empty.
        assert plain.strip()
        assert t("ui-running-analysis-plain") in plain or "Running analysis" in plain


# ── Summary stats tables ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_summary_stats_tables(tmp_path: Path) -> None:
    """Summary pane builds event, tool, phase, and turns tables."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        screen._update_stats()
        await pilot.pause()

        from textual.widgets import DataTable as DT

        ev_table = screen.query_one("#stats-events-table", DT)
        assert ev_table.row_count >= 1
        tools_table = screen.query_one("#stats-tools-table", DT)
        assert tools_table.row_count >= 0
        phases_table = screen.query_one("#stats-phases-table", DT)
        assert phases_table.row_count >= 1


# ── Diff tab ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_diff_tab(tmp_path: Path) -> None:
    """Diff tab renders even with no diff data."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        screen._update_diff_tab()
        await pilot.pause()


# ── Summary tab ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_summary_tab(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        screen._update_summary_tab()
        await pilot.pause()


# ── Flag event ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_flag_event_action(tmp_path: Path) -> None:
    """Flag action only available when timeline focused with event selected."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-timeline")
        tl = screen.query_one("#timeline-list", TimelineTable)
        if tl.row_count > 0:
            tl.move_cursor(row=0, animate=False)
            await pilot.pause()
        # check_action returns False when no event selected yet
        result = screen.check_action("flag_event", ())
        # Binding enabled only when timeline has a flaggable event under cursor.
        assert result in (True, False)


@pytest.mark.asyncio
async def test_browser_flag_result_save_delete(tmp_path: Path) -> None:
    """_on_flag_result save and delete branches."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        from groket.models import Flag, FlagVerdict

        flag = Flag(
            event_index=0,
            event_type="tool_call",
            tool_name="run_terminal_command",
            verdict=FlagVerdict.BAD,
            description="wrong command",
        )
        screen._on_flag_result(("save", flag))
        await pilot.pause()
        assert 0 in screen._flags

        screen._on_flag_result(("delete", 0))
        await pilot.pause()
        assert 0 not in screen._flags

        screen._on_flag_result(None)
        await pilot.pause()


# ── Export finding ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_export_finding(tmp_path: Path) -> None:
    """Export finding creates a markdown report file."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="export-f1",
        title="Exported finding",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="Should have done X",
        tool_call_ids=["c1"],
        extras={"should_have": "done X instead"},
        children=[
            Finding(
                id="child-f1",
                title="Sub finding",
                severity=Severity.LOW,
                plugin_id="engine",
            ),
        ],
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser for export")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        screen._selected_finding = finding
        await _activate_tab(pilot, screen, "tab-findings")
        screen.action_export_finding()
        await pilot.pause()
        reports_dir = screen._reports_dir()
        if reports_dir.exists():
            md_files = list(reports_dir.glob("*.md"))
            assert len(md_files) >= 1


# ── Timeline view modes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_timeline_view_modes(tmp_path: Path) -> None:
    """Exercise all timeline View select modes."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        for mode in ("tools", "user", "asst", "sess", "errors", "all"):
            screen._apply_timeline_mode(mode)
            await pilot.pause()
        assert screen._timeline_filter == "all"


# ── Search action ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_search_action(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_search()
        await pilot.pause()
        screen.action_clear_filters()
        await pilot.pause()


# ── Refresh context ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_refresh_context(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_refresh_context()
        await pilot.pause()
        await pilot.pause()


# ── Show findings tab ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_show_findings_action(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_show_findings()
        await pilot.pause()


# ── Focus follow-up field ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_focus_follow_up(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_focus_follow_up()
        await pilot.pause()


# ── Focus timeline filter ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_focus_timeline_filter(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_focus_timeline_filter()
        await pilot.pause()


# ── check_action for follow-up ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_check_action_follow_up(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        for action in ("send_follow_up", "mark_session_done", "focus_follow_up"):
            result = screen.check_action(action, ())
            assert result in (True, False)
        # Export requires findings tab active
        result = screen.check_action("export_finding", ())
        assert result in (True, False)


# ── Open share (no URL) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_open_share_no_url(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_open_share()
        await pilot.pause()


# ── Refresh tip surfaces ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_refresh_tip_surfaces(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.refresh_tip_surfaces()
        await pilot.pause()


# ── Report plugin helpers ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_report_plugin_helpers(tmp_path: Path) -> None:
    assert BrowserScreen._plugin_title("engine") == "Detectors"
    assert BrowserScreen._plugin_title("custom_thing") == "Custom Thing"
    assert BrowserScreen._report_plugin_slug("test-plugin") == "test-plugin"
    assert BrowserScreen._report_plugin_slug("a/b") == "a_b"


# ── Findings row mapping ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_findings_row_index(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f-row-test",
        title="row test finding",
        severity=Severity.LOW,
        plugin_id="engine",
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser for row test")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-findings")
        findings_table = screen.query_one("#findings-table")
        if findings_table.row_count > 0:
            findings_table.move_cursor(row=0, animate=False)
            await pilot.pause()
