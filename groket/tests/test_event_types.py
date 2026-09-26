"""Grok-aligned timeline event type taxonomy."""

from __future__ import annotations

import json
from pathlib import Path

from groket import event_types as et
from groket.parser import parse_timeline


def test_session_update_types_are_identity_mapped(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        {
            "timestamp": 1,
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "task_backgrounded", "tool_call_id": "c1"}},
        },
        {
            "timestamp": 2,
            "method": "session/update",
            "params": {
                "update": {"sessionUpdate": "task_completed", "task_snapshot": {"ok": True}}
            },
        },
        {
            "timestamp": 3,
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "turn_completed", "prompt_id": "p1"}},
        },
        {
            "timestamp": 4,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hi"},
                }
            },
        },
    ]
    (sd / "updates.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    types = {e.event_type for e in parse_timeline(sd)}
    assert et.TASK_BACKGROUNDED in types
    assert et.TASK_COMPLETED in types
    assert et.TURN_COMPLETED in types
    assert et.USER_MESSAGE_CHUNK in types


def test_type_label_uses_grok_names() -> None:
    assert et.type_label("user_message_chunk") == "user message chunk"
    assert et.type_label("tool_call_update") == "tool call update"
