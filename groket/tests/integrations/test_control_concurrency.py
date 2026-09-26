"""Control owner: heavy I/O off the loop, bounded concurrency, notes fan-out."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
from importlib import import_module
from pathlib import Path

import pytest


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="groket-ctl-conc-"))
    return root / name


def _write_session(session_dir: Path) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "Conc"}),
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


@pytest.mark.asyncio
async def test_heavy_io_semaphore_caps_concurrent_access_threads(tmp_path: Path) -> None:
    """Many concurrent overview RPCs share HEAVY_IO_CONCURRENCY worker slots."""
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")

    sessions: dict[str, Path] = {}
    for i in range(8):
        sd = tmp_path / f"sess-{i}"
        _write_session(sd)
        sessions[sd.name] = sd

    lock = threading.Lock()
    active = 0
    peak = 0

    server = control.ControlServer(
        socket_path=_short_sock("heavy.sock"),
        resolve_session=lambda ref: sessions.get(ref) or sessions.get(Path(ref).name),
    )
    orig = server._access.session_overview

    def slow_overview(session: str, *args: object, **kwargs: object) -> object:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.12)
            return orig(session, *args, **kwargs)
        finally:
            with lock:
                active -= 1

    server._access.session_overview = slow_overview  # type: ignore[method-assign]
    await server.start()
    try:
        clients = [
            client_mod.ControlClient(server.socket_path, client_name=f"c{i}") for i in range(8)
        ]
        for c in clients:
            await c.initialize()
        results = await asyncio.gather(
            *[c.session_overview(f"sess-{i}") for i, c in enumerate(clients)]
        )
        assert len(results) == 8
        assert all(r.get("sessionId") == f"sess-{i}" for i, r in enumerate(results))
        assert peak <= control.HEAVY_IO_CONCURRENCY
        assert peak >= 1
        assert control.HEAVY_IO_CONCURRENCY == 4
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_notes_upsert_notifies_second_client(tmp_path: Path) -> None:
    """Writer client upsert → second client receives notes/changed with new revision."""
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")

    session_dir = tmp_path / "note-fanout"
    _write_session(session_dir)
    server = control.ControlServer(
        socket_path=_short_sock("fanout.sock"),
        resolve_session=lambda ref: (
            session_dir if ref in {session_dir.name, str(session_dir)} else None
        ),
    )
    await server.start()
    try:
        writer = client_mod.ControlClient(server.socket_path, client_name="writer")
        await writer.initialize()

        notify_q: asyncio.Queue[dict] = asyncio.Queue()

        async def on_notify(method: str, params: object) -> None:
            if method == "notes/changed":
                await notify_q.put({"method": method, "params": params})

        listen_task = asyncio.create_task(
            client_mod.listen_control_notifications(
                server.socket_path,
                on_notify,
                client_name="reader-listen",
            )
        )
        await asyncio.sleep(0.05)

        listed = await writer.notes_list(session_dir.name)
        rev = listed["revision"]
        saved = await writer.notes_upsert(
            session_dir.name,
            {
                "id": "n-fanout",
                "turnIndex": 0,
                "fields": {"summary": "From writer", "detail": "visible everywhere"},
                "eventIndices": [],
            },
            expected_revision=rev,
        )
        assert any(n["id"] == "n-fanout" for n in saved["notes"])

        echo = await asyncio.wait_for(notify_q.get(), timeout=3.0)
        assert echo["method"] == "notes/changed"
        params = echo["params"]
        assert isinstance(params, dict)
        assert params.get("sessionId") == session_dir.name
        assert params.get("revision") == saved["revision"]

        again = await writer.notes_list(session_dir.name)
        assert again["revision"] == saved["revision"]
        assert any(n["id"] == "n-fanout" for n in again["notes"])

        listen_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listen_task
    finally:
        await server.close()
