"""Wire timeline hydration for control-attached TUI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.control_views import (
    MAX_CONTENT_CHARS,
    MAX_TIMELINE_LIMIT,
    build_session_overview,
    build_session_timeline,
)
from groket.session.wire_timeline import (
    TIMELINE_RPC_CHARS,
    TIMELINE_RPC_LIMIT,
    fetch_timeline_events,
    session_meta_from_overview,
    trace_event_from_wire,
)


def _write_session(root: Path, name: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": "Wire sess"}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hello wire"},
                    }
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": 1001,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hi back"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return sd


def test_trace_event_from_wire_roundtrip_fields(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w1")
    page = build_session_timeline(sd, offset=0, limit=50, content_chars=500)
    assert page["events"]
    ev = trace_event_from_wire(page["events"][0])
    assert ev.index == page["events"][0]["index"]
    assert "user" in (ev.event_type or "").lower() or page["events"][0]["kind"] == "user"
    assert "hello" in (ev.content or "")


def test_session_meta_from_overview(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w2")
    ov = build_session_overview(sd)
    meta = session_meta_from_overview(ov, fallback_dir=sd)
    assert meta.session_id == "w2"
    assert meta.num_events >= 1
    assert meta.session_dir == sd or meta.session_dir.name == "w2"


@pytest.mark.asyncio
async def test_fetch_timeline_events_pages(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w3")

    class _Local:
        async def session_timeline(self, session: str, **kwargs: object) -> object:
            return build_session_timeline(
                sd,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 1),
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    events = await fetch_timeline_events(_Local(), "w3", page_limit=1)
    assert len(events) >= 2
    assert any("hello" in (e.content or "") for e in events)


def test_timeline_rpc_pages_are_smaller_than_server_max() -> None:
    assert TIMELINE_RPC_LIMIT < MAX_TIMELINE_LIMIT
    assert TIMELINE_RPC_CHARS < MAX_CONTENT_CHARS
    assert TIMELINE_RPC_LIMIT == 200
    assert TIMELINE_RPC_CHARS == 12_000
