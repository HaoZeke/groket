"""Shared tool display transforms: prefixes, web_search flatten, image path."""

from __future__ import annotations

import json
from pathlib import Path

from groket.parser import parse_timeline, parse_tool_calls
from groket.session.control_views import timeline_event_mapping
from groket.tool_display import (
    display_tool_output,
    image_result_path,
    preserve_primary_raw_input,
    strip_inline_line_prefixes,
    tool_input_fields,
    web_search_from_raw_output,
)


def test_strip_inline_line_prefixes_removes_grok_arrows() -> None:
    raw = "1→from pathlib import Path\n10→x = 1\n"
    out = strip_inline_line_prefixes(raw)
    assert "→" not in out
    assert out.startswith("from pathlib")
    assert "x = 1" in out
    assert strip_inline_line_prefixes("plain file") == "plain file"


def test_display_tool_output_strips_read_file_for_highlight() -> None:
    raw = "1→def foo():\n2→    return 1\n"
    out = display_tool_output(raw, tool_name="read_file")
    assert "→" not in out
    assert "def foo():" in out
    assert "return 1" in out


def test_web_search_from_raw_output_query_and_urls() -> None:
    body, query, url = web_search_from_raw_output(
        {
            "action": {
                "type": "search",
                "query": 'Phil Karlton "only two hard things"',
                "sources": [
                    {"type": "url", "url": "https://martinfowler.com/bliki/TwoHardThings.html"},
                    {"type": "url", "url": "https://example.com/naming", "title": "Naming"},
                ],
            },
            "id": "ws_1",
            "status": "completed",
        }
    )
    assert query == 'Phil Karlton "only two hard things"'
    assert url == ""
    assert "martinfowler.com/bliki/TwoHardThings.html" in body
    assert "https://example.com/naming" in body
    assert query in body


def test_web_search_open_page_uses_url() -> None:
    body, query, url = web_search_from_raw_output(
        {
            "action": {"type": "open_page", "url": "https://x.ai/"},
            "id": "ws_open",
            "status": "completed",
        }
    )
    assert query == ""
    assert url == "https://x.ai/"
    assert "https://x.ai/" in body


def test_image_result_path_from_json_body() -> None:
    body = json.dumps(
        {
            "path": "/tmp/sess/images/1.jpg",
            "filename": "1.jpg",
            "message": "Image generated and saved to /tmp/sess/images/1.jpg.",
        }
    )
    assert image_result_path(body) == "/tmp/sess/images/1.jpg"


def test_tool_input_fields_not_one_json_bag() -> None:
    fields = tool_input_fields(
        "search_replace",
        {
            "file_path": "a.py",
            "old_string": "aaa" * 20,
            "new_string": "bbb" * 20,
        },
    )
    ids = [str(f["id"]) for f in fields]
    assert ids == ["file_path", "old_string", "new_string"]
    assert "aaa" in str(fields[1]["value"])
    cmd = tool_input_fields("run_terminal_command", {"command": "git status", "timeout": 30})
    assert cmd[0]["id"] == "command"
    assert cmd[0]["value"] == "git status"
    ws = tool_input_fields(
        "web_search",
        {"variant": "WebSearch", "backend": True, "query": "hello"},
    )
    assert [str(f["id"]) for f in ws] == ["query"]
    assert ws[0]["value"] == "hello"


def test_preserve_primary_raw_input_keeps_old_new() -> None:
    raw = {
        "old_string": "keep-old",
        "new_string": "keep-new",
        "blob": "x" * 80_000,
    }
    kept = preserve_primary_raw_input(raw, max_chars=200)
    assert kept["old_string"] == "keep-old"
    assert kept["new_string"] == "keep-new"


def _write_web_search_session(root: Path) -> Path:
    sd = root / "ws-sess"
    sd.mkdir()
    (sd / "summary.json").write_text('{"generated_title":"ws"}', encoding="utf-8")
    call = {
        "timestamp": 1000,
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "ws_1",
                "title": "Web search:",
                "rawInput": {"variant": "WebSearch", "backend": True},
            }
        },
    }
    update = {
        "timestamp": 1001,
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "ws_1",
                "title": "Web search:",
                "status": "completed",
                "content": None,
                "rawOutput": {
                    "action": {
                        "type": "search",
                        "query": "Kernighan Pike naming",
                        "sources": [
                            {"type": "url", "url": "https://example.com/pike"},
                        ],
                    },
                    "id": "ws_1",
                    "status": "completed",
                },
            }
        },
    }
    (sd / "updates.jsonl").write_text(
        json.dumps(call) + "\n" + json.dumps(update) + "\n",
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_parse_timeline_flattens_web_search_action(tmp_path: Path) -> None:
    sd = _write_web_search_session(tmp_path)
    events = parse_timeline(sd)
    result = next(e for e in events if e.event_type == "tool_call_update")
    assert "Kernighan Pike naming" in (result.content or "")
    assert "https://example.com/pike" in (result.content or "")
    call = next(e for e in events if e.event_type == "tool_call")
    assert call.raw_input.as_str("query") == "Kernighan Pike naming"
    calls = parse_tool_calls(sd)
    assert calls[0].raw_input.as_str("query") == "Kernighan Pike naming"
    assert "example.com/pike" in (calls[0].result_content or "")


def test_parse_timeline_open_page_url(tmp_path: Path) -> None:
    sd = tmp_path / "open-page"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "ws_p",
                        "title": "Web search:",
                        "rawInput": {"variant": "WebSearch", "backend": True},
                    }
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": 2,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "ws_p",
                        "title": "Web search:",
                        "status": "completed",
                        "content": None,
                        "rawOutput": {
                            "action": {"type": "open_page", "url": "https://x.ai/"},
                            "status": "completed",
                        },
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events = parse_timeline(sd)
    call = next(e for e in events if e.event_type == "tool_call")
    result = next(e for e in events if e.event_type == "tool_call_update")
    assert call.raw_input.as_str("url") == "https://x.ai/"
    assert "https://x.ai/" in (result.content or "")


def test_timeline_mapping_strips_prefixes_and_exposes_fields(tmp_path: Path) -> None:
    from groket.models import ToolInputBag, TraceEvent

    ev = TraceEvent(
        index=3,
        event_type="tool_call_update",
        tool_name="read_file",
        content="1→from pathlib import Path\n2→import os\n",
        raw_input=ToolInputBag({"target_file": "/tmp/x.py", "limit": 40}),
        tool_call_id="c1",
    )
    row = timeline_event_mapping(ev, content_chars=4000)
    assert "→" not in str(row["content"])
    assert "from pathlib import Path" in str(row["content"])
    fields = row["toolFields"]
    assert isinstance(fields, list)
    assert fields
    assert fields[0]["id"] == "target_file"
    assert "/tmp/x.py" in str(fields[0]["value"])


def test_timeline_mapping_image_path(tmp_path: Path) -> None:
    from groket.models import TraceEvent

    ev = TraceEvent(
        index=1,
        event_type="tool_call_update",
        tool_name="image_gen",
        content=json.dumps(
            {
                "path": "/tmp/img.jpg",
                "filename": "img.jpg",
                "message": "Image generated and saved to /tmp/img.jpg.",
            }
        ),
    )
    row = timeline_event_mapping(ev)
    assert row["imagePath"] == "/tmp/img.jpg"
