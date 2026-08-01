"""Unix-socket control protocol for editor clients."""

from __future__ import annotations

import asyncio
import json
from importlib import import_module
from pathlib import Path

import pytest


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
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        if response.get("id") == request_id:
            return response


def _write_session(session_dir: Path) -> None:
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "Socket review"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "review"},
                        "_meta": {"promptIndex": 6},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_control_server_initializes_renders_and_opens_session(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    session_dir = tmp_path / "session-control"
    _write_session(session_dir)
    opened: list[tuple[Path, int | None]] = []

    async def open_session(path: Path, prompt_index: int | None) -> bool:
        opened.append((path, prompt_index))
        return True

    server = control.ControlServer(
        socket_path=tmp_path / "control.sock",
        resolve_session=lambda reference: session_dir if reference == session_dir.name else None,
        open_session=open_session,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        initialized = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": 1, "clientInfo": {"name": "test-editor"}},
        )
        assert initialized["result"]["protocolVersion"] == 1
        assert "session/render" in initialized["result"]["capabilities"]

        rendered = await _request(
            reader,
            writer,
            2,
            "session/render",
            {"session": session_dir.name},
        )
        assert rendered["result"]["sessionId"] == session_dir.name
        assert rendered["result"]["promptIndexes"] == [6]
        assert "* Prompt 6" in rendered["result"]["text"]

        opened_response = await _request(
            reader,
            writer,
            3,
            "session/open",
            {"session": session_dir.name, "promptIndex": 6},
        )
        assert opened_response["result"] == {"opened": True}
        assert opened == [(session_dir, 6)]
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_rejects_stale_note_mutation(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    session_dir = tmp_path / "session-notes"
    _write_session(session_dir)
    server = control.ControlServer(
        socket_path=tmp_path / "notes.sock",
        resolve_session=lambda reference: session_dir if reference == session_dir.name else None,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        listed = await _request(
            reader,
            writer,
            1,
            "notes/list",
            {"session": session_dir.name},
        )
        original_revision = listed["result"]["revision"]
        entry = {
            "id": "n-socket",
            "turnIndex": 0,
            "fields": {"summary": "Socket note", "detail": "Inspect the event."},
            "eventIndices": [1],
        }
        saved = await _request(
            reader,
            writer,
            2,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": original_revision,
                "note": entry,
            },
        )
        saved_revision = saved["result"]["revision"]
        assert saved_revision != original_revision
        assert saved["result"]["notes"][0]["id"] == "n-socket"

        stale = await _request(
            reader,
            writer,
            3,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": original_revision,
                "note": {**entry, "fields": {"summary": "stale"}},
            },
        )
        assert stale["error"]["code"] == 409
        assert stale["error"]["data"]["kind"] == "notes_conflict"
        assert stale["error"]["data"]["currentRevision"] == saved_revision

        deleted = await _request(
            reader,
            writer,
            4,
            "notes/delete",
            {
                "session": session_dir.name,
                "expectedRevision": saved_revision,
                "noteId": "n-socket",
            },
        )
        assert deleted["result"]["notes"] == []
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_returns_jsonrpc_errors(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    server = control.ControlServer(socket_path=tmp_path / "errors.sock")
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write(b"not-json\n")
        await writer.drain()
        parse_error = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert parse_error["error"]["code"] == -32700

        unknown = await _request(reader, writer, 2, "missing/method")
        assert unknown["error"]["code"] == -32601
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
