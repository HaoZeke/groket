"""Resume seed: staging tree + live symlink for Grok."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from groket.runs.launch_meta import LAUNCH_META_FILENAME
from groket.session.resume import (
    RESUME_SEED_DIRNAME,
    can_resume_session,
    fork_parent_session_dir,
    is_resume_seed_path,
    resume_cwd_token,
    resume_session_id,
    seed_resume_into_traces_vol,
)


def _fake_session(root: Path, *, sid: str = "sess-abc") -> Path:
    token = "%2Fworkspace"
    session = root / "traces" / "groket-old" / token / sid
    session.mkdir(parents=True)
    (session / "chat_history.jsonl").write_text(
        '{"role":"user","content":"hi"}\n', encoding="utf-8"
    )
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session.parent / "prompt_history.jsonl").write_text("p\n", encoding="utf-8")
    return session


def test_resume_session_id_and_cwd_token(tmp_path: Path) -> None:
    s = _fake_session(tmp_path)
    assert resume_session_id(s) == "sess-abc"
    assert resume_cwd_token(s) == "%2Fworkspace"
    assert can_resume_session(s) is True
    assert can_resume_session(tmp_path / "missing") is False


def test_seed_layout_is_staging_plus_live_symlink(tmp_path: Path) -> None:
    source = _fake_session(tmp_path)
    # Parent container noise must not ride into the fork volume.
    (source / "groket-share.json").write_text(
        json.dumps({"share_url": "https://share.example/parent-only", "error": "denied"}),
        encoding="utf-8",
    )
    (source / "session_search.sqlite").write_bytes(b"not-a-db")
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    sid = seed_resume_into_traces_vol(dest_vol, source)
    assert sid == "sess-abc"
    seed = dest_vol / RESUME_SEED_DIRNAME / "%2Fworkspace" / "sess-abc"
    live = dest_vol / "%2Fworkspace" / "sess-abc"
    assert (seed / "chat_history.jsonl").is_file()
    assert not (seed / "groket-share.json").exists()
    assert not (seed / "session_search.sqlite").exists()
    assert live.is_symlink()
    assert live.resolve() == seed.resolve()
    assert is_resume_seed_path(seed) is True
    assert is_resume_seed_path(live) is True
    assert (source / "chat_history.jsonl").is_file()


def test_find_sessions_skips_resume_seed_and_live_link(tmp_path: Path) -> None:
    """Substrate and its live symlink are not operator eval rows."""
    from groket.parser import find_sessions

    source = _fake_session(tmp_path)
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    seed_resume_into_traces_vol(dest_vol, source)
    child = dest_vol / "%2Fworkspace" / "forked-child-id"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")

    found = {p.name for p in find_sessions(dest_vol)}
    assert "sess-abc" not in found
    assert "forked-child-id" in found


def test_seed_resume_rejects_empty_session(tmp_path: Path) -> None:
    empty = tmp_path / "empty-sess"
    empty.mkdir()
    with pytest.raises(ValueError, match="no chat"):
        seed_resume_into_traces_vol(tmp_path / "vol", empty)


def test_seed_resume_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        seed_resume_into_traces_vol(tmp_path / "vol", tmp_path / "gone")


def test_seed_resume_overwrites_existing_and_strips_locks(tmp_path: Path) -> None:
    source = _fake_session(tmp_path)
    (source / "summary.json.lock").write_text("x", encoding="utf-8")
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    seed_resume_into_traces_vol(dest_vol, source)
    # Re-seed replaces substrate
    seed_resume_into_traces_vol(dest_vol, source)
    seed = dest_vol / RESUME_SEED_DIRNAME / "%2Fworkspace" / "sess-abc"
    assert not (seed / "summary.json.lock").exists()
    assert (seed / "chat_history.jsonl").is_file()


def test_resume_cwd_token_fallback(tmp_path: Path) -> None:
    flat = tmp_path / "sess-flat"
    flat.mkdir()
    (flat / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert resume_cwd_token(flat) == "%2Fworkspace"
    assert can_resume_session(flat) is True


def _append_update(
    updates_path: Path,
    *,
    session_id: str,
    timestamp: int,
    session_update: str,
    text: str,
) -> None:
    row = {
        "timestamp": timestamp,
        "method": "session/update",
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": session_update,
                "content": {"type": "text", "text": text},
            },
        },
    }
    prev_u = updates_path.read_text(encoding="utf-8") if updates_path.is_file() else ""
    updates_path.write_text(prev_u + json.dumps(row) + "\n", encoding="utf-8")


def _write_completed_turn(
    events_path: Path,
    updates_path: Path,
    *,
    session_id: str,
    turn_number: int,
    text: str,
    t0: int,
) -> None:
    """One turn with interleaved marker/update timestamps (epoch seconds)."""
    started = {
        "ts": t0,
        "type": "turn_started",
        "session_id": session_id,
        "turn_number": turn_number,
        "model_id": "v9-test",
    }
    ended = {"ts": t0 + 3, "type": "turn_ended", "outcome": "completed"}
    prev = events_path.read_text(encoding="utf-8") if events_path.is_file() else ""
    events_path.write_text(
        prev + json.dumps(started) + "\n" + json.dumps(ended) + "\n",
        encoding="utf-8",
    )
    # User + assistant so consecutive multi-turn fixtures do not coalesce users.
    _append_update(
        updates_path,
        session_id=session_id,
        timestamp=t0 + 1,
        session_update="user_message_chunk",
        text=text,
    )
    _append_update(
        updates_path,
        session_id=session_id,
        timestamp=t0 + 2,
        session_update="agent_message_chunk",
        text=f"reply-{turn_number}",
    )


def test_fork_parent_session_dir_resolves_seed(tmp_path: Path) -> None:
    from groket.runs.launch_meta import build_launch_meta, write_launch_meta

    source = _fake_session(tmp_path, sid="parent-1")
    (source / "events.jsonl").write_text("{}\n", encoding="utf-8")
    vol = tmp_path / "traces" / "groket-fork"
    vol.mkdir(parents=True)
    seed_resume_into_traces_vol(vol, source)
    write_launch_meta(
        vol,
        build_launch_meta(
            model="v9",
            resume_parent_session_id="parent-1",
            resume_fork_session_id="child-1",
        ),
    )
    child = vol / "%2Fworkspace" / "child-1"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")

    parent = fork_parent_session_dir(child)
    assert parent is not None
    assert parent.name == "parent-1"
    assert RESUME_SEED_DIRNAME in parent.parts
    assert fork_parent_session_dir(parent) is None
    assert fork_parent_session_dir(vol / "%2Fworkspace" / "other") is None


def test_parse_timeline_inherits_parent_turns_on_fork(tmp_path: Path) -> None:
    """Fork child with only turn_number=1 still shows parent turn 0 in the timeline."""
    from groket.parser import parse_timeline
    from groket.runs.launch_meta import build_launch_meta, write_launch_meta
    from groket.session.turns import segment_timeline_turns

    source = _fake_session(tmp_path, sid="parent-turns")
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-turns",
        turn_number=0,
        text="parent prompt",
        t0=1_700_000_000,
    )

    vol = tmp_path / "traces" / "groket-fork-turns"
    vol.mkdir(parents=True)
    seed_resume_into_traces_vol(vol, source)
    write_launch_meta(
        vol,
        build_launch_meta(
            model="v9",
            resume_parent_session_id="parent-turns",
            resume_fork_session_id="child-turns",
        ),
    )
    child = vol / "%2Fworkspace" / "child-turns"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")
    _write_completed_turn(
        child / "events.jsonl",
        child / "updates.jsonl",
        session_id="child-turns",
        turn_number=1,
        text="fork continuation",
        t0=1_700_003_600,
    )

    tl = parse_timeline(child)
    turns = segment_timeline_turns(tl)
    assert len(turns) == 2
    assert turns[0].turn_number == 0
    assert turns[1].turn_number == 1
    contents = " ".join(e.content for e in tl)
    assert "parent prompt" in contents
    assert "fork continuation" in contents


def test_parse_timeline_fork_strips_restamped_parent_replay(tmp_path: Path) -> None:
    """Child updates replaying parent tools must not appear again under turn 1."""
    from groket.parser import parse_timeline
    from groket.runs.launch_meta import build_launch_meta, write_launch_meta
    from groket.session.turns import segment_timeline_turns

    source = _fake_session(tmp_path, sid="parent-replay")
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-replay",
        turn_number=0,
        text="parent prompt",
        t0=1_700_000_000,
    )
    # Parent also has a tool_call so the restamp is unambiguous.
    tool_row = {
        "timestamp": 1_700_000_001,
        "method": "session/update",
        "params": {
            "sessionId": "parent-replay",
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-parent-1",
                "title": "read_file",
                "rawInput": {"target_file": "a.py"},
            },
        },
    }
    # Insert tool between user and agent by rewriting updates in order.
    updates = [
        {
            "timestamp": 1_700_000_001,
            "method": "session/update",
            "params": {
                "sessionId": "parent-replay",
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "parent prompt"},
                },
            },
        },
        tool_row,
        {
            "timestamp": 1_700_000_002,
            "method": "session/update",
            "params": {
                "sessionId": "parent-replay",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "reply-0"},
                },
            },
        },
    ]
    (source / "updates.jsonl").write_text(
        "\n".join(json.dumps(r) for r in updates) + "\n", encoding="utf-8"
    )
    (source / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": 1_700_000_000,
                "type": "turn_started",
                "session_id": "parent-replay",
                "turn_number": 0,
                "model_id": "v9-test",
            }
        )
        + "\n"
        + json.dumps({"ts": 1_700_000_003, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )

    vol = tmp_path / "traces" / "groket-fork-replay"
    vol.mkdir(parents=True)
    seed_resume_into_traces_vol(vol, source)
    write_launch_meta(
        vol,
        build_launch_meta(
            model="v9",
            resume_parent_session_id="parent-replay",
            resume_fork_session_id="child-replay",
        ),
    )
    child = vol / "%2Fworkspace" / "child-replay"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")
    # Child: restamped full parent replay + continuation, single turn_number=1.
    restamp_ts = 1_700_010_000
    child_updates = []
    for row in updates:
        r = json.loads(json.dumps(row))
        r["timestamp"] = restamp_ts
        r["params"]["sessionId"] = "child-replay"
        child_updates.append(r)
    child_updates.append(
        {
            "timestamp": restamp_ts + 10,
            "method": "session/update",
            "params": {
                "sessionId": "child-replay",
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "fork continuation"},
                },
            },
        }
    )
    child_updates.append(
        {
            "timestamp": restamp_ts + 11,
            "method": "session/update",
            "params": {
                "sessionId": "child-replay",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-child-new",
                    "title": "run_terminal_command",
                    "rawInput": {"command": "echo hi"},
                },
            },
        }
    )
    (child / "updates.jsonl").write_text(
        "\n".join(json.dumps(r) for r in child_updates) + "\n", encoding="utf-8"
    )
    (child / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": restamp_ts,
                "type": "turn_started",
                "session_id": "child-replay",
                "turn_number": 1,
                "model_id": "v9-test",
            }
        )
        + "\n"
        + json.dumps({"ts": restamp_ts + 12, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )

    tl = parse_timeline(child)
    turns = segment_timeline_turns(tl)
    assert len(turns) == 2
    t0_tools = [e.tool_call_id for e in turns[0].events if e.tool_call_id]
    t1_tools = [e.tool_call_id for e in turns[1].events if e.tool_call_id]
    assert "call-parent-1" in t0_tools
    assert "call-parent-1" not in t1_tools
    assert "call-child-new" in t1_tools
    assert "fork continuation" in " ".join(e.content for e in turns[1].events)
    assert "parent prompt" not in " ".join(e.content for e in turns[1].events)


def test_parse_timeline_fork_empty_events_keeps_parent_turns(tmp_path: Path) -> None:
    """Child with empty events and re-stamped updates does not collapse parent turns."""
    from groket.parser import parse_timeline
    from groket.runs.launch_meta import build_launch_meta, write_launch_meta
    from groket.session.turns import segment_timeline_turns

    source = _fake_session(tmp_path, sid="parent-multi")
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-multi",
        turn_number=0,
        text="turn0",
        t0=1_700_000_000,
    )
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-multi",
        turn_number=1,
        text="turn1",
        t0=1_700_000_100,
    )

    vol = tmp_path / "traces" / "groket-fork-empty-ev"
    vol.mkdir(parents=True)
    seed_resume_into_traces_vol(vol, source)
    write_launch_meta(
        vol,
        build_launch_meta(
            model="v9",
            resume_parent_session_id="parent-multi",
            resume_fork_session_id="child-empty-ev",
        ),
    )
    child = vol / "%2Fworkspace" / "child-empty-ev"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")
    (child / "events.jsonl").write_text("", encoding="utf-8")
    # Re-timestamped full copy of parent updates (Grok fork pattern).
    restamped = []
    for line in (source / "updates.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["timestamp"] = 1_700_010_000
        row["params"]["sessionId"] = "child-empty-ev"
        restamped.append(json.dumps(row))
    (child / "updates.jsonl").write_text("\n".join(restamped) + "\n", encoding="utf-8")

    parent_turns = len(
        segment_timeline_turns(
            parse_timeline(vol / RESUME_SEED_DIRNAME / "%2Fworkspace" / "parent-multi")
        )
    )
    assert parent_turns == 2
    child_turns = segment_timeline_turns(parse_timeline(child))
    assert len(child_turns) == 2


def test_seed_lock_unlink_and_hist_copy_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_session(tmp_path)
    (source / "summary.json.lock").write_text("x", encoding="utf-8")
    dest_vol = tmp_path / "vol"
    dest_vol.mkdir()

    real_unlink = Path.unlink
    real_copy2 = shutil.copy2

    def boom_unlink(self, *a, **k):
        if str(self).endswith(".lock"):
            raise OSError("busy")
        return real_unlink(self, *a, **k)

    def boom_copy2(src, dst, *a, **k):
        if "prompt_history" in str(src):
            raise OSError("no hist")
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom_unlink)
    monkeypatch.setattr(shutil, "copy2", boom_copy2)
    sid = seed_resume_into_traces_vol(dest_vol, source)
    assert sid == "sess-abc"


def test_seed_empty_session_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _fake_session(tmp_path)
    monkeypatch.setattr(
        "groket.session.resume.resume_session_id",
        lambda _p: "",
    )
    with pytest.raises(ValueError, match="empty session id"):
        seed_resume_into_traces_vol(tmp_path / "vol", source)


def test_start_container_seed_failure_propagates(tmp_path: Path) -> None:
    from groket.docker.orchestrator import ContainerConfig, DockerOrchestrator

    class FakeDocker:
        def run(self, *a, **k):
            raise AssertionError("should not run")

    o = DockerOrchestrator(tmp_path / "runs")
    o._docker = FakeDocker()  # type: ignore[assignment]
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="x",
        container_name="groket-resume-fail",
        resume_source_dir=str(tmp_path / "missing-session"),
    )
    with pytest.raises(FileNotFoundError):
        o.start_container(cfg, "fake-image:tag", auth, cfg_path)


def test_start_container_sets_restore_and_commit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fork/resume injects RESTORE_CODE and REPO_COMMIT for workspace restore."""
    from unittest.mock import MagicMock

    from groket.docker.orchestrator import ContainerConfig, DockerOrchestrator

    o = DockerOrchestrator.__new__(DockerOrchestrator)
    o.work_dir = tmp_path / "runs"
    o.work_dir.mkdir(parents=True)
    o._build_dir = o.work_dir / "docker-build"
    o._docker = MagicMock()
    o._docker.run.return_value = type("C", (), {"id": "deadbeefcafebabe"})()
    o.containers = {}
    auth = tmp_path / "a.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    # Bypass image/build; call start_container with seeded resume fields.
    source = tmp_path / "parent-sess"
    source.mkdir()
    (source / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="continue",
        container_name="groket-restore-env",
        repo_url="",
        repo_branch="",
        repo_commit="deadbeef",
        restore_code=True,
        resume_source_dir=str(source),
        resume_session_id="parent-sess",
        resume_fork_session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    # Seed resume so start_container does not fail
    from groket.session.resume import seed_resume_into_traces_vol

    traces = o.work_dir / "traces" / cfg.container_name
    traces.mkdir(parents=True)
    seed_resume_into_traces_vol(traces, source)
    o.start_container(cfg, "img:tag", auth, gc)
    assert o._docker.run.called
    kwargs = o._docker.run.call_args.kwargs
    envs = kwargs.get("envs") or {}
    assert envs.get("RESTORE_CODE") == "1"
    assert envs.get("REPO_COMMIT") == "deadbeef"
    assert envs.get("RESUME_FORK") == "1"
    # Host checkout bind-mounted as /workspace
    vols = kwargs.get("volumes") or []
    assert any(
        str(v[1]) == "/workspace" for v in vols if isinstance(v, (list, tuple)) and len(v) >= 2
    )


def test_start_container_sets_resume_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from groket.docker import orchestrator as orch_mod
    from groket.docker.orchestrator import ContainerConfig, DockerOrchestrator

    source = _fake_session(tmp_path)
    captured: dict = {}

    class FakeDocker:
        def run(self, *a, **k):
            captured["envs"] = k.get("envs") or {}

            class C:
                id = "deadbeefdead"

            return C()

    o = DockerOrchestrator(tmp_path / "runs")
    o._docker = FakeDocker()  # type: ignore[assignment]
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[cli]\n", encoding="utf-8")
    monkeypatch.setattr(orch_mod, "share_once_py", lambda: "")
    monkeypatch.setattr(orch_mod, "entrypoint_sh", lambda: "#!/bin/bash\n")
    monkeypatch.setattr(orch_mod, "empty_setup_sh", lambda: "#!/bin/bash\n")

    cfg = ContainerConfig(
        model="v9",
        prompt="continue please",
        container_name="groket-resume-test",
        interactive=True,
        resume_source_dir=str(source),
        resume_session_id="sess-abc",
        resume_fork_session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        run_id="rid1",
    )
    o.start_container(cfg, "fake-image:tag", auth, cfg_path)
    assert captured["envs"].get("RESUME_SESSION_ID") == "sess-abc"
    assert captured["envs"].get("RESUME_FORK") == "1"
    assert captured["envs"].get("FORK_SESSION_ID") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    traces = tmp_path / "runs" / "traces" / "groket-resume-test"
    seed = traces / RESUME_SEED_DIRNAME / "%2Fworkspace" / "sess-abc"
    live = traces / "%2Fworkspace" / "sess-abc"
    assert (seed / "chat_history.jsonl").is_file()
    assert live.is_symlink()
    assert "continue please" in (traces / "groket-prompt.txt").read_text(encoding="utf-8")
    launch = json.loads((traces / LAUNCH_META_FILENAME).read_text(encoding="utf-8"))
    assert launch["resume_parent_session_id"] == "sess-abc"


def test_collect_installed_plugin_dir_aliases(tmp_path: Path) -> None:
    """Parent traces that hardcode installed-plugins paths yield aliases."""
    from groket.session.resume import collect_installed_plugin_dir_aliases

    sess = tmp_path / "sess"
    sess.mkdir()
    (sess / "chat_history.jsonl").write_text(
        json.dumps(
            {
                "role": "assistant",
                "content": "see /root/.grok/installed-plugins/src-3b9c6c63/skills/using-superpowers/SKILL.md",
            }
        )
        + "\n"
        + json.dumps(
            {
                "role": "user",
                "content": "also /root/.grok/installed-plugins/src-3b9c6c63/skills/brainstorming/",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    aliases = collect_installed_plugin_dir_aliases(sess)
    assert aliases == ["src-3b9c6c63"]


def test_collect_installed_plugin_dir_aliases_empty(tmp_path: Path) -> None:
    from groket.session.resume import collect_installed_plugin_dir_aliases

    sess = tmp_path / "empty"
    sess.mkdir()
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert collect_installed_plugin_dir_aliases(sess) == []
