"""Control RPC: session/get, timeline, turns, usage."""

from __future__ import annotations

import json
import tempfile
from importlib import import_module
from pathlib import Path

import pytest


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="groket-rich-"))
    return root / name


def _write_session(tmp_path: Path) -> Path:
    session_dir = tmp_path / "session-rich"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "Rich"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 50,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                        "_meta": {"promptIndex": 4},
                    }
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": 51,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "yo"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return session_dir


@pytest.mark.asyncio
async def test_control_session_get_timeline_turns_usage(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")
    session_dir = _write_session(tmp_path)
    sock = _short_sock("rich.sock")
    server = control.ControlServer(
        socket_path=sock,
        resolve_session=lambda ref: (
            session_dir if ref in {session_dir.name, str(session_dir)} else None
        ),
    )
    await server.start()
    try:
        client = client_mod.ControlClient(sock, client_name="rich-test")
        init = await client.initialize()
        caps = init["capabilities"]
        assert "session/get" in caps
        assert "session/overview" in caps
        assert "session/timeline" in caps
        assert "session/turns" in caps
        assert "session/usage" in caps

        got = await client.session_get(session_dir.name)
        assert got["sessionId"] == session_dir.name
        assert got["title"] == "Rich"
        assert "status" in got
        assert "contextUsage" in got or "contextUsageCompact" in got

        ov = await client.session_overview(session_dir.name)
        assert ov["sessionId"] == session_dir.name
        assert ov["meta"]["title"] == "Rich"
        assert ov["turns"]["total"] >= 1
        assert ov["timeline"]["total"] >= 1
        assert ov["timeline"]["events"] == []
        assert ov["timeline"].get("lazy") is True
        assert "summary" in ov["turns"]["turns"][0]

        tl = await client.session_timeline(session_dir.name, limit=50)
        assert tl["total"] >= 1
        assert tl["events"]
        assert tl["events"][0]["type"]

        turns = await client.session_turns(session_dir.name)
        assert turns["total"] >= 1
        assert turns["turns"]
        assert "summary" in turns["turns"][0]

        usage = await client.session_usage(session_dir.name)
        assert usage["sessionId"] == session_dir.name
        assert "hostTools" in usage
    finally:
        await server.close()
