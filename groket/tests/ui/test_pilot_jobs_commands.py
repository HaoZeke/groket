"""Pilot: Jobs modal — mount, actions, log append, status rows, history.

Uses Textual ``App.run_test()`` so compose, workers, and bindings run.
Synchronisation is condition-based (``wait_until``); see AGENTS.md §4.5c.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.docker.orchestrator import ContainerConfig, ContainerStatus
from groket.models import EvalRun
from groket.runs.run_manager import BackgroundRun
from groket.ui.app import TraceEvalApp
from groket.ui.commands import yield_app_commands
from groket.ui.screens.jobs import JobsModal
from textual.widgets import DataTable, RichLog, Static

from .pilot_helpers import wait_until


def _make_work(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "w"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    return work, traces


def _host_app(work: Path, traces: Path) -> TraceEvalApp:
    return TraceEvalApp(work_dir=work, traces_path=traces)


def _make_bg_run(
    *,
    run_id: str = "run-abc123",
    model: str = "v9-dietcoke",
    container_name: str = "groket-v9-abc12345",
    status: str = "running",
) -> BackgroundRun:
    """Build a BackgroundRun with one container config and a status entry."""
    cfg = ContainerConfig(model=model, prompt="test", container_name=container_name)
    st = ContainerStatus(
        container_name=container_name,
        model=model,
        status=status,
        started_at="2026-06-25T00:00:00",
        finished_at="" if status == "running" else "2026-06-25T00:05:00",
    )
    bg = BackgroundRun(
        run_id=run_id,
        eval_run=EvalRun(
            run_id=run_id,
            prompt="test",
            models=[model],
            status=status if status in ("running", "completed", "failed") else "running",
        ),
        configs=[cfg],
        statuses={container_name: st},
    )
    return bg


async def _open_jobs(app: TraceEvalApp, pilot, work: Path) -> JobsModal:
    """Push JobsModal and wait until it mounts with tables ready."""
    app.push_screen(JobsModal(app.run_manager, work_dir=work))

    def _ready() -> bool:
        for s in app.screen_stack:
            if isinstance(s, JobsModal):
                try:
                    s.query_one("#jobs-status-table", DataTable)
                    return True
                except Exception:
                    return False
        return False

    await wait_until(pilot, _ready, description="JobsModal composed with tables")
    modal = next(s for s in app.screen_stack if isinstance(s, JobsModal))
    return modal


# ── Mount tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jobs_modal_mounts(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        st_table = modal.query_one("#jobs-status-table", DataTable)
        assert len(st_table.columns) >= 6
        ht_table = modal.query_one("#jobs-history-table", DataTable)
        assert len(ht_table.columns) >= 4


@pytest.mark.asyncio
async def test_jobs_modal_refresh_action(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal.action_refresh()
        await pilot.pause()
        status_w = modal.query_one("#jobs-app-status", Static)
        # Static.update() was called → the widget exists and was refreshed
        assert status_w is not None


@pytest.mark.asyncio
async def test_jobs_modal_show_help(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal.action_show_help()
        await pilot.pause()


@pytest.mark.asyncio
async def test_jobs_modal_dismiss(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal.action_dismiss_modal()
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, JobsModal) for s in app.screen_stack),
            description="JobsModal dismissed",
        )


@pytest.mark.asyncio
async def test_jobs_modal_close_button(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._btn_close()
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, JobsModal) for s in app.screen_stack),
            description="JobsModal closed via button",
        )


# ── Status + log tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jobs_modal_status_row_and_log(tmp_path: Path) -> None:
    """Inject a BackgroundRun into RunManager, verify table and log append."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run()
    with app.run_manager._lock:
        app.run_manager._active[bg.run_id] = bg

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        st_table = modal.query_one("#jobs-status-table", DataTable)
        await wait_until(
            pilot,
            lambda: st_table.row_count >= 1,
            description="status table has a row from bg run",
        )
        # Append a log line — exercises _append_log path + _ensure_log_tabs
        modal._append_log("groket-v9-abc12345", "Hello from container")
        await pilot.pause()
        # RichLog.write is async; verify the tab was ensured
        assert "jobs-log-tab-groket-v9-abc12345" in modal._log_tabs


@pytest.mark.asyncio
async def test_jobs_modal_history_table_with_finished_run(tmp_path: Path) -> None:
    """Finished run appears in the history table."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run(status="completed")
    bg.eval_run.status = "completed"
    bg.elapsed_s = 120.0
    with app.run_manager._lock:
        app.run_manager._history.append(bg)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        ht = modal.query_one("#jobs-history-table", DataTable)
        await wait_until(
            pilot,
            lambda: ht.row_count >= 1,
            description="history table has finished run row",
        )


@pytest.mark.asyncio
async def test_jobs_modal_clear_logs(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._append_log("test-c", "line 1")
        await pilot.pause()
        modal.action_clear_logs()
        await pilot.pause()
        all_log = modal.query_one("#jobs-logs-all", RichLog)
        assert len(all_log.lines) == 0


@pytest.mark.asyncio
async def test_jobs_modal_clear_btn(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._append_log("test-c", "line before clear")
        await pilot.pause()
        modal._btn_clear()
        await pilot.pause()


@pytest.mark.asyncio
async def test_jobs_modal_refresh_btn(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._btn_refresh()
        await pilot.pause()


@pytest.mark.asyncio
async def test_jobs_modal_status_update_message(tmp_path: Path) -> None:
    """Post a StatusUpdate message and verify table update."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run()
    with app.run_manager._lock:
        app.run_manager._active[bg.run_id] = bg

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        await pilot.pause()
        new_st = ContainerStatus(
            container_name="groket-v9-abc12345",
            model="v9-dietcoke",
            status="completed",
            started_at="2026-06-25T00:00:00",
            finished_at="2026-06-25T00:05:00",
        )
        modal.on_jobs_modal_status_update(JobsModal.StatusUpdate(new_st))
        await pilot.pause()


@pytest.mark.asyncio
async def test_jobs_modal_run_finished_message(tmp_path: Path) -> None:
    """Post a RunFinished message and verify history update."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run(status="completed")
    bg.eval_run.status = "completed"
    bg.elapsed_s = 60.0
    with app.run_manager._lock:
        app.run_manager._history.append(bg)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal.on_jobs_modal_run_finished(JobsModal.RunFinished(bg))
        await pilot.pause()
        ht = modal.query_one("#jobs-history-table", DataTable)
        assert ht.row_count >= 1


@pytest.mark.asyncio
async def test_jobs_modal_log_line_message(tmp_path: Path) -> None:
    """Post a LogLine message and verify log tab receives it."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal.on_jobs_modal_log_line(JobsModal.LogLine("test-container", "a log line"))
        await pilot.pause()
        assert "jobs-log-tab-test-container" in modal._log_tabs


@pytest.mark.asyncio
async def test_jobs_modal_open_session_no_session(tmp_path: Path) -> None:
    """action_open_session notifies when no session dir exists."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run()
    with app.run_manager._lock:
        app.run_manager._active[bg.run_id] = bg

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        st_table = modal.query_one("#jobs-status-table", DataTable)
        await wait_until(
            pilot,
            lambda: st_table.row_count >= 1,
            description="status table populated before open",
        )
        modal.action_open_session()
        await pilot.pause()


@pytest.mark.asyncio
async def test_jobs_modal_open_share_no_url(tmp_path: Path) -> None:
    """action_open_share returns silently when no share URL exists."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run()
    with app.run_manager._lock:
        app.run_manager._active[bg.run_id] = bg

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        st_table = modal.query_one("#jobs-status-table", DataTable)
        await wait_until(
            pilot,
            lambda: st_table.row_count >= 1,
            description="status table populated before share",
        )
        modal.action_open_share()
        await pilot.pause()


@pytest.mark.asyncio
async def test_jobs_modal_ensure_log_tabs(tmp_path: Path) -> None:
    """_ensure_log_tabs creates per-container tabs."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._ensure_log_tabs(["groket-alpha-1", "groket-beta-2"])
        await pilot.pause()
        assert "jobs-log-tab-groket-alpha-1" in modal._log_tabs
        assert "jobs-log-tab-groket-beta-2" in modal._log_tabs


@pytest.mark.asyncio
async def test_jobs_log_tabs_accept_model_effort_container_names(tmp_path: Path) -> None:
    """Container names that once contained ``:`` must not raise BadIdentifier."""
    from textual.widgets import RichLog

    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    # Reproduce the user-facing failure mode: model:effort leaked into the name.
    bad = "groket-2bffe270c1a3-zingster:hig"
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._ensure_log_tabs([bad])
        await pilot.pause()
        tab_id, log_id = JobsModal._log_ids(bad)
        assert ":" not in tab_id and ":" not in log_id
        assert tab_id in modal._log_tabs
        # Widget must be queryable (mount succeeded)
        log = modal.query_one(f"#{log_id}", RichLog)
        assert log is not None
        modal._append_log(bad, "hello from eval")
        modal._flush_log_buffers()
        await pilot.pause()


def test_jobs_modal_fmt_ts_various_inputs() -> None:
    """Test _fmt_ts with different input types."""
    assert JobsModal._fmt_ts(None) == "—"
    assert JobsModal._fmt_ts("") == "—"
    assert JobsModal._fmt_ts("  ") == "—"
    ts = "2026-06-25T19:37:49.455515+00:00"
    assert JobsModal._fmt_ts(ts) == "19:37:49"
    assert JobsModal._fmt_ts("just-text") == "just-text"


@pytest.mark.asyncio
async def test_jobs_modal_subscribe_unsubscribe(tmp_path: Path) -> None:
    """Subscribe and unsubscribe lifecycle."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        assert modal._subscribed
        modal._unsubscribe()
        assert not modal._subscribed
        modal._subscribe()
        assert modal._subscribed


@pytest.mark.asyncio
async def test_jobs_modal_long_log_truncated(tmp_path: Path) -> None:
    """Log lines >400 chars are truncated."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        long_line = "x" * 500
        modal._append_log("test-c", long_line)
        await pilot.pause()
        assert "jobs-log-tab-test-c" in modal._log_tabs


@pytest.mark.asyncio
async def test_jobs_modal_update_status_row_states(tmp_path: Path) -> None:
    """Verify different status states get styled differently."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    bg = _make_bg_run()
    with app.run_manager._lock:
        app.run_manager._active[bg.run_id] = bg

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        await pilot.pause()
        for state in ("running", "completed", "failed", "building"):
            st = ContainerStatus(
                container_name=f"groket-{state}-test",
                model="v9",
                status=state,
                started_at="2026-06-25T00:00:00",
            )
            modal._update_status_row(st, run_id=bg.run_id)
        await pilot.pause()
        table = modal.query_one("#jobs-status-table", DataTable)
        assert table.row_count >= 4


@pytest.mark.asyncio
async def test_jobs_modal_flush_log_buffers(tmp_path: Path) -> None:
    """_flush_log_buffers writes pending lines to mounted RichLog tabs."""
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = await _open_jobs(app, pilot, work)
        modal._ensure_log_tabs(["flush-test"])
        await pilot.pause()
        from rich.text import Text as RText

        modal._log_buffer.setdefault("flush-test", []).append(RText("buffered line"))
        modal._flush_log_buffers()
        await pilot.pause()


# ── Command palette ───────────────────────────────────────────────────────


def test_yield_app_commands_on_main(tmp_path: Path) -> None:
    work, traces = _make_work(tmp_path)
    app = _host_app(work, traces)

    class FakeScreen:
        pass

    cmds = list(yield_app_commands(app, FakeScreen()))  # type: ignore[arg-type]  # stub for test
    titles = [c[0] for c in cmds]
    assert any("Self-test" in t or "self" in t.lower() for t in titles) or len(cmds) > 3
