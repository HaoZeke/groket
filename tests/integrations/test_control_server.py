"""Unix-socket control protocol for editor clients."""

from __future__ import annotations

import asyncio
import json
import shutil
from importlib import import_module
from pathlib import Path

import pytest


async def _request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
    notifications: list[dict] | None = None,
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
        if notifications is not None and "method" in response:
            notifications.append(response)


async def _header_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
) -> dict:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    writer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
    await writer.drain()
    header = await asyncio.wait_for(reader.readline(), timeout=2)
    assert header.startswith(b"Content-Length: ")
    length = int(header.split(b":", 1)[1])
    assert await reader.readline() == b"\r\n"
    return json.loads(await reader.readexactly(length))


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
            notifications := [],
        )
        writer.close()
        await writer.wait_closed()
        assert opened_response["result"] == {"opened": True}
        assert opened == [(session_dir, 6)]
        assert notifications == [
            {
                "jsonrpc": "2.0",
                "method": "session/selected",
                "params": {"sessionId": session_dir.name, "promptIndex": 6},
            }
        ]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_supports_emacs_jsonrpc_framing(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    server = control.ControlServer(socket_path=tmp_path / "emacs.sock")
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        initialized = await _header_request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": 1, "clientInfo": {"name": "Emacs"}},
        )
        assert initialized["result"]["protocolVersion"] == 1
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_does_not_chmod_existing_socket_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = import_module("groket.integrations.control")
    socket_path = tmp_path / "existing-parent.sock"
    original_chmod = Path.chmod

    def reject_parent_chmod(path: Path, mode: int, **kwargs: object) -> None:
        if path == tmp_path:
            raise PermissionError("socket parent is not owned by this process")
        original_chmod(path, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", reject_parent_chmod)
    server = control.ControlServer(socket_path=socket_path)
    await server.start()
    try:
        assert socket_path.is_socket()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_publishes_tui_changes(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    session_dir = tmp_path / "session-tui-change"
    _write_session(session_dir)
    server = control.ControlServer(socket_path=tmp_path / "changes.sock")
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": 1, "clientInfo": {"name": "test-editor"}},
        )
        await server.publish_session_changed(session_dir)
        session_message = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert session_message["method"] == "session/changed"
        assert session_message["params"] == {"sessionId": session_dir.name}

        await server.publish_notes_changed(session_dir)
        notes_message = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert notes_message["method"] == "notes/changed"
        assert notes_message["params"]["sessionId"] == session_dir.name
        assert len(notes_message["params"]["revision"]) == 64
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_stock_emacs_opens_live_org_session(tmp_path: Path) -> None:
    emacs = shutil.which("emacs")
    if emacs is None:
        pytest.skip("Emacs is not installed")
    control = import_module("groket.integrations.control")
    session_dir = tmp_path / "session-emacs-live"
    _write_session(session_dir)
    opened: list[tuple[Path, int | None]] = []

    async def open_session(path: Path, prompt_index: int | None) -> bool:
        opened.append((path, prompt_index))
        return True

    server = control.ControlServer(
        socket_path=tmp_path / "emacs-live.sock",
        open_session=open_session,
    )
    await server.start()
    package_dir = Path(__file__).parents[2] / "groket" / "integrations" / "emacs"
    expression = f"""
(progn
  (setq groket-control-socket {json.dumps(str(server.socket_path))})
  (require 'groket)
  (let ((buffer (groket-open-session {json.dumps(str(session_dir))} 6)))
    (with-current-buffer buffer
      (princ (format "%s|%s|%s"
                     groket-session-id
                     groket-notes-revision
                     (derived-mode-p 'groket-session-mode))))))
"""
    try:
        process = await asyncio.create_subprocess_exec(
            emacs,
            "--batch",
            "--quick",
            "--eval",
            "(setq load-prefer-newer t)",
            "-L",
            str(package_dir),
            "--eval",
            expression,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        session_id, revision, mode = stdout.decode().strip().split("|")
        assert session_id == session_dir.name
        assert len(revision) == 64
        assert mode == "groket-session-mode"
        assert opened == [(session_dir, 6)]
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
        notifications: list[dict] = []
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
            notifications,
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
        assert notifications[0]["method"] == "notes/changed"
        assert notifications[0]["params"] == {
            "sessionId": session_dir.name,
            "revision": saved_revision,
        }
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
