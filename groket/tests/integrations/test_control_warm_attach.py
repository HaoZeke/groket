"""Headless owner warm cache + attach-only RPC list (shared control contract)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest
from groket.integrations import daemon as daemon_mod
from groket.integrations.control_client import ControlClient
from groket.session.access import filter_session_catalog
from groket.session.catalog import SessionCatalogCache


def _short_sock(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix="groket-warm-")) / name


def _write_sess(root: Path, name: str, title: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": f"ask {title}"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return sd


@pytest.mark.asyncio
async def test_daemon_warm_makes_second_list_cheap(tmp_path: Path) -> None:
    """After force warm, session/list must not redo a cold full scan cost."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    # Enough sessions that cold build is measurable vs cache hit.
    for i in range(40):
        _write_sess(traces, f"warm-{i:03d}", f"Warm Title {i}")
    sock = _short_sock("warm.sock")
    server = daemon_mod.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    cache = getattr(server, "_catalog_cache", None)
    assert isinstance(cache, SessionCatalogCache)
    await server.start()
    try:
        # Explicit warm (same as background warm loop first step).
        t0 = time.perf_counter()
        await asyncio.to_thread(lambda: cache.get(force=True))
        warm_build = time.perf_counter() - t0
        client = ControlClient(sock, client_name="warm-test", timeout=30)
        t0 = time.perf_counter()
        listed = await client.session_list(limit=500)
        first = time.perf_counter() - t0
        t0 = time.perf_counter()
        listed2 = await client.session_list(limit=500)
        second = time.perf_counter() - t0
        assert listed["matched"] == 40
        assert listed2["matched"] == 40
        # Both post-warm lists are cache hits; do not require second < first
        # (scheduling noise can invert microsecond-scale timings).
        cheap_bound = max(0.15, warm_build * 0.5)
        assert first < cheap_bound, f"first={first} warm_build={warm_build}"
        assert second < cheap_bound, f"second={second} warm_build={warm_build}"
        # Cold force build should dominate a cache hit (when build is measurable).
        if warm_build >= 0.02:
            assert warm_build > min(first, second)
        # Wire fields used by HUD + attach TUI home list
        row = listed["sessions"][0]
        for key in (
            "sessionId",
            "path",
            "status",
            "taskId",
            "durationSeconds",
            "numEvents",
            "contextUsageCompact",
            "contextWindowUsagePct",
            "contextTokensUsed",
            "contextWindowTokens",
        ):
            assert key in row, key
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_session_list_query_is_server_substring(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "alpha", "Alpha Rocket")
    _write_sess(traces, "beta", "Beta Plane")
    sock = _short_sock("q.sock")
    server = daemon_mod.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    await server.start()
    try:
        getattr(server, "_catalog_cache").get(force=True)  # type: ignore[union-attr]
        client = ControlClient(sock, client_name="q-test", timeout=20)
        all_rows = await client.session_list(limit=50)
        rocket = await client.session_list(query="rocket", limit=50)
        assert all_rows["total"] == 2
        assert rocket["matched"] == 1
        assert rocket["sessions"][0]["sessionId"] == "alpha"
        # Same semantics as filter_session_catalog pure helper.
        local = filter_session_catalog(list(all_rows["sessions"]), query="rocket", limit=50)
        # Note: all_rows sessions may be capped; rebuild full via cache for parity.
        cache = server._catalog_cache  # type: ignore[attr-defined]
        full = cache.get()
        local_full = filter_session_catalog(full, query="rocket", limit=50)
        assert local_full["matched"] == rocket["matched"]
        assert local_full["sessions"][0]["sessionId"] == rocket["sessions"][0]["sessionId"]
        _ = local
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_attach_client_uses_rpc_only_for_list(tmp_path: Path) -> None:
    """ControlClient.session_list is the attach path; no dual local catalog."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "only", "Only Session")
    sock = _short_sock("attach.sock")
    server = daemon_mod.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    await server.start()
    try:
        getattr(server, "_catalog_cache").get(force=True)  # type: ignore[union-attr]
        client = ControlClient(sock, client_name="attach-tui", timeout=20)
        init = await client.initialize()
        assert init["protocolVersion"] == 1
        assert "session/list" in init["capabilities"]
        listed = await client.session_list()
        assert listed["matched"] >= 1
        assert listed["sessions"][0]["title"] == "Only Session"
        ov = await client.session_overview("only")
        assert ov["turns"]["total"] >= 1
        turn0 = ov["turns"]["turns"][0]
        assert "summary" in turn0
        assert turn0.get("summary", "").startswith("ask ") or turn0.get("summary") == ""
        assert ov["timeline"]["events"] == []
        assert ov["timeline"].get("lazy") is True
        tl = await client.session_timeline("only", offset=0, limit=10)
        assert tl["events"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_warm_loop_refreshes_after_start(tmp_path: Path) -> None:
    """serve_control_forever warm loop force-refreshes catalog while running."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "a", "A")
    sock = _short_sock("loop.sock")
    server = daemon_mod.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    task = asyncio.create_task(
        daemon_mod.serve_control_forever(server, write_pid=False, warm_interval=0.4)
    )
    try:
        for _ in range(50):
            if sock.exists():
                break
            await asyncio.sleep(0.05)
        client = ControlClient(sock, client_name="loop", timeout=15)
        await client.initialize()
        # Allow first warm to finish
        await asyncio.sleep(0.25)
        first = await client.session_list()
        assert first["matched"] == 1
        _write_sess(traces, "b", "B")
        # Wait for a refresh tick (interval 0.4s) + force rebuild
        await asyncio.sleep(0.9)
        second = await client.session_list()
        assert second["matched"] == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await server.close()
