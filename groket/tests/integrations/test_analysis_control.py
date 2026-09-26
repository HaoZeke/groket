"""Control-plane analysis/run and analysis/status."""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib import import_module
from pathlib import Path

import pytest


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="groket-ctl-"))
    return root / name


def _write_session(session_dir: Path) -> None:
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "Analyze me"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Complete session so analysis can cache.
    (session_dir / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "timestamp": 1001}) + "\n",
        encoding="utf-8",
    )


async def _request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()
    while True:
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=8))
        if response.get("id") == request_id:
            return response


@pytest.mark.asyncio
async def test_analysis_run_and_status_on_domain_server(tmp_path: Path) -> None:
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session = traces / "session-analysis"
    _write_session(session)
    sock = _short_sock("analysis.sock")
    server = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        init = await _request(reader, writer, 1, "initialize", {"protocolVersion": 1})
        caps = init["result"]["capabilities"]
        assert "analysis/run" in caps
        assert "analysis/status" in caps

        idle = await _request(reader, writer, 2, "analysis/status", {"session": session.name})
        assert idle["result"]["state"] == "idle"

        started = await _request(
            reader,
            writer,
            3,
            "analysis/run",
            {"session": session.name, "force": True},
        )
        assert started["result"]["state"] == "running"
        assert started["result"]["jobId"]

        # Wait for completion (basic analyzer is quick).
        final: dict | None = None
        for _ in range(80):
            status = await _request(
                reader, writer, 10 + _, "analysis/status", {"session": session.name}
            )
            final = status["result"]
            if final["state"] in {"done", "error"}:
                break
            await asyncio.sleep(0.05)
        assert final is not None
        assert final["state"] == "done"
        assert final["sessionId"] == session.name
        assert isinstance(final["analyzerIds"], list)

        # Second run while idle starts again.
        again = await _request(
            reader,
            writer,
            100,
            "analysis/run",
            {"session": session.name, "force": False},
        )
        assert again["result"]["state"] in {"running", "done"}

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_analysis_run_without_service_returns_501(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    session = tmp_path / "session-no-svc"
    _write_session(session)
    sock = _short_sock("nosvc.sock")
    server = control.ControlServer(
        socket_path=sock,
        resolve_session=lambda ref: session if ref == session.name else None,
        analysis_service=None,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        response = await _request(
            reader,
            writer,
            1,
            "analysis/run",
            {"session": session.name},
        )
        assert response["error"]["code"] == 501
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


def test_local_access_analysis_run_summary(tmp_path: Path) -> None:
    from groket.analysis.service import AnalysisService
    from groket.paths import analysis_cache_dir
    from groket.session.access import LocalSessionAccess

    session = tmp_path / "sess-local"
    _write_session(session)
    svc = AnalysisService(tmp_path, cache_root=analysis_cache_dir())
    access = LocalSessionAccess(
        resolve_session=lambda ref: session if ref in {session.name, str(session)} else None,
    )
    summary = access.analysis_run(session.name, force=True, service=svc)
    assert summary["state"] == "done"
    assert summary["sessionId"] == session.name
    assert summary["okCount"] + summary["errorCount"] >= 1
