"""Browser live-refresh watch root and inflight contracts."""

from __future__ import annotations

from pathlib import Path

from groket.session_inflight import KIND_REFRESH, clear, try_begin
from groket.ui.screens.browser import BrowserScreen


def setup_function() -> None:
    clear(KIND_REFRESH)


def teardown_function() -> None:
    clear(KIND_REFRESH)


def test_live_watch_root_is_session_dir(tmp_path: Path) -> None:
    """Watch only the session dir (not the whole traces volume)."""
    vol = tmp_path / "traces" / "ctr"
    vol.mkdir(parents=True)
    (vol / ".groket-turn").mkdir()
    sess = vol / "%2Fworkspace" / "019f-sess"
    sess.mkdir(parents=True)
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sess
    assert screen._live_watch_root() == sess.resolve()


def test_live_watch_root_orphan_session(tmp_path: Path) -> None:
    """Orphan session dirs still watch themselves."""
    sess = tmp_path / "orphan-session"
    sess.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sess
    assert screen._live_watch_root() == sess.resolve()


def test_live_refresh_worker_done_clears_busy_and_runs_pending(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._live_refresh_pending = True
    screen._last_light_submit_at = 0.0
    screen._live_refresh_deferred = None
    from groket.session_inflight import request_rerun

    request_rerun(KIND_REFRESH, sd)
    calls: list[object] = []
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: calls.append(("tick", bool(kwargs.get("heartbeat"))))
    )
    screen._light_refresh_heartbeat = True
    screen._live_refresh_worker_done()
    assert screen._live_refresh_busy is False
    assert screen._live_refresh_pending is False
    assert screen._light_refresh_heartbeat is False
    assert calls == [("tick", True)]


def test_live_refresh_from_fs_sets_pending_when_busy(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._last_light_submit_at = 0.0
    screen._live_refresh_deferred = None
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_from_fs()
    assert screen._live_refresh_pending is True


def test_live_watch_root_uuid_session_layout(tmp_path: Path) -> None:
    """Typical eval layout still watches the session dir, not the volume root."""
    vol = tmp_path / "traces" / "ctr"
    sess = vol / "%2Fworkspace" / "019fabc-session-id"
    sess.mkdir(parents=True)
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sess
    assert screen._live_watch_root() == sess.resolve()


def test_schedule_live_refresh_arms_heartbeat(tmp_path: Path) -> None:
    from groket.constants import LIVE_BROWSER_SNAPSHOT_INTERVAL, LIVE_POLL_HEARTBEAT_INTERVAL
    from groket.session.context_samples import ContextSampleStore

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._live_refresh_timer = None
    screen._live_heartbeat_timer = None
    screen._live_recheck_timer = None
    screen._live_refresh_deferred = None
    screen._trace_watch = None
    screen._context_samples = ContextSampleStore()
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    screen._refresh_session_pending_bar = lambda: None  # type: ignore[method-assign]
    timers: list[float] = []

    class _T:
        def stop(self) -> None:
            return None

    def _set_interval(interval, callback):
        timers.append(float(interval))
        return _T()

    screen.set_interval = _set_interval  # type: ignore[method-assign]
    screen._schedule_live_refresh()
    assert LIVE_POLL_HEARTBEAT_INTERVAL in timers
    assert LIVE_BROWSER_SNAPSHOT_INTERVAL in timers
    assert screen._live_heartbeat_timer is not None
    assert screen._live_refresh_timer is not None
    assert screen._live_recheck_timer is None


def test_schedule_live_refresh_idle_keeps_slow_recheck(tmp_path: Path) -> None:
    """Imported/idle sessions must re-arm live without a full F5."""
    from groket.constants import LIVE_POLL_WATCH_FALLBACK_INTERVAL

    sd = tmp_path / "imported-sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._live_refresh_timer = None
    screen._live_heartbeat_timer = None
    screen._live_recheck_timer = None
    screen._live_refresh_deferred = None
    screen._trace_watch = None
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: False  # type: ignore[method-assign]
    screen._refresh_session_pending_bar = lambda: None  # type: ignore[method-assign]
    timers: list[tuple[float, object]] = []

    class _T:
        def stop(self) -> None:
            return None

    def _set_interval(interval, callback):
        timers.append((float(interval), callback))
        return _T()

    screen.set_interval = _set_interval  # type: ignore[method-assign]
    screen._schedule_live_refresh()
    assert screen._live_refresh_timer is None
    assert screen._live_heartbeat_timer is None
    assert screen._live_recheck_timer is not None
    assert any(iv == LIVE_POLL_WATCH_FALLBACK_INTERVAL for iv, _ in timers)


def test_live_recheck_tick_rearms_hot_live(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen._live_recheck_timer = object()  # type: ignore[assignment]
    screen._live_refresh_timer = None
    screen._live_heartbeat_timer = None
    screen._trace_watch = None
    screen._live_refresh_deferred = None
    scheduled: list[str] = []
    pulled: list[bool] = []

    class _T:
        def stop(self) -> None:
            return None

    screen.set_interval = lambda interval, callback: _T()  # type: ignore[method-assign]
    screen._invalidate_live_timeline_cache = lambda: None  # type: ignore[method-assign]
    screen._invalidate_pending_cache = lambda: None  # type: ignore[method-assign]
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    screen._schedule_live_refresh = (  # type: ignore[method-assign]
        lambda: scheduled.append("hot")
    )
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: pulled.append(bool(kwargs.get("heartbeat")))
    )
    screen._live_recheck_tick()
    assert scheduled == ["hot"]
    assert pulled == [False]
    assert screen._live_recheck_timer is None


def test_live_watch_root_resolves_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real-sess"
    real.mkdir()
    link = tmp_path / "imported" / "link-sess"
    link.parent.mkdir(parents=True)
    link.symlink_to(real, target_is_directory=True)
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = link
    assert screen._live_watch_root() == real.resolve()


def test_live_refresh_heartbeat_passes_flag(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    flags: list[bool] = []
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: flags.append(bool(kwargs.get("heartbeat")))
    )
    screen._live_refresh_heartbeat()
    assert flags == [True]


def test_record_context_sample_and_turn_index(tmp_path: Path) -> None:
    from groket.models import SessionMeta, TraceEvent
    from groket.session.context_samples import ContextSampleStore

    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        TraceEvent(
            index=0, timestamp=1.0, event_type="turn_started", content="Turn started turn_number=0"
        ),
        TraceEvent(index=1, timestamp=2.0, event_type="user_message_chunk", content="hi"),
    ]
    screen.meta = SessionMeta(
        session_id="s",
        session_dir=sd,
        context_window_usage_pct=35,
        context_tokens_used=178996,
        context_window_tokens=500000,
    )
    screen._context_samples = ContextSampleStore()
    assert screen._current_turn_index() == 0
    assert screen._record_context_sample() is True
    assert screen._record_context_sample() is False
    assert "35%" in screen._context_samples.compact_for_turn(0)


def test_signals_mtime_missing_and_oserror(tmp_path: Path, monkeypatch) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    assert screen._signals_mtime() == 0.0
    sig = sd / "signals.json"
    sig.write_text("{}", encoding="utf-8")
    assert screen._signals_mtime() > 0.0

    class _BoomStat:
        def __init__(self, *a, **k):
            raise OSError("nope")

    monkeypatch.setattr(Path, "stat", _BoomStat)
    assert screen._signals_mtime() == 0.0
