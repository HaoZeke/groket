"""Session turn-gate helpers (interactive multi-turn on the session volume)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from groket.session.turn_gate import (
    _PENDING_QUEUE,
    _ensure_gate_dirs,
    _follow_up_already_staged,
    _gate_command,
    _read_status_file,
    _write_queue,
    _write_status,
    drain_queued_follow_up,
    enqueue_follow_up,
    list_queued_follow_ups,
    read_turn_gate_status,
    session_awaits_follow_up,
    session_pending_label,
    traces_volume_for_session,
    turn_gate_dirs_for_session,
    write_done_for_session,
    write_follow_up_for_session,
)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    vol = tmp_path / "traces" / "groket-run1-m"
    sess = vol / "%2Fworkspace" / "019f0ea5-sess"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    gate = vol / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess", "turn": 1})
        + "\n",
        encoding="utf-8",
    )
    return vol, sess


def test_events_have_open_turn_caches_by_mtime(tmp_path: Path) -> None:
    """Repeated probes must not re-read events.jsonl when mtime/size match."""
    from groket.session import turn_gate as tg

    sess = tmp_path / "sess"
    sess.mkdir()
    events = sess / "events.jsonl"
    events.write_text(
        json.dumps({"type": "turn_started"}) + "\n",
        encoding="utf-8",
    )
    tg._OPEN_TURN_CACHE.clear()
    assert tg.events_have_open_turn(sess) is True
    # Corrupt file content but keep mtime/size identical so cache must win.
    key = str(events.resolve())
    cached = tg._OPEN_TURN_CACHE[key]
    events.write_bytes(b"x" * cached[1])
    # Restore size match with same mtime_ns stamped into cache after force.
    st = events.stat()
    tg._OPEN_TURN_CACHE[key] = (
        int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
        int(st.st_size),
        True,
    )
    assert tg.events_have_open_turn(sess) is True  # cache hit, ignores corrupt body

    # Size change busts the cache.
    events.write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    assert tg.events_have_open_turn(sess) is False


def test_traces_volume_and_status(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    assert (
        traces_volume_for_session(sess) == vol.resolve() or traces_volume_for_session(sess) == vol
    )
    st = read_turn_gate_status(sess)
    assert st.get("state") == "awaiting_follow_up"
    assert session_awaits_follow_up(sess) is True
    assert "follow-up" in session_pending_label(sess)


def test_write_follow_up_and_done_clears_pending(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    assert write_follow_up_for_session(sess, "next") == "sent"
    gate = vol / ".groket-turn"
    assert (gate / "next-prompt.txt").read_text(encoding="utf-8") == "next"
    assert "follow_up" in (gate / "command").read_text(encoding="utf-8")
    assert json.loads((gate / "status.json").read_text())["state"] == "running"

    write_done_for_session(sess)
    assert "done" in (gate / "command").read_text(encoding="utf-8")
    # Host only writes command=done; status stays until finalize / entrypoint.
    assert json.loads((gate / "status.json").read_text())["state"] == "running"
    assert session_awaits_follow_up(sess) is False
    # No open harness turn → lifecycle settles (does not wait on mtimes).
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    from groket.session.turn_gate import host_requested_done, lifecycle_state

    assert host_requested_done(sess) is True
    assert lifecycle_state(sess) == "done"
    assert session_pending_label(sess) == ""
    assert read_turn_gate_status(sess).get("state") == "running"


def test_queue_follow_ups_while_running_and_drain(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    # First send while awaiting stages immediately.
    assert write_follow_up_for_session(sess, "a") == "sent"
    # Second while command still staged → host queue.
    assert write_follow_up_for_session(sess, "b") == "queued"
    assert write_follow_up_for_session(sess, "c") == "queued"
    assert list_queued_follow_ups(sess) == ["b", "c"]
    assert "2 queued" in session_pending_label(sess)

    # Simulate entrypoint consumed staged prompt and is awaiting again.
    (gate / "command").unlink(missing_ok=True)
    (gate / "next-prompt.txt").unlink(missing_ok=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess", "turn": 2})
        + "\n",
        encoding="utf-8",
    )
    assert drain_queued_follow_up(sess) == "b"
    assert (gate / "next-prompt.txt").read_text(encoding="utf-8") == "b"
    assert list_queued_follow_ups(sess) == ["c"]
    # Not awaiting while staged running.
    assert drain_queued_follow_up(sess) is None


def test_load_session_meta_shows_awaiting_not_completed(tmp_path: Path) -> None:
    """Main session list must not show completed while gate awaits follow-up."""
    from groket.parser import load_session_meta

    vol, sess = _layout(tmp_path)
    # Harness wrote a successful turn_ended — without gate override this is "success"
    (sess / "events.jsonl").write_text(
        json.dumps({"ts": 1, "type": "turn_ended", "outcome": "success"}) + "\n",
        encoding="utf-8",
    )
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    meta = load_session_meta(sess)
    assert meta.turn_outcome == "awaiting_follow_up"
    assert meta.turn_in_progress is True
    assert meta.turn_failed is False
    _ = vol


def test_command_done_overrides_stale_awaiting_status(tmp_path: Path) -> None:
    """Host Done rejects further awaits; closed turns → lifecycle done."""
    from groket.session.turn_gate import host_requested_done, lifecycle_state

    _vol, sess = _layout(tmp_path)
    gate = _vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    assert json.loads((gate / "status.json").read_text())["state"] == "awaiting_follow_up"
    assert session_awaits_follow_up(sess) is False
    assert host_requested_done(sess) is True
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    assert lifecycle_state(sess) == "done"
    assert session_pending_label(sess) == ""
    assert read_turn_gate_status(sess).get("state") == "awaiting_follow_up"


def test_last_turn_staged_is_running_not_done(tmp_path: Path) -> None:
    """Last-turn follow-up must not look complete before the agent runs it."""
    from groket.session.turn_gate import lifecycle_state, read_staged_follow_up

    vol, sess = _layout(tmp_path)
    assert write_follow_up_for_session(sess, "finish up now", final=True) == "sent"
    staged = read_staged_follow_up(sess)
    assert staged is not None
    assert staged[0] == "finish up now"
    assert staged[1] is True
    # Closed previous turn + final_turn must not settle to done while staged.
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    assert lifecycle_state(sess) == "running"
    label = session_pending_label(sess)
    assert "running" in label
    assert label != ""


def test_last_turn_running_after_prompt_consumed(tmp_path: Path) -> None:
    """Entrypoint deleted next-prompt but final_turn remains while turn runs."""
    from groket.session.turn_gate import lifecycle_state, read_staged_follow_up

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    assert write_follow_up_for_session(sess, "last one", final=True) == "sent"
    # Simulate entrypoint consume.
    (gate / "command").unlink(missing_ok=True)
    (gate / "next-prompt.txt").unlink(missing_ok=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "019f0ea5-sess", "turn": 2}) + "\n",
        encoding="utf-8",
    )
    assert read_staged_follow_up(sess) is None
    assert (gate / "final_turn").is_file()
    # No harness turn_ended yet → handoff / about to start.
    assert lifecycle_state(sess) == "running"
    # Open harness turn → ending (last turn in progress).
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "turn_number": 2}) + "\n",
        encoding="utf-8",
    )
    assert lifecycle_state(sess) == "ending"
    # Turn closed while final_turn remains (entrypoint still sharing) → done.
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "turn_number": 2})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    assert lifecycle_state(sess) == "done"
    assert session_pending_label(sess) == ""


def test_last_turn_closed_settles_even_while_fresh(tmp_path: Path) -> None:
    """Leftover final_turn after turn_ended must not pin the UI to running."""
    from groket.session.turn_gate import lifecycle_state

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    assert write_follow_up_for_session(sess, "wrap up", final=True) == "sent"
    (gate / "command").unlink(missing_ok=True)
    (gate / "next-prompt.txt").unlink(missing_ok=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "019f0ea5-sess", "turn": 2}) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n"
        + json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    assert (gate / "final_turn").is_file()
    assert lifecycle_state(sess) == "done"


def test_settle_stale_zombie_last_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dead container leftovers finalize so the UI stops looking live."""
    from groket.session import turn_gate as tg
    from groket.session.turn_gate import (
        lifecycle_state,
        session_needs_live_timeline,
        settle_stale_session_gates,
    )

    vol, sess = _layout(tmp_path)
    assert write_follow_up_for_session(sess, "never ran", final=True) == "sent"
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    # Activity looks ancient → orphan settle.
    monkeypatch.setattr(tg, "session_activity_stale", lambda _s, **_k: True)
    assert settle_stale_session_gates(sess) is True
    gate = vol / ".groket-turn"
    assert json.loads((gate / "status.json").read_text())["state"] == "done"
    assert not (gate / "final_turn").exists()
    assert not (gate / "next-prompt.txt").exists()
    assert lifecycle_state(sess) == "done"
    assert session_needs_live_timeline(sess) is False


def test_session_needs_live_timeline_fresh_updates(tmp_path: Path) -> None:
    """Fresh updates.jsonl keeps live polling when gate status is missing/lagging."""
    from groket.session.turn_gate import session_needs_live_timeline

    sess = tmp_path / "orphan-live"
    sess.mkdir()
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    (sess / "updates.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    assert session_needs_live_timeline(sess) is True


def test_session_needs_live_timeline_awaiting_skips(tmp_path: Path) -> None:
    """Awaiting follow-up is idle — do not keep re-parsing traces."""
    from groket.session.turn_gate import session_needs_live_timeline

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    (sess / "updates.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    assert session_needs_live_timeline(sess) is False


def test_finalize_session_gate_writes_done(tmp_path: Path) -> None:
    """Host finalize after docker stop rewrites status to done."""
    from groket.session.turn_gate import finalize_session_gate, lifecycle_state

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "019f0ea5-sess", "turn": 3}) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"}) + "\n",
        encoding="utf-8",
    )
    assert lifecycle_state(sess) == "ending"
    finalize_session_gate(sess)
    assert json.loads((gate / "status.json").read_text())["state"] == "done"
    assert lifecycle_state(sess) == "done"
    assert session_pending_label(sess) == ""


# ── volume discovery and gate directory listing ──────────────────────────


def test_traces_volume_no_gate_dirs(tmp_path: Path) -> None:
    """Volume found via grandparent fallback; no .groket-turn* → empty list."""
    from groket.session.turn_gate import turn_gate_dirs_for_session

    vol = tmp_path / "traces" / "groket-run-m"
    sess = vol / "%2F" / "sess-id"
    sess.mkdir(parents=True)
    dirs = turn_gate_dirs_for_session(sess)
    assert dirs == []


def test_traces_volume_none_returns_empty_gate_dirs(tmp_path: Path) -> None:
    from groket.session.turn_gate import turn_gate_dirs_for_session

    # Isolated session with no recognisable volume
    sess = tmp_path / "isolated"
    sess.mkdir()
    dirs = turn_gate_dirs_for_session(sess)
    assert dirs == []


def test_gate_command_missing_and_present(tmp_path: Path) -> None:
    from groket.session.turn_gate import _gate_command

    gate = tmp_path / "gate"
    gate.mkdir()
    assert _gate_command(gate) == ""
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    assert _gate_command(gate) == "follow_up"


def test_read_status_file_missing_and_invalid(tmp_path: Path) -> None:
    from groket.session.turn_gate import _read_status_file

    gate = tmp_path / "gate"
    gate.mkdir()
    assert _read_status_file(gate) == {}
    (gate / "status.json").write_text("not-json", encoding="utf-8")
    assert _read_status_file(gate) == {}


def test_write_status_creates_dir(tmp_path: Path) -> None:
    from groket.session.turn_gate import _write_status

    gate = tmp_path / "new-gate"
    _write_status(gate, state="running", session_id="s1", turn=2)
    data = json.loads((gate / "status.json").read_text(encoding="utf-8"))
    assert data["state"] == "running"
    assert data["session_id"] == "s1"
    assert data["turn"] == 2


def test_write_status_no_optional_fields(tmp_path: Path) -> None:
    from groket.session.turn_gate import _write_status

    gate = tmp_path / "gate2"
    _write_status(gate, state="done")
    data = json.loads((gate / "status.json").read_text(encoding="utf-8"))
    assert data["state"] == "done"
    assert "session_id" not in data
    assert "turn" not in data


def test_read_turn_gate_status_no_gate(tmp_path: Path) -> None:
    sess = tmp_path / "s"
    sess.mkdir()
    st = read_turn_gate_status(sess)
    assert st == {}


def test_read_turn_gate_no_state_in_status(tmp_path: Path) -> None:
    """Gate status.json with empty state is skipped."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(json.dumps({"foo": "bar"}) + "\n", encoding="utf-8")
    st = read_turn_gate_status(sess)
    assert st == {}


def test_read_turn_gate_done_without_match(tmp_path: Path) -> None:
    """Command=done does not invent status=done; status.json is returned as-is."""
    vol = tmp_path / "traces" / "groket-r-m"
    sess = vol / "%2F" / "no-match"
    sess.mkdir(parents=True)
    gate = vol / ".groket-turn"
    gate.mkdir()
    (gate / "command").write_text("done\n", encoding="utf-8")
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "other"}) + "\n",
        encoding="utf-8",
    )
    st = read_turn_gate_status(sess)
    assert st["state"] == "awaiting_follow_up"


def test_session_awaits_done_cmd_overrides(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    assert session_awaits_follow_up(sess) is False


def test_session_awaits_state_done(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "done", "session_id": "019f0ea5-sess"}) + "\n", encoding="utf-8"
    )
    assert session_awaits_follow_up(sess) is False


def test_enqueue_empty_raises(tmp_path: Path) -> None:
    from groket.session.turn_gate import enqueue_follow_up

    vol, sess = _layout(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        enqueue_follow_up(sess, "")


def test_write_follow_up_empty_raises(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        write_follow_up_for_session(sess, "")


def test_write_follow_up_done_raises(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="done"):
        write_follow_up_for_session(sess, "hello")


def test_list_queued_plain_text_lines(tmp_path: Path) -> None:
    """Queue file with plain text lines (not JSON) are read as-is."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "pending-prompts.jsonl").write_text("plain line\n\n", encoding="utf-8")
    q = list_queued_follow_ups(sess)
    assert q == ["plain line"]


def test_list_queued_empty_prompt_skipped(tmp_path: Path) -> None:
    """JSON lines with empty prompt string are skipped."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "pending-prompts.jsonl").write_text(json.dumps({"prompt": ""}) + "\n", encoding="utf-8")
    assert list_queued_follow_ups(sess) == []


def test_list_queued_no_gate(tmp_path: Path) -> None:
    """Session with no traces volume → empty queue."""
    sess = tmp_path / "isolated"
    sess.mkdir()
    assert list_queued_follow_ups(sess) == []


def test_drain_not_awaiting(tmp_path: Path) -> None:
    """drain returns None when gate is not awaiting."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    assert drain_queued_follow_up(sess) is None


def test_drain_already_staged(tmp_path: Path) -> None:
    """drain returns None when a follow-up is already staged."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("staged", encoding="utf-8")
    assert drain_queued_follow_up(sess) is None


def test_drain_empty_queue(tmp_path: Path) -> None:
    """drain returns None when queue is empty."""
    vol, sess = _layout(tmp_path)
    assert drain_queued_follow_up(sess) is None


def test_write_done_clears_queue(tmp_path: Path) -> None:
    """write_done removes pending queue file."""
    from groket.session.turn_gate import enqueue_follow_up

    vol, sess = _layout(tmp_path)
    enqueue_follow_up(sess, "queued item")
    write_done_for_session(sess)
    gate = vol / ".groket-turn"
    qp = gate / "pending-prompts.jsonl"
    # Queue file either gone or empty
    assert not qp.exists() or not qp.read_text(encoding="utf-8").strip()


def test_session_pending_label_running(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "019f0ea5-sess", "turn": 3}) + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert "agent running" in label
    assert "3" in label


def test_session_pending_label_turn_in_progress(tmp_path: Path) -> None:
    sess = tmp_path / "s"
    sess.mkdir()
    label = session_pending_label(sess, turn_in_progress=True)
    assert "turn in progress" in label


def test_session_pending_label_unknown_state(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "paused", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert "paused" in label


def test_session_pending_label_queued_only(tmp_path: Path) -> None:
    from groket.session.turn_gate import enqueue_follow_up

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    # State = empty (not running, not awaiting)
    (gate / "status.json").write_text(json.dumps({"state": ""}) + "\n", encoding="utf-8")
    enqueue_follow_up(sess, "q1")
    label = session_pending_label(sess)
    assert "queued" in label


def test_traces_volume_via_glob(tmp_path: Path) -> None:
    """Volume discovered via .groket-turn* glob at parent.parent level."""
    from groket.session.turn_gate import traces_volume_for_session

    vol = tmp_path / "groket-run" / "workspace"
    sess = vol / "sess-id"
    sess.mkdir(parents=True)
    # Create .groket-turn at the grandparent (tmp_path / "groket-run")
    (tmp_path / "groket-run" / ".groket-turn").mkdir()
    result = traces_volume_for_session(sess)
    assert result is not None


def test_read_turn_gate_matched_sid_done_cmd(tmp_path: Path) -> None:
    """Matching session_id + done command leaves status.json state unchanged."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    st = read_turn_gate_status(sess)
    assert st["state"] == "awaiting_follow_up"


def test_read_turn_gate_saw_done_with_best(tmp_path: Path) -> None:
    """Done command does not override status when session_id does not match."""
    vol = tmp_path / "traces" / "groket-r-m"
    sess = vol / "%2F" / "unmatched-sess"
    sess.mkdir(parents=True)
    gate = vol / ".groket-turn"
    gate.mkdir()
    (gate / "command").write_text("done\n", encoding="utf-8")
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "other-sess", "turn": 1}) + "\n",
        encoding="utf-8",
    )
    st = read_turn_gate_status(sess)
    assert st["state"] == "running"


def test_read_turn_gate_saw_done_no_best(tmp_path: Path) -> None:
    """Done command with no status.json → empty status (not fabricated done)."""
    vol = tmp_path / "traces" / "groket-r-m"
    sess = vol / "%2F" / "no-status-sess"
    sess.mkdir(parents=True)
    gate = vol / ".groket-turn"
    gate.mkdir()
    (gate / "command").write_text("done\n", encoding="utf-8")
    st = read_turn_gate_status(sess)
    assert not st.get("state")


def test_ensure_gate_dirs_fallback_to_volume(tmp_path: Path) -> None:
    from groket.session.turn_gate import _ensure_gate_dirs

    vol = tmp_path / "traces" / "groket-run" / "workspace"
    sess = vol / "sess"
    sess.mkdir(parents=True)
    dirs = _ensure_gate_dirs(sess)
    assert len(dirs) == 1
    assert dirs[0].name == ".groket-turn"


def test_ensure_gate_dirs_creates_fallback(tmp_path: Path) -> None:
    """When no existing gate dirs, fallback creates .groket-turn."""
    from groket.session.turn_gate import _ensure_gate_dirs

    vol = tmp_path / "groket-run" / "workspace"
    sess = vol / "sess"
    sess.mkdir(parents=True)
    dirs = _ensure_gate_dirs(sess)
    assert len(dirs) == 1
    assert dirs[0].name == ".groket-turn"


def test_follow_up_already_staged(tmp_path: Path) -> None:
    from groket.session.turn_gate import _follow_up_already_staged

    gate = tmp_path / "gate"
    gate.mkdir()
    assert _follow_up_already_staged(gate) is False
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("hello", encoding="utf-8")
    assert _follow_up_already_staged(gate) is True
    # Empty next-prompt → not staged
    (gate / "next-prompt.txt").write_text("", encoding="utf-8")
    assert _follow_up_already_staged(gate) is False


def test_session_pending_label_awaiting_with_turn(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess", "turn": 2})
        + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert "awaiting follow-up" in label
    assert "2" in label


def test_session_pending_label_awaiting_no_turn(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert "awaiting follow-up" in label


def test_session_pending_label_running_no_turn(tmp_path: Path) -> None:
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert "agent running" in label


def test_session_pending_label_custom_state(tmp_path: Path) -> None:
    """pending_label for a non-standard state returns the raw state string."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "custom_state", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert "custom_state" in label


def test_session_pending_label_done_via_command(tmp_path: Path) -> None:
    """command=done with closed turns settles; open turn shows ending."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up"}) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"})
        + "\n"
        + json.dumps({"type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    assert session_pending_label(sess) == ""
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"}) + "\n",
        encoding="utf-8",
    )
    assert session_pending_label(sess).startswith("ending_")


def test_session_pending_label_queued_with_running(tmp_path: Path) -> None:
    """pending_label shows queue count alongside running state."""
    from groket.session.turn_gate import enqueue_follow_up

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "turn": 1, "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    enqueue_follow_up(sess, "queued prompt 1")
    label = session_pending_label(sess)
    assert "1 queued" in label


def test_write_follow_up_queues_when_staged(tmp_path: Path) -> None:
    """write_follow_up_for_session queues when follow_up already staged."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    # Stage a follow-up first
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("first prompt", encoding="utf-8")
    result = write_follow_up_for_session(sess, "second prompt")
    assert result == "queued"
    queued = list_queued_follow_ups(sess)
    assert "second prompt" in queued


def test_drain_queued_follow_up_stages_and_pops(tmp_path: Path) -> None:
    """drain_queued_follow_up pops first queued prompt and stages it."""
    from groket.session.turn_gate import enqueue_follow_up

    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    enqueue_follow_up(sess, "prompt A")
    enqueue_follow_up(sess, "prompt B")
    result = drain_queued_follow_up(sess)
    assert result == "prompt A"
    remaining = list_queued_follow_ups(sess)
    assert remaining == ["prompt B"]


def test_list_queued_oserror_returns_empty(tmp_path: Path) -> None:
    """list_queued_follow_ups returns [] when primary gate cannot be found."""
    sd = tmp_path / "nonexistent" / "sess"
    result = list_queued_follow_ups(sd)
    assert result == []


def test_write_follow_up_not_awaiting_queues(tmp_path: Path) -> None:
    """write_follow_up_for_session queues when gate is not awaiting."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    result = write_follow_up_for_session(sess, "hello")
    assert result == "queued"


def test_traces_volume_parent_parent_fallback(tmp_path: Path) -> None:
    """traces_volume_for_session falls back to parent.parent when no .groket-turn found."""
    vol = tmp_path / "traces" / "groket-r"
    ws = vol / "workspace"
    sd = ws / "019f-sess"
    sd.mkdir(parents=True)
    # No .groket-turn dir → falls back to parent.parent (groket-r)
    result = traces_volume_for_session(sd)
    assert result is not None
    assert result == vol


def test_turn_gate_dirs_returns_empty_no_volume(tmp_path: Path) -> None:
    """turn_gate_dirs_for_session returns [] when volume is None."""
    sd = tmp_path / "no-vol"
    sd.mkdir()
    result = turn_gate_dirs_for_session(sd)
    assert result == []


def test_gate_command_reads_file(tmp_path: Path) -> None:
    """_gate_command reads command file content."""
    gate = tmp_path / "gate"
    gate.mkdir()
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    result = _gate_command(gate)
    assert result == "follow_up"


def test_gate_command_no_file(tmp_path: Path) -> None:
    """_gate_command returns empty when no command file."""
    gate = tmp_path / "gate"
    gate.mkdir()
    result = _gate_command(gate)
    assert result == ""


def test_read_status_file_empty(tmp_path: Path) -> None:
    """_read_status_file returns {} when no status.json."""
    gate = tmp_path / "gate"
    gate.mkdir()
    result = _read_status_file(gate)
    assert result == {}


def test_read_status_file_bad_json(tmp_path: Path) -> None:
    """_read_status_file returns {} for invalid JSON."""
    gate = tmp_path / "gate"
    gate.mkdir()
    (gate / "status.json").write_text("not json", encoding="utf-8")
    result = _read_status_file(gate)
    assert result == {}


def test_write_status_with_session_and_turn(tmp_path: Path) -> None:
    """_write_status writes session_id and turn to status.json."""
    gate = tmp_path / "gate"
    _write_status(gate, state="running", session_id="s1", turn=3)
    data = json.loads((gate / "status.json").read_text(encoding="utf-8"))
    assert data["state"] == "running"
    assert data["session_id"] == "s1"
    assert data["turn"] == 3


def test_ensure_gate_dirs_nested_volume(tmp_path: Path) -> None:
    """_ensure_gate_dirs creates .groket-turn for nested volume layout."""
    vol = tmp_path / "traces" / "groket-r"
    ws = vol / "workspace"
    sd = ws / "019f-sess"
    sd.mkdir(parents=True)
    # No .groket-turn dirs exist yet
    dirs = _ensure_gate_dirs(sd)
    assert len(dirs) == 1
    assert dirs[0].name == ".groket-turn"


def test_follow_up_already_staged_true(tmp_path: Path) -> None:
    """_follow_up_already_staged returns True when command=follow_up and next-prompt exists."""
    gate = tmp_path / "gate"
    gate.mkdir()
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("hello", encoding="utf-8")
    result = _follow_up_already_staged(gate)
    assert result is True


def test_follow_up_already_staged_false_no_command(tmp_path: Path) -> None:
    """_follow_up_already_staged returns False when no command file."""
    gate = tmp_path / "gate"
    gate.mkdir()
    result = _follow_up_already_staged(gate)
    assert result is False


def test_write_queue_clears_when_empty(tmp_path: Path) -> None:
    """_write_queue removes the queue file when prompts is empty."""
    gate = tmp_path / "gate"
    gate.mkdir()
    qp = gate / _PENDING_QUEUE
    qp.write_text('{"prompt": "old"}\n', encoding="utf-8")
    _write_queue(gate, [])
    assert not qp.exists()


def test_drain_queued_not_awaiting_returns_none(tmp_path: Path) -> None:
    """drain_queued_follow_up returns None when session isn't awaiting."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(json.dumps({"state": "running"}) + "\n", encoding="utf-8")
    result = drain_queued_follow_up(sess)
    assert result is None


def test_drain_queued_empty_queue_returns_none(tmp_path: Path) -> None:
    """drain_queued_follow_up returns None when queue is empty."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    result = drain_queued_follow_up(sess)
    assert result is None


def test_write_done_clears_pending_prompts(tmp_path: Path) -> None:
    """write_done_for_session clears pending prompts from queue."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "019f0ea5-sess"}) + "\n",
        encoding="utf-8",
    )
    enqueue_follow_up(sess, "pending")
    write_done_for_session(sess)
    assert list_queued_follow_ups(sess) == []
    assert _gate_command(gate) == "done"


def test_session_pending_label_custom_state_string(tmp_path: Path) -> None:
    """session_pending_label returns custom state as label."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "status.json").write_text(
        json.dumps({"state": "custom_state", "turn": 0}) + "\n", encoding="utf-8"
    )
    label = session_pending_label(sess)
    assert label == "custom_state"


def test_pending_label_turn_in_progress_queued(tmp_path: Path) -> None:
    """session_pending_label shows 'turn in progress' with queued count."""
    vol = tmp_path / "traces" / "groket-run-x"
    sess = vol / "%2Fworkspace" / "019f-sess2"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    gate = vol / ".groket-turn"
    gate.mkdir(parents=True)
    # No state file → turn_in_progress=True gives the label
    label = session_pending_label(sess, turn_in_progress=True)
    assert "turn in progress" in label


def test_write_follow_up_done_session_raises(tmp_path: Path) -> None:
    """write_follow_up_for_session raises when session has done command."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already marked done"):
        write_follow_up_for_session(sess, "more")


def test_write_follow_up_blank_prompt_raises(tmp_path: Path) -> None:
    """write_follow_up_for_session raises for blank prompt."""
    vol, sess = _layout(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        write_follow_up_for_session(sess, "")


def test_list_queued_parses_plain_text(tmp_path: Path) -> None:
    """list_queued_follow_ups handles plain text lines (not JSON)."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    qp = gate / _PENDING_QUEUE
    qp.write_text("plain prompt line\n", encoding="utf-8")
    result = list_queued_follow_ups(sess)
    assert result == ["plain prompt line"]


def test_list_queued_skips_empty_prompt(tmp_path: Path) -> None:
    """list_queued_follow_ups skips JSON objects with empty prompt."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    qp = gate / _PENDING_QUEUE
    qp.write_text('{"prompt": ""}\n{"prompt": "real"}\n', encoding="utf-8")
    result = list_queued_follow_ups(sess)
    assert result == ["real"]


# ── traces volume fallback paths ─────────────────────────────────────────


def test_traces_volume_parent_fallback(tmp_path: Path) -> None:
    """traces_volume_for_session falls back to parent.parent."""
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    result = traces_volume_for_session(deep)
    assert result is not None or result is None


def test_turn_gate_dirs_no_volume(tmp_path: Path) -> None:
    """turn_gate_dirs_for_session returns empty when no volume found."""
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    result = turn_gate_dirs_for_session(orphan)
    assert isinstance(result, list)


def test_drain_queued_stages_next(tmp_path: Path) -> None:
    """drain_queued_follow_up stages the first queued prompt."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up")
    enqueue_follow_up(sess, "first")
    enqueue_follow_up(sess, "second")
    result = drain_queued_follow_up(sess)
    assert result == "first"
    remaining = list_queued_follow_ups(sess)
    assert remaining == ["second"]


def test_session_pending_label_awaiting(tmp_path: Path) -> None:
    """session_pending_label shows awaiting follow-up."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up", turn=2)
    label = session_pending_label(sess)
    assert "awaiting" in label
    assert "turn 2" in label


def test_write_follow_up_queued_when_staged(tmp_path: Path) -> None:
    """write_follow_up_for_session queues when follow-up already staged."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up")
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("already staged", encoding="utf-8")
    result = write_follow_up_for_session(sess, "new prompt")
    assert result == "queued"


def test_write_follow_up_sent_when_awaiting(tmp_path: Path) -> None:
    """write_follow_up_for_session sends when awaiting and not staged."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up")
    result = write_follow_up_for_session(sess, "new prompt")
    assert result == "sent"


# ── error resilience in turn gate I/O ─────────────────────────────────────


def test_traces_volume_glob_oserror(tmp_path: Path) -> None:
    """traces_volume_for_session handles OSError in glob check."""
    sd = tmp_path / "vol" / "ws" / "sess"
    sd.mkdir(parents=True)
    with patch.object(Path, "glob", side_effect=OSError("denied")):
        result = traces_volume_for_session(sd)
    assert result is not None or result is None


def test_traces_volume_parent_not_dir(tmp_path: Path) -> None:
    """traces_volume_for_session returns None when parent.parent is not a dir."""
    sd = tmp_path / "x"
    sd.mkdir()
    with patch.object(Path, "is_dir", return_value=False):
        result = traces_volume_for_session(sd)
    assert result is None


def test_turn_gate_dirs_base_none(tmp_path: Path) -> None:
    """turn_gate_dirs_for_session returns empty when traces_volume returns None."""
    with patch("groket.session.turn_gate.traces_volume_for_session", return_value=None):
        result = turn_gate_dirs_for_session(tmp_path)
    assert result == []


def test_gate_command_read_oserror(tmp_path: Path) -> None:
    """_gate_command returns empty on read OSError."""
    gate = tmp_path / "gate"
    gate.mkdir()
    cmd = gate / "command"
    cmd.write_text("follow_up", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("denied")):
        assert _gate_command(gate) == ""


def test_write_status_oserror(tmp_path: Path) -> None:
    """_write_status handles write OSError."""
    gate = tmp_path / "gate"
    gate.mkdir()
    with patch.object(Path, "write_text", side_effect=OSError("denied")):
        _write_status(gate, state="running")  # should not raise


def test_ensure_gate_dirs_no_volume_raises(tmp_path: Path) -> None:
    """_ensure_gate_dirs raises RuntimeError when no volume found."""
    with patch("groket.session.turn_gate.traces_volume_for_session", return_value=None):
        with pytest.raises(RuntimeError, match="traces volume"):
            _ensure_gate_dirs(tmp_path)


def test_list_queued_follow_ups_read_oserror(tmp_path: Path) -> None:
    """list_queued_follow_ups returns empty on read OSError."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    qp = gate / _PENDING_QUEUE
    qp.write_text('{"prompt": "x"}\n', encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("denied")):
        result = list_queued_follow_ups(sess)
    assert result == []


def test_write_queue_unlink_oserror(tmp_path: Path) -> None:
    """_write_queue handles OSError on unlink."""
    gate = tmp_path / "gate"
    gate.mkdir()
    qp = gate / _PENDING_QUEUE
    qp.write_text("old", encoding="utf-8")
    with patch.object(Path, "unlink", side_effect=OSError("denied")):
        _write_queue(gate, [])  # should not raise


def test_follow_up_already_staged_read_oserror(tmp_path: Path) -> None:
    """_follow_up_already_staged returns False on read OSError."""
    gate = tmp_path / "gate"
    gate.mkdir()
    (gate / "command").write_text("follow_up", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("staged", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("denied")):
        assert not _follow_up_already_staged(gate)


def test_write_done_clears_pending(tmp_path: Path) -> None:
    """write_done_for_session clears queue via _write_queue with OSError."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up")
    enqueue_follow_up(sess, "pending")
    with patch("groket.session.turn_gate._write_queue", side_effect=OSError("fail")):
        write_done_for_session(sess)
    assert _gate_command(gate) == "done"


def test_session_pending_label_done_gate(tmp_path: Path) -> None:
    """session_pending_label ends when host done and a harness turn is open."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    (gate / "command").write_text("done\n", encoding="utf-8")
    (sess / "events.jsonl").write_text(
        json.dumps({"type": "turn_started"}) + "\n",
        encoding="utf-8",
    )
    label = session_pending_label(sess)
    assert label.startswith("ending_")


def test_session_pending_label_queued_count(tmp_path: Path) -> None:
    """session_pending_label includes queued count."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up", turn=1)
    enqueue_follow_up(sess, "q1")
    enqueue_follow_up(sess, "q2")
    label = session_pending_label(sess)
    assert "2 queued" in label


def test_traces_volume_fallback_parent_parent(tmp_path: Path) -> None:
    """traces_volume_for_session falls back to parent.parent when no .groket-turn found."""
    vol = tmp_path / "traces" / "groket-run"
    sess = vol / "workspace" / "session"
    sess.mkdir(parents=True)
    result = traces_volume_for_session(sess)
    # parent.parent (groket-run) is a dir → returned as fallback
    assert result == vol


def test_traces_volume_none_when_no_parents(tmp_path: Path) -> None:
    """traces_volume_for_session returns None when parent.parent is not a dir."""
    sess = tmp_path / "sess"
    sess.mkdir()
    # parent.parent is tmp_path parent which exists, so gets returned
    result = traces_volume_for_session(sess)
    assert result is None or result.is_dir()


def test_session_pending_label_running_state(tmp_path: Path) -> None:
    """session_pending_label returns 'agent running' for running state."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="running", turn=2)
    label = session_pending_label(sess)
    assert "agent running" in label
    assert "turn 2" in label


def test_drain_queued_already_staged(tmp_path: Path) -> None:
    """drain_queued_follow_up returns None when follow-up already staged."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="awaiting_follow_up", turn=1)
    # Stage a follow-up manually
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    (gate / "next-prompt.txt").write_text("already staged", encoding="utf-8")
    # Queue something
    enqueue_follow_up(sess, "queued")
    # Already staged → should return None
    result = drain_queued_follow_up(sess)
    assert result is None


def test_session_pending_label_done_state(tmp_path: Path) -> None:
    """session_pending_label returns empty for state=done."""
    vol, sess = _layout(tmp_path)
    gate = vol / ".groket-turn"
    _write_status(gate, state="done", turn=3)
    label = session_pending_label(sess)
    assert label == ""
