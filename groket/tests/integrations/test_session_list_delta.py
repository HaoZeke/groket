"""session/list sinceRevision: unchanged polls transfer no rows."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from groket.integrations import daemon as daemon_mod
from groket.integrations.control_client import ControlClient
from groket.session.catalog import SessionCatalogCache


def _short_sock(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix="groket-delta-")) / name


def _write_sess(root: Path, name: str, title: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


@pytest.mark.asyncio
async def test_session_list_since_revision_unchanged_and_delta(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "alpha", "Alpha")
    for i in range(40):
        _write_sess(traces, f"bulk-{i:03d}", f"Bulk {i}")
    sock = _short_sock("delta.sock")
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
        cache.get(force=True)
        client = ControlClient(sock, client_name="delta-test", timeout=20)
        first = await client.session_list_all()
        assert first["matched"] == 41
        assert first["delta"] is False
        rev = int(first["revision"] or 0)
        assert rev >= 1
        poll = await client.session_list(limit=10_000, since_revision=rev)
        assert poll["unchanged"] is True
        assert poll["sessions"] == []
        assert poll["matched"] == 41
        (one / "summary.json").write_text(
            json.dumps({"info": {"id": "alpha"}, "generated_title": "Alpha live"}),
            encoding="utf-8",
        )
        cache.refresh_rows([one])
        delta = await client.session_list(limit=10_000, since_revision=rev)
        assert delta["unchanged"] is False
        assert delta["delta"] is True
        assert [r["sessionId"] for r in delta["sessions"]] == ["alpha"]
        assert delta["sessions"][0]["title"] == "Alpha live"
        omit = await client.session_list(limit=5)
        assert omit.get("delta") is False
        assert len(omit["sessions"]) == 5
        assert omit["matched"] == 41
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_session_list_since_after_owner_restart_is_full_snapshot(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "alpha", "Alpha")
    _write_sess(traces, "beta", "Beta")
    sock1 = _short_sock("old.sock")
    server1 = daemon_mod.build_domain_control_server(
        socket_path=sock1,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    cache1 = getattr(server1, "_catalog_cache", None)
    assert isinstance(cache1, SessionCatalogCache)
    await server1.start()
    try:
        cache1.get(force=True)
        client1 = ControlClient(sock1, client_name="before", timeout=20)
        first = await client1.session_list_all()
        old_since = int(first["revision"] or 0)
    finally:
        await server1.close()
    _write_sess(traces, "gamma", "Gamma")
    sock2 = _short_sock("new.sock")
    server2 = daemon_mod.build_domain_control_server(
        socket_path=sock2,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    cache2 = getattr(server2, "_catalog_cache", None)
    assert isinstance(cache2, SessionCatalogCache)
    await server2.start()
    try:
        cache2.get(force=True)
        client2 = ControlClient(sock2, client_name="after", timeout=20)
        listed = await client2.session_list(limit=10_000, since_revision=old_since)
        ids = {str(r["sessionId"]) for r in listed["sessions"]}
        assert listed["unchanged"] is False
        assert listed["delta"] is False
        assert ids == {"alpha", "beta", "gamma"}
        assert listed["matched"] == 3
    finally:
        await server2.close()
