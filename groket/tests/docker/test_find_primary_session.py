"""Primary session picker used by the eval entrypoint for multi-turn resume."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "assets" / "docker" / "groket_find_primary_session.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("groket_find_primary_session", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def _session(path: Path, *, kind: str = "", summary: str = "s") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": path.name, "cwd": "/workspace"},
                "session_summary": summary,
                "session_kind": kind,
                "num_messages": 1,
            }
        ),
        encoding="utf-8",
    )
    (path / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    (path / "events.jsonl").write_text("{}\n", encoding="utf-8")


def test_skips_subagent_sibling_mirror(tmp_path: Path, mod) -> None:
    root = tmp_path / "sessions"
    token = root / "%2Fworkspace"
    parent = token / "019f-parent"
    sub = token / "019f-sub-zzz"
    _session(parent, kind="", summary="main")
    _session(sub, kind="subagent", summary="sub")
    (parent / "subagents" / sub.name).mkdir(parents=True)
    (sub / "chat_history.jsonl").write_text("{}\n" * 50, encoding="utf-8")

    assert mod.resolve_primary_session_id(root) == parent.name
    assert mod.main([str(root)]) == 0
    assert mod.main([str(root), "--check", parent.name]) == 0
    assert mod.main([str(root), "--check", sub.name]) == 1


def test_preferred_sticky_primary(tmp_path: Path, mod) -> None:
    import os
    import time

    root = tmp_path / "sessions"
    token = root / "%2Fworkspace"
    a = token / "sess-a"
    b = token / "sess-b"
    _session(a)
    _session(b)
    (b / "chat_history.jsonl").write_text("{}\n" * 10, encoding="utf-8")
    now = time.time()
    os.utime(a / "chat_history.jsonl", (now - 100, now - 100))
    os.utime(b / "chat_history.jsonl", (now, now))
    # Newest is b, but preferred a stays
    assert mod.resolve_primary_session_id(root, preferred_id=a.name) == a.name
    assert mod.resolve_primary_session_id(root) == b.name


def test_rejects_preferred_subagent(tmp_path: Path, mod) -> None:
    root = tmp_path / "sessions"
    token = root / "%2Fworkspace"
    parent = token / "parent"
    sub = token / "sub"
    _session(parent)
    _session(sub, kind="subagent")
    (parent / "subagents" / sub.name).mkdir(parents=True)
    assert mod.resolve_primary_session_id(root, preferred_id=sub.name) == parent.name


def test_prompt_history_prefers_first_primary(tmp_path: Path, mod) -> None:
    root = tmp_path / "sessions"
    token = root / "%2Fworkspace"
    parent = token / "019f-main"
    other = token / "019f-other-primary"
    _session(parent)
    _session(other)
    (other / "chat_history.jsonl").write_text("{}\n" * 30, encoding="utf-8")
    (token / "prompt_history.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-07-19T15:00:00Z",
                "session_id": parent.name,
                "prompt": "first",
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-07-19T16:00:00Z",
                "session_id": other.name,
                "prompt": "later",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Newest mtime is other; history first primary is parent
    assert mod.resolve_primary_session_id(root) == parent.name


def test_mirrors_real_coredis_layout(tmp_path: Path, mod) -> None:
    """Reproduce the failed batch layout: follow-ups must not stick to subagent."""
    root = tmp_path / "sessions"
    token = root / "%2Fworkspace"
    main = token / "019f7aef-cc5d-79b3-93d5-143d2226db15"
    sub = token / "019f7af0-1ffe-7563-8d53-e427c9a27381"
    _session(main, kind="")
    _session(sub, kind="subagent")
    (main / "subagents" / sub.name).mkdir(parents=True)
    (sub / "chat_history.jsonl").write_text("{}\n" * 100, encoding="utf-8")
    (token / "prompt_history.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-19T15:12:43Z",
                        "session_id": main.name,
                        "prompt": "first",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-19T15:19:11Z",
                        "session_id": sub.name,
                        "prompt": "follow",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # preferred sticky wrongly set to subagent → still primary
    assert mod.resolve_primary_session_id(root, preferred_id=sub.name) == main.name
    assert mod.resolve_primary_session_id(root) == main.name


def test_nested_subagents_dir_not_primary(tmp_path: Path, mod) -> None:
    root = tmp_path / "sessions"
    token = root / "%2Fworkspace"
    parent = token / "parent"
    nested = parent / "subagents" / "nested-sub"
    _session(parent)
    _session(nested, kind="subagent")
    # nested is under parent, not a top-level candidate — list should only be parent
    ids = {p.name for p in mod.list_primary_session_dirs(root)}
    assert ids == {parent.name}
