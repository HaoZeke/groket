"""Versioned local control protocol over a private Unix socket."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, JsonValue, as_json_object, json_as_int, json_as_str
from ..notes import (
    NoteEntry,
    NotesConflict,
    NotesSnapshot,
    delete_note,
    load_schema,
    notes_snapshot,
    upsert_note,
)
from .editor import render_editor_document

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
CAPABILITIES = (
    "session/open",
    "session/render",
    "notes/list",
    "notes/upsert",
    "notes/delete",
)

type SessionResolver = Callable[[str], Path | None]
type OpenSession = Callable[[Path, int | None], Awaitable[bool]]
type NotesChanged = Callable[[Path], Awaitable[None]]


@dataclass(frozen=True)
class ControlError(Exception):
    """JSON-RPC error with stable structured data."""

    code: int
    message: str
    data: JsonObject | None = None


def default_socket_path() -> Path:
    """Return the per-user runtime socket path."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    root = Path(runtime) if runtime else Path.home() / ".groket" / "run"
    return root / "groket" / "control.sock" if runtime else root / "control.sock"


def _default_resolve_session(reference: str) -> Path | None:
    path = Path(reference).expanduser()
    return path.resolve() if path.is_dir() else None


def _note_mapping(note: NoteEntry) -> JsonObject:
    return {
        "id": note.id,
        "turnIndex": note.turn_index,
        "fields": dict(note.fields),
        "eventIndices": list(note.event_indices),
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
    }


def _snapshot_mapping(snapshot: NotesSnapshot) -> JsonObject:
    schema = load_schema()
    return {
        "revision": snapshot.revision,
        "schema": {
            "id": schema.schema_id,
            "fields": [
                {
                    "id": field.id,
                    "label": field.label,
                    "choices": list(field.choices),
                    "pick": field.pick,
                }
                for field in schema.fields
            ],
        },
        "notes": [_note_mapping(note) for note in snapshot.doc.sorted_notes()],
    }


def _note_from_params(data: JsonObject) -> NoteEntry:
    note_id = json_as_str(data.get("id")).strip()
    if not note_id:
        raise ControlError(-32602, "note.id is required")
    raw_fields = data.get("fields")
    fields = (
        {str(key): str(value) for key, value in raw_fields.items() if value is not None}
        if isinstance(raw_fields, dict)
        else {}
    )
    raw_indices = data.get("eventIndices")
    event_indices = (
        [json_as_int(value) for value in raw_indices] if isinstance(raw_indices, list) else []
    )
    return NoteEntry(
        id=note_id,
        turn_index=json_as_int(data.get("turnIndex")),
        fields=fields,
        event_indices=event_indices,
        created_at=json_as_str(data.get("createdAt")),
        updated_at=json_as_str(data.get("updatedAt")),
    )


class ControlServer:
    """Async JSON-RPC server for local editor integrations."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        resolve_session: SessionResolver | None = None,
        open_session: OpenSession | None = None,
        notes_changed: NotesChanged | None = None,
    ) -> None:
        self.socket_path = Path(socket_path or default_socket_path()).expanduser()
        self._resolve_session = resolve_session or _default_resolve_session
        self._open_session = open_session
        self._notes_changed = notes_changed
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._writer_framing: dict[asyncio.StreamWriter, str] = {}

    async def start(self) -> None:
        """Bind the configured socket and begin accepting connections."""
        socket_parent = self.socket_path.parent
        try:
            socket_parent.mkdir(parents=True)
        except FileExistsError:
            if not socket_parent.is_dir():
                raise
        else:
            socket_parent.chmod(0o700)
        if self.socket_path.exists():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path), timeout=0.2
                )
                _ = reader
            except (OSError, TimeoutError):
                self.socket_path.unlink(missing_ok=True)
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"control socket is active: {self.socket_path}")
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.socket_path,
            limit=MAX_MESSAGE_BYTES + 1,
        )
        self.socket_path.chmod(0o600)

    async def serve_forever(self) -> None:
        """Serve until cancelled, then release the owned socket."""
        if self._server is None:
            await self.start()
        assert self._server is not None
        try:
            await self._server.serve_forever()
        finally:
            await self.close()

    async def close(self) -> None:
        """Close listeners and connected streams."""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        writers = list(self._writers)
        self._writers.clear()
        self._writer_framing.clear()
        for writer in writers:
            writer.close()
        for writer in writers:
            try:
                await writer.wait_closed()
            except OSError:
                pass
        self.socket_path.unlink(missing_ok=True)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._writers.add(writer)
        try:
            while not reader.at_eof():
                try:
                    first_line = await reader.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    await self._send_error(writer, None, -32600, "message exceeds size limit")
                    break
                if not first_line:
                    break
                if first_line.lower().startswith(b"content-length:"):
                    self._writer_framing[writer] = "headers"
                    try:
                        length = int(first_line.split(b":", 1)[1].strip())
                    except ValueError:
                        await self._send_error(writer, None, -32600, "invalid Content-Length")
                        break
                    while True:
                        header = await reader.readline()
                        if header in (b"\r\n", b"\n", b""):
                            break
                    if length < 0 or length > MAX_MESSAGE_BYTES:
                        await self._send_error(
                            writer,
                            None,
                            -32600,
                            "message exceeds size limit",
                        )
                        break
                    try:
                        message = await reader.readexactly(length)
                    except asyncio.IncompleteReadError:
                        break
                else:
                    self._writer_framing.setdefault(writer, "newline")
                    message = first_line
                if len(message) > MAX_MESSAGE_BYTES:
                    await self._send_error(writer, None, -32600, "message exceeds size limit")
                    break
                await self._handle_line(message, writer)
        finally:
            self._writers.discard(writer)
            self._writer_framing.pop(writer, None)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await self._send_error(writer, None, -32700, "parse error")
            return
        if not isinstance(raw, dict):
            await self._send_error(writer, None, -32600, "invalid request")
            return
        request = as_json_object(raw)
        request_id = request.get("id")
        method = json_as_str(request.get("method"))
        if request.get("jsonrpc") != "2.0" or not method:
            await self._send_error(writer, request_id, -32600, "invalid request")
            return
        params_raw = request.get("params")
        params = as_json_object(params_raw) if isinstance(params_raw, dict) else {}
        try:
            result = await self._dispatch(method, params)
        except NotesConflict as exc:
            await self._send_error(
                writer,
                request_id,
                409,
                "operator notes changed",
                {"kind": "notes_conflict", "currentRevision": exc.current_revision},
            )
            return
        except ControlError as exc:
            await self._send_error(writer, request_id, exc.code, exc.message, exc.data)
            return
        except OSError as exc:
            await self._send_error(writer, request_id, -32603, str(exc))
            return
        if request_id is not None:
            await self._send(writer, {"jsonrpc": "2.0", "id": request_id, "result": result})

    def _session(self, params: JsonObject) -> Path:
        reference = json_as_str(params.get("session")).strip()
        if not reference:
            raise ControlError(-32602, "session is required")
        session = self._resolve_session(reference)
        if session is None or not session.is_dir():
            raise ControlError(404, "session not found", {"session": reference})
        return session

    async def _dispatch(self, method: str, params: JsonObject) -> JsonValue:
        if method == "initialize":
            requested = json_as_int(params.get("protocolVersion"))
            if requested != PROTOCOL_VERSION:
                raise ControlError(
                    -32602,
                    "unsupported protocol version",
                    {"supported": PROTOCOL_VERSION},
                )
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": list(CAPABILITIES),
            }
        if method == "session/render":
            document = await asyncio.to_thread(render_editor_document, self._session(params))
            return {
                "sessionId": document.session_id,
                "notesRevision": document.notes_revision,
                "promptIndexes": list(document.prompt_indexes),
                "contentType": "text/org",
                "text": document.text,
            }
        if method == "session/open":
            if self._open_session is None:
                raise ControlError(501, "session opening is unavailable")
            raw_prompt = params.get("promptIndex")
            prompt_index = None if raw_prompt is None else json_as_int(raw_prompt)
            session = self._session(params)
            opened = await self._open_session(session, prompt_index)
            await self.notify(
                "session/selected",
                {"sessionId": session.name, "promptIndex": prompt_index},
            )
            return {"opened": bool(opened)}
        if method == "notes/list":
            snapshot = await asyncio.to_thread(notes_snapshot, self._session(params))
            return _snapshot_mapping(snapshot)
        if method == "notes/upsert":
            note_raw = params.get("note")
            if not isinstance(note_raw, dict):
                raise ControlError(-32602, "note is required")
            session = self._session(params)
            snapshot = await asyncio.to_thread(
                upsert_note,
                session,
                _note_from_params(as_json_object(note_raw)),
                expected_revision=json_as_str(params.get("expectedRevision")),
            )
            result = _snapshot_mapping(snapshot)
            if self._notes_changed is not None:
                await self._notes_changed(session)
            await self.notify(
                "notes/changed",
                {"sessionId": session.name, "revision": snapshot.revision},
            )
            return result
        if method == "notes/delete":
            note_id = json_as_str(params.get("noteId")).strip()
            if not note_id:
                raise ControlError(-32602, "noteId is required")
            session = self._session(params)
            snapshot = await asyncio.to_thread(
                delete_note,
                session,
                note_id,
                expected_revision=json_as_str(params.get("expectedRevision")),
            )
            result = _snapshot_mapping(snapshot)
            if self._notes_changed is not None:
                await self._notes_changed(session)
            await self.notify(
                "notes/changed",
                {"sessionId": session.name, "revision": snapshot.revision},
            )
            return result
        raise ControlError(-32601, "method not found", {"method": method})

    async def notify(self, method: str, params: JsonObject) -> None:
        """Publish a notification to connected editor clients."""
        message: JsonObject = {"jsonrpc": "2.0", "method": method, "params": params}
        for writer in list(self._writers):
            try:
                await self._send(writer, message)
            except (ConnectionError, OSError):
                self._writers.discard(writer)

    async def publish_session_changed(self, session_dir: Path) -> None:
        """Notify editor clients that a session projection changed."""
        session = Path(session_dir)
        await self.notify("session/changed", {"sessionId": session.name})

    async def publish_session_selected(
        self,
        session_dir: Path,
        prompt_index: int | None,
    ) -> None:
        """Notify editor clients about the TUI's active session and prompt."""
        session = Path(session_dir)
        await self.notify(
            "session/selected",
            {"sessionId": session.name, "promptIndex": prompt_index},
        )

    async def publish_notes_changed(self, session_dir: Path) -> None:
        """Notify editor clients that canonical operator notes changed."""
        session = Path(session_dir)
        snapshot = await asyncio.to_thread(notes_snapshot, session)
        await self.notify(
            "notes/changed",
            {"sessionId": session.name, "revision": snapshot.revision},
        )

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: JsonValue | None,
        code: int,
        message: str,
        data: JsonObject | None = None,
    ) -> None:
        error: JsonObject = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._send(
            writer,
            {"jsonrpc": "2.0", "id": request_id, "error": error},
        )

    async def _send(self, writer: asyncio.StreamWriter, payload: JsonObject) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._writer_framing.get(writer) == "headers":
            header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
            writer.write(header + encoded)
        else:
            writer.write(encoded + b"\n")
        await writer.drain()
