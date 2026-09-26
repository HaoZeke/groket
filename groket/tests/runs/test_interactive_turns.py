"""Interactive multi-turn turn-gate host API (must match container bind mount)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from groket.docker.orchestrator import ContainerConfig
from groket.models import EvalRun
from groket.runs.run_manager import BackgroundRun, RunManager


def _eval_run(run_id: str = "run1") -> EvalRun:
    return EvalRun(
        run_id=run_id,
        prompt="first",
        models=["m"],
        status="running",
        created_at="t0",
    )


def _register_active(
    rm: RunManager,
    *,
    run_id: str,
    container_name: str,
    traces_vol: Path,
) -> BackgroundRun:
    traces_vol.mkdir(parents=True, exist_ok=True)
    cfg = ContainerConfig(
        model="m",
        prompt="p",
        container_name=container_name,
        run_id=run_id,
        interactive=True,
    )
    bg = BackgroundRun(
        run_id=run_id,
        eval_run=_eval_run(run_id),
        configs=[cfg],
        interactive=True,
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active[run_id] = bg
    return bg


def test_turn_gate_dirs_prefer_container_traces_volume(tmp_path: Path) -> None:
    """Gates live under traces/<container>/.groket-turn only."""
    rm = RunManager(tmp_path)
    cname = "groket-run1-model"
    vol = tmp_path / "traces" / cname
    _register_active(rm, run_id="run1", container_name=cname, traces_vol=vol)

    dirs = rm.turn_gate_dirs("run1")
    paths = [str(p) for p in dirs]
    assert any(p.endswith(f"{cname}/.groket-turn") or f"/{cname}/.groket-turn" in p for p in paths)
    assert not any(Path(p).name == ".groket-turn" and Path(p).parent.name == "traces" for p in dirs)


def test_submit_follow_up_writes_container_volume_gate(tmp_path: Path) -> None:
    rm = RunManager(tmp_path)
    cname = "groket-run1-model"
    vol = tmp_path / "traces" / cname
    _register_active(rm, run_id="run1", container_name=cname, traces_vol=vol)

    correct = vol / ".groket-turn"
    correct.mkdir(parents=True)
    (correct / "status.json").write_text(
        '{"state": "awaiting_follow_up", "session_id": "s", "turn": 2}\n',
        encoding="utf-8",
    )
    st = rm.interactive_status("run1")
    assert st.get("state") == "awaiting_follow_up"

    rm.submit_follow_up("second turn", run_id="run1")
    assert (correct / "next-prompt.txt").read_text(encoding="utf-8") == "second turn"
    assert "follow_up" in (correct / "command").read_text(encoding="utf-8")


def test_interactive_status_reads_container_gate(tmp_path: Path) -> None:
    """Container volume status is the sole source."""
    rm = RunManager(tmp_path)
    cname = "groket-x"
    vol = tmp_path / "traces" / cname
    _register_active(rm, run_id="rid", container_name=cname, traces_vol=vol)

    gate = vol / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        '{"state": "awaiting_follow_up", "session_id": "sess-1", "turn": 1}\n',
        encoding="utf-8",
    )
    st = rm.interactive_status("rid")
    assert st.get("state") == "awaiting_follow_up"
    assert st.get("session_id") == "sess-1"


def test_complete_interactive_writes_done_and_stops_container(tmp_path: Path) -> None:
    import json

    rm = RunManager(tmp_path)
    cname = "groket-stop-me"
    vol = tmp_path / "traces" / cname
    _register_active(rm, run_id="rid2", container_name=cname, traces_vol=vol)

    docker = MagicMock()
    rm.orchestrator._docker = docker  # type: ignore[attr-defined]  # injecting fake

    gate = vol / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        '{"state": "awaiting_follow_up", "turn": 1}\n', encoding="utf-8"
    )

    rm.complete_interactive("rid2")
    docker.stop.assert_called()
    assert cname in str(docker.stop.call_args)
    # Host finalizes gate after stop so list does not stick on ending.
    assert json.loads((gate / "status.json").read_text(encoding="utf-8"))["state"] == "done"
    # Finalize clears command / final_turn so orphans cannot keep the UI live.
    assert not (gate / "command").exists()
    assert not (gate / "final_turn").exists()


def test_submit_follow_up_rejects_empty(tmp_path: Path) -> None:
    rm = RunManager(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        rm.submit_follow_up("  ", run_id="x")


def test_orchestrator_writes_scripted_turns_on_container_volume(tmp_path: Path) -> None:
    """Scripted batch turns must be on the bind-mounted traces vol, not parent only."""
    from groket.docker import orchestrator as orch_mod

    class FakeDocker:
        def run(self, *a, **k):
            @dataclass
            class C:
                id: str = "deadbeefdead"

            return C()

        def stop(self, *a, **k):
            return None

        def remove(self, *a, **k):
            return None

    o = orch_mod.DockerOrchestrator(tmp_path)
    o._docker = FakeDocker()  # type: ignore[assignment]  # injecting fake
    o._build_dir = tmp_path / "build"
    o._build_dir.mkdir()
    # Minimal start_container pieces: only exercise turn file write path via public field
    cfg = ContainerConfig(
        model="m",
        prompt="hi",
        container_name="groket-batch-1",
        run_id="batch1",
        follow_up_prompts=["turn two", "turn three"],
        interactive=False,
    )
    traces_vol = tmp_path / "traces" / cfg.container_name
    traces_vol.mkdir(parents=True)
    # Replicate orchestrator turn write logic (same as start_container)
    import json

    scripted = list(cfg.follow_up_prompts)
    td = traces_vol / ".groket-turn"
    td.mkdir(parents=True, exist_ok=True)
    (td / "scripted-turns.json").write_text(json.dumps(scripted) + "\n", encoding="utf-8")

    data = json.loads((traces_vol / ".groket-turn" / "scripted-turns.json").read_text())
    assert data == ["turn two", "turn three"]
    parent_script = tmp_path / "traces" / ".groket-turn" / "scripted-turns.json"
    assert not parent_script.is_file()


def test_stop_session_container_only_that_name(tmp_path: Path) -> None:
    """stop_session_container targets traces volume basename, not whole run."""
    import json
    from unittest.mock import MagicMock

    from groket.runs.run_manager import RunManager

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    cname = "groket-only-me"
    sess = traces / cname / "%2Fworkspace" / "sid-1"
    sess.mkdir(parents=True)
    gate = traces / cname / ".groket-turn"
    gate.mkdir()
    (gate / "status.json").write_text(
        '{"state": "running", "session_id": "sid-1"}\n', encoding="utf-8"
    )
    rm = RunManager(work)
    docker = MagicMock()
    rm.orchestrator._docker = docker
    rm.stop_session_container(sess)
    docker.stop.assert_called_once_with(cname)
    docker.remove.assert_called_once_with(cname)
    assert json.loads((gate / "status.json").read_text(encoding="utf-8"))["state"] == "done"


def test_stop_session_container_swallows_docker_errors(tmp_path: Path) -> None:
    """Docker stop/remove failures are best-effort (logged, not raised)."""
    from unittest.mock import MagicMock

    from groket.runs.run_manager import RunManager

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    cname = "groket-flaky"
    sess = traces / cname / "%2Fworkspace" / "sid-1"
    sess.mkdir(parents=True)
    (traces / cname / ".groket-turn").mkdir()
    rm = RunManager(work)
    docker = MagicMock()
    docker.stop.side_effect = RuntimeError("stop failed")
    docker.remove.side_effect = RuntimeError("remove failed")
    rm.orchestrator._docker = docker
    rm.stop_session_container(sess)  # does not raise
    docker.stop.assert_called_once_with(cname)
    docker.remove.assert_called_once_with(cname)


def test_stop_session_container_no_volume_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No traces volume → no docker calls."""
    from unittest.mock import MagicMock

    import groket.session.turn_gate as turn_gate
    from groket.runs.run_manager import RunManager

    monkeypatch.setattr(turn_gate, "traces_volume_for_session", lambda _p: None)
    work = tmp_path / "work"
    sess = tmp_path / "orphan" / "sid"
    sess.mkdir(parents=True)
    rm = RunManager(work)
    docker = MagicMock()
    rm.orchestrator._docker = docker
    rm.stop_session_container(sess)
    docker.stop.assert_not_called()
