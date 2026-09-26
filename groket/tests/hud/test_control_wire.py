"""Python control JSON matches the HUD typed decode fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from groket.session.access import filter_session_catalog
from groket.session.catalog import session_catalog_row
from groket.session.control_views import (
    build_session_overview,
    build_session_timeline,
    build_session_turns,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "groket-hud" / "tests" / "fixtures"


def _write_session(root: Path, name: str) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": "View session",
                "model": "grok-test",
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1000,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hello user"},
                    "_meta": {"promptIndex": 2},
                }
            },
        },
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello agent"},
                }
            },
        },
        {
            "timestamp": 1002,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "title": "read_file",
                    "kind": "read",
                    "status": "completed",
                    "rawInput": {"target_file": "/tmp/x"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates),
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


def _assert_shape(live: object, fixture: object, *, path: str = "$") -> None:
    """Same JSON types and object keys; ignore volatile path/epoch values."""
    if isinstance(fixture, dict):
        assert isinstance(live, dict), f"{path}: expected object"
        assert set(fixture) <= set(live), f"{path}: missing keys {set(fixture) - set(live)}"
        skip = {"path", "sortEpoch", "revision"}
        for key, expected in fixture.items():
            if key in skip:
                continue
            _assert_shape(live[key], expected, path=f"{path}.{key}")
        return
    if isinstance(fixture, list):
        assert isinstance(live, list), f"{path}: expected array"
        assert len(live) == len(fixture), f"{path}: length {len(live)} != {len(fixture)}"
        for i, (a, b) in enumerate(zip(live, fixture, strict=True)):
            _assert_shape(a, b, path=f"{path}[{i}]")
        return
    assert live == fixture, f"{path}: {live!r} != {fixture!r}"


def test_control_wire_matches_hud_fixtures(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-wire")
    turns = build_session_turns(sd)
    timeline = build_session_timeline(sd, offset=0, limit=50)
    overview = build_session_overview(sd)
    listed = filter_session_catalog([session_catalog_row(sd, origin="work")], query="", limit=50)

    assert listed["sessions"][0]["sessionId"] == "sess-wire"
    assert listed["sessions"][0]["status"] == "running"
    assert turns["turns"][0]["assistantSummary"] == "hello agent"
    assert turns["turns"][0]["assistantEventIndex"] == 1
    assert timeline["events"][1]["kind"] == "agent"
    assert overview["meta"]["status"] == "running"

    _assert_shape(listed, json.loads((_FIXTURES / "list.json").read_text(encoding="utf-8")))
    _assert_shape(turns, json.loads((_FIXTURES / "turns.json").read_text(encoding="utf-8")))
    _assert_shape(timeline, json.loads((_FIXTURES / "timeline.json").read_text(encoding="utf-8")))
    _assert_shape(overview, json.loads((_FIXTURES / "overview.json").read_text(encoding="utf-8")))
