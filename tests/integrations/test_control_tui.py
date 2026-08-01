"""Textual ownership of the local editor control socket."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from groket.ui.app import TraceEvalApp
from groket.ui.screens.browser import BrowserScreen
from textual.pilot import Pilot


async def _wait_until(
    pilot: Pilot,
    predicate: Callable[[], bool],
    *,
    description: str,
) -> None:
    for _ in range(80):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError(f"timed out waiting for {description}")


def _write_session(traces: Path) -> Path:
    session_dir = traces / "session-tui-control"
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "TUI control"}),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1000,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "open here"},
                    "_meta": {"promptIndex": 11},
                }
            },
        },
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "opened"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(update) + "\n" for update in updates),
        encoding="utf-8",
    )
    return session_dir


async def _rpc_open(socket_path: Path, session_id: str) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "session/open",
        "params": {"session": session_id, "promptIndex": 11},
    }
    writer.write(json.dumps(request).encode("utf-8") + b"\n")
    await writer.drain()
    while True:
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
        if response.get("id") == 1:
            break
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
async def test_tui_owns_control_socket_and_opens_catalog_session(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    session_dir = _write_session(traces)
    socket_path = tmp_path / "groket-control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=socket_path,
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_until(pilot, socket_path.exists, description="control socket")
        response = await _rpc_open(socket_path, session_dir.name)
        assert response["result"] == {"opened": True}
        await _wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, BrowserScreen) and app.screen.selected_prompt_index == 11
            ),
            description="socket-selected prompt",
        )

    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_tui_control_helpers_publish_changes(tmp_path: Path) -> None:
    published: list[tuple[str, Path, int | None]] = []

    class StubServer:
        async def publish_session_selected(self, path: Path, prompt_index: int | None) -> None:
            published.append(("selected", path, prompt_index))

        async def publish_session_changed(self, path: Path) -> None:
            published.append(("session", path, None))

        async def publish_notes_changed(self, path: Path) -> None:
            published.append(("notes", path, None))

    app = TraceEvalApp.__new__(TraceEvalApp)
    app._control_server = StubServer()
    workers: list = []
    app.run_worker = lambda coroutine, **_kwargs: workers.append(coroutine)  # type: ignore[method-assign]
    session = tmp_path / "session-publish"

    app.control_session_selected(session, 9)
    app.control_session_changed(session)
    app.control_notes_changed(session)
    await asyncio.gather(*workers)

    assert published == [
        ("selected", session, 9),
        ("session", session, None),
        ("notes", session, None),
    ]
