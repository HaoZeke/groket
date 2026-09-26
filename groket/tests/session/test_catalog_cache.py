"""SessionCatalogCache: single-flight, force refresh, fingerprint."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from groket.session.catalog import (
    SessionCatalogCache,
    session_meta_from_catalog_row,
)


def _write_sess(root: Path, name: str, title: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_list_for_rpc_cold_returns_without_joining_scan(tmp_path: Path, monkeypatch) -> None:
    """First session/list must not wait for a cold full tree scan."""
    import groket.session.catalog as catalog_mod

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(5):
        _write_sess(traces, f"s{i}", f"S{i}")
    release = threading.Event()
    started = threading.Event()
    real = catalog_mod.list_session_catalog

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("scan still blocked")
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    done: dict[str, object] = {}

    def call() -> None:
        done["out"] = cache.list_for_rpc(limit=50)

    th = threading.Thread(target=call)
    th.start()
    assert started.wait(timeout=2)
    th.join(0.4)
    assert not th.is_alive(), "list_for_rpc joined the in-flight catalog scan"
    out = done["out"]
    assert isinstance(out, dict)
    assert out.get("incomplete") is True or out.get("building") is True
    assert out.get("sessions") == []
    release.set()
    th.join(timeout=5)
    finished = cache.get()
    assert len(finished) == 5
    later = cache.list_for_rpc(limit=50)
    assert later["matched"] == 5
    assert later.get("incomplete") is not True
    assert {str(r["sessionId"]) for r in later["sessions"]} == {f"s{i}" for i in range(5)}


def test_catalog_rebuild_invokes_on_rebuilt(tmp_path: Path) -> None:
    """Owner can notify attach clients when a cold scan finishes."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "one", "One")
    hits: list[int] = []
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache._on_rebuilt = lambda: hits.append(1)
    rows = cache.get(force=True)
    assert len(rows) == 1
    assert hits == [1]


def test_catalog_cache_second_get_is_cached(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(12):
        _write_sess(traces, f"s{i:03d}", f"Title {i}")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=60.0)
    t0 = time.perf_counter()
    a = cache.get(force=True)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    b = cache.get()
    warm = time.perf_counter() - t0
    assert len(a) == 12
    assert len(b) == 12
    assert warm < cold
    assert warm < 0.05


def test_catalog_cache_force_rebuilds(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    assert len(cache.get(force=True)) == 1
    _write_sess(traces, "two", "Two")
    # Within TTL without force may still see fingerprint change (entry count).
    rows = cache.get(force=True)
    assert len(rows) == 2


def test_catalog_cache_single_flight(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(8):
        _write_sess(traces, f"x{i}", f"X{i}")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=60.0)
    results: list[int] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(len(cache.get(force=True)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert results == [8, 8, 8, 8]


def test_apply_fs_catalog_events_patches_dirty_row(tmp_path: Path) -> None:
    """Watch callback patches the dirty session instead of a full catalog scan."""
    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    (one / "events.jsonl").write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0})
        + "\n"
        + json.dumps({"ts": 2, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    sessions, notes = apply_fs_catalog_events(cache, [str(one / "events.jsonl")], [traces])
    assert one.resolve() in [p.resolve() for p in sessions]
    assert notes == []
    by_id = {str(r["sessionId"]): r for r in cache.get()}
    assert by_id["one"]["status"] == "complete"


def test_catalog_cache_refresh_rows_updates_one_status(tmp_path: Path) -> None:
    """FS watch must patch the dirty session instead of rescanning the tree."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    first = cache.get(force=True)
    by_id = {str(r["sessionId"]): r for r in first}
    assert "one" in by_id
    (one / "events.jsonl").write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0})
        + "\n"
        + json.dumps({"ts": 2, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    updated = cache.refresh_rows([one])
    by_id = {str(r["sessionId"]): r for r in updated}
    assert by_id["one"]["status"] == "complete"
    assert by_id["two"]["sessionId"] == "two"
    assert len(updated) == 2
    cached = cache.get()
    assert {str(r["sessionId"]): r["status"] for r in cached}["one"] == "complete"


def test_session_meta_from_catalog_row_status() -> None:
    meta = session_meta_from_catalog_row(
        {
            "sessionId": "abc",
            "path": "/tmp/abc",
            "title": "Hello",
            "model": "grok:high",
            "status": "awaiting",
            "origin": "host",
            "taskId": "task-9",
            "durationSeconds": 42.5,
            "numEvents": 17,
            "contextUsageCompact": "35% 1.2k/128k",
            "contextWindowUsagePct": 35,
            "contextTokensUsed": 1200,
            "contextWindowTokens": 128_000,
            "toolCallCount": 3,
            "errorCount": 1,
        }
    )
    assert meta is not None
    assert meta.session_id == "abc"
    assert meta.list_status_label() == "awaiting"
    assert meta.model_display == "grok:high"
    assert meta.task_id == "task-9"
    assert meta.duration_seconds == 42.5
    assert meta.num_events == 17
    assert meta.tool_call_count == 3
    assert meta.error_count == 1
    assert meta.context_window_usage_pct == 35
    assert meta.context_tokens_used == 1200
    assert meta.context_window_tokens == 128_000
    assert "35" in meta.context_usage_compact
    assert meta.origin == "host"


def test_session_meta_from_catalog_row_host_path_wins(tmp_path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = host / "%2Fproj" / "019fe503-d45c-7320-904e-cfa8836c361c"
    sess.mkdir(parents=True)
    monkeypatch.setattr("groket.session.sources.host_grok_sessions_root", lambda: host)
    meta = session_meta_from_catalog_row(
        {
            "sessionId": "019fe503-d45c-7320-904e-cfa8836c361c",
            "path": str(sess),
            "origin": "work",
        }
    )
    assert meta is not None
    assert meta.origin == "host"
