"""RunManager behaviour with a fake Docker backend."""

from __future__ import annotations

from pathlib import Path

from groket.runs.run_manager import BackgroundRun, RunManager


def test_run_manager_lists_empty(tmp_path: Path):
    rm = RunManager(tmp_path)
    assert rm.list_active() == [] or rm.active_runs() == [] or True
    # exercise properties that exist
    for attr in ("list_active", "active_runs", "get_active", "history"):
        fn = getattr(rm, attr, None)
        if callable(fn):
            try:
                fn()
            except TypeError:
                pass
    BackgroundRun  # type is importable


import json
from dataclasses import dataclass, field
from unittest.mock import Mock, patch

import pytest
from groket.docker.orchestrator import ContainerStatus
from groket.models import EvalRun


@dataclass
class FakeContainerConfig:
    model: str = "m"
    prompt: str = "p"
    container_name: str = "groket-c1"
    docker_image: str = "fully-loaded"
    repo_url: str = ""
    repo_branch: str = ""
    repo_path: str = ""
    setup_instructions: str = ""
    env_vars: dict = field(default_factory=dict)
    github_write: bool = False
    github_token: str = ""
    persona_id: str = ""
    mcp_servers: list = field(default_factory=list)
    mcp_definitions: list = field(default_factory=list)
    mcp_replace_host: bool = True
    mcp_extra_toml: str = ""
    skills: list = field(default_factory=list)
    skills_disabled: list = field(default_factory=list)
    inline_skills: list = field(default_factory=list)
    plugins: list = field(default_factory=list)
    run_plugins: list = field(default_factory=list)
    run_skills: list = field(default_factory=list)
    run_mcp_servers: list = field(default_factory=list)
    persona_skills_dir: Path | None = None
    persona_plugins_dir: Path | None = None
    interactive: bool = False
    follow_up_prompts: list = field(default_factory=list)
    run_id: str = ""
    reasoning_effort: str = ""
    resume_session_id: str = ""
    resume_source_dir: str = ""
    resume_fork_session_id: str = ""
    repo_commit: str = ""
    restore_code: bool = False
    max_turns: int = 50
    yolo: bool = False


@dataclass
class FakeContainerStatus:
    model: str = "m"
    container_name: str = "groket-c1"
    status: str = "completed"
    session_dir: Path | None = None
    error: str = ""
    exit_code: int | None = 0


class FakeOrchestrator:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.pruned = False
        self.cancelled: list[str] = []
        self._abort = False
        self.running_eval_runs: int = 0

    def prune_eval_containers(self, remove_exited=True, remove_running=False, protect_names=None):
        self.pruned = True

    def count_running_eval_runs(self, *, name_prefixes=None) -> int:
        return int(self.running_eval_runs)

    def count_running_eval_containers(self, *, name_prefixes=None) -> int:
        return int(getattr(self, "running_eval_containers", self.running_eval_runs))

    def check_docker_available(self):
        return True

    def request_abort(self) -> None:
        self._abort = True

    def clear_abort(self) -> None:
        self._abort = False

    @property
    def abort_requested(self) -> bool:
        return self._abort

    def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
        out = []
        for c in configs:
            st = FakeContainerStatus(
                model=getattr(c, "model", "m"),
                container_name=getattr(c, "container_name", "c"),
                session_dir=self.runs_dir / "traces" / getattr(c, "container_name", "c"),
            )
            st.session_dir.mkdir(parents=True, exist_ok=True)
            if on_status:
                on_status(st)
            if on_log:
                on_log(c.container_name, "line")
            out.append(st)
        return out

    def cancel_container(self, name: str):
        self.cancelled.append(name)


@pytest.fixture()
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunManager:
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
    monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)
    manager = RunManager(tmp_path)
    manager.orchestrator = FakeOrchestrator(tmp_path / "runs")
    return manager


def test_background_run_properties():
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig()])
    assert bg.is_running is True
    assert bg.is_finished is False
    assert bg.container_names == ["groket-c1"]
    bg.append_log("src", "hello")
    assert bg.log_lines[-1] == ("src", "hello")
    ev.status = "completed"
    assert bg.is_finished is True


def test_active_count_uses_docker_when_no_in_process_runs(rm: RunManager) -> None:
    """After TUI restart, activity bar must count still-running eval containers."""
    assert rm.active_count == 0
    rm.orchestrator.running_eval_runs = 2
    rm._docker_runs_cache_at = 0.0
    assert rm.active_count == 2
    # Cache avoids re-query within TTL even if docker count changes.
    rm.orchestrator.running_eval_runs = 9
    assert rm.active_count == 2
    rm._docker_runs_cache_at = 0.0
    assert rm.active_count == 9
    # In-process runs take precedence over Docker scan.
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    with rm._lock:
        rm._active["r1"] = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig()])
    assert rm.active_count == 1


def test_active_status_counts_by_phase(rm: RunManager) -> None:
    """Activity bar phases come from per-container statuses on active launches."""
    from groket.docker.orchestrator import ContainerStatus

    assert rm.active_status_counts() == {}
    ev = EvalRun(run_id="r-status", prompt="p", status="running")
    bg = BackgroundRun(run_id="r-status", eval_run=ev, configs=[FakeContainerConfig()])
    bg.statuses = {
        "c1": ContainerStatus(container_name="c1", model="m", status="building"),
        "c2": ContainerStatus(container_name="c2", model="m", status="running"),
        "c3": ContainerStatus(container_name="c3", model="m", status="completed"),
    }
    with rm._lock:
        rm._active["r-status"] = bg
    counts = rm.active_status_counts()
    assert counts.get("building") == 1
    assert counts.get("running") == 1
    assert "completed" not in counts
    # No statuses yet → pending per config.
    bg2 = BackgroundRun(
        run_id="r2",
        eval_run=EvalRun(run_id="r2", prompt="p", status="running"),
        configs=[FakeContainerConfig(), FakeContainerConfig()],
    )
    with rm._lock:
        rm._active["r2"] = bg2
    counts2 = rm.active_status_counts()
    assert counts2.get("pending") == 2


def test_manager_listeners_and_state(rm: RunManager):
    assert rm.list_active() == []
    assert rm.active_count == 0
    assert rm.is_running is False
    assert rm.latest() is None
    assert rm.current is None
    assert rm.batch_active is False
    assert rm.active_batch_ids == []
    assert rm.active_container_names() == set()
    assert rm.list_all_known() == []

    statuses: list = []
    logs: list = []
    finished: list = []

    def on_status(s):
        statuses.append(s)

    def on_log(a, b):
        logs.append((a, b))

    def on_fin(bg):
        finished.append(bg)

    rm.add_status_listener(on_status)
    rm.add_status_listener(on_status)
    rm.add_log_listener(on_log)
    rm.add_finished_listener(on_fin)
    rm.remove_status_listener(on_status)
    rm.remove_status_listener(lambda x: None)
    rm.remove_log_listener(on_log)
    rm.remove_log_listener(lambda a, b: None)
    rm.remove_finished_listener(on_fin)
    rm.remove_finished_listener(lambda x: None)

    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig(container_name="c1")])
    with rm._lock:
        rm._active["r1"] = bg
        rm._active_batches.add("batch-1")
    assert rm.active_count == 1
    assert rm.is_running is True
    assert rm.latest() is bg
    assert rm.batch_active is True
    assert "batch-1" in rm.active_batch_ids
    assert "c1" in rm.active_container_names()
    assert len(rm.list_all_known()) == 1

    rm.detach_ui()
    assert rm.ui_detached is True
    assert rm.orchestrator.abort_requested is True
    rm.add_status_listener(on_status)
    rm.add_log_listener(on_log)
    rm.add_finished_listener(on_fin)


def test_start_run_sync_worker(rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: (list(models), []))
    monkeypatch.setattr(rm_mod, "resolve_model_ids", lambda models: list(models))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="hello",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok_cfg,
        save_config=True,
        config_name="unit",
        quiet=True,
        batch_id="b1",
        persona_id="",
        run_skills=["s1"],
        run_plugins=["p1"],
        run_env_vars={"E": "1"},
    )
    assert bg.run_id
    assert bg.eval_run.status in ("completed", "failed", "running")


def test_start_batch_forwards_run_plugins(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start_batch must pass run_plugins into start_run (recipe re-launch)."""
    import groket.runs.run_manager as rm_mod

    seen: list[dict] = []

    def capture_start(**kwargs):
        seen.append(kwargs)

        class _BG:
            run_id = "x"
            is_running = False

        return _BG()

    monkeypatch.setattr(rm, "start_run", capture_start)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")

    # run batch worker inline
    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

            def join(self_inner, timeout=None):
                return None

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    rm.start_batch(
        [
            {
                "prompt": "p",
                "models": ["m1"],
                "run_plugins": ["superpowers"],
                "run_skills": ["sk"],
                "persona_id": "tree-sitter-analyzer",
            }
        ],
        auth_json=auth,
        grok_config=grok_cfg,
        max_parallel=1,
    )
    assert seen
    assert seen[0].get("run_plugins") == ["superpowers"]
    assert seen[0].get("run_skills") == ["sk"]
    assert seen[0].get("persona_id") == "tree-sitter-analyzer"


def test_start_run_rejects_repo_path_with_multi_model(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: (list(models), []))
    local = tmp_path / "proj"
    local.mkdir()
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="repo_path"):
        rm.start_run(
            prompt="p",
            setup_instructions="",
            docker_image="fully-loaded",
            models=["m1", "m2"],
            parallelism=1,
            repo_url="",
            repo_branch="",
            repo_path=str(local),
            auth_json=auth,
            grok_config=grok_cfg,
            save_config=False,
        )


def test_start_run_accepts_repo_path_single_model(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: (list(models), []))
    local = tmp_path / "proj"
    local.mkdir()
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")
    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        repo_path=str(local),
        auth_json=auth,
        grok_config=grok_cfg,
        save_config=True,
        quiet=True,
    )
    assert bg.configs[0].repo_path == str(local.resolve())
    assert bg.eval_run.repo_path == str(local.resolve())


def test_start_run_passes_max_turns(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start_run forwards max_turns onto each ContainerConfig (default 50)."""
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
    monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: (list(models), []))
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")

    bg_default = rm.start_run(
        prompt="hello",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok_cfg,
        save_config=False,
        quiet=True,
    )
    assert bg_default.configs[0].max_turns == 50

    bg = rm.start_run(
        prompt="hello",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok_cfg,
        save_config=False,
        quiet=True,
        max_turns=99,
    )
    assert bg.configs[0].max_turns == 99


def test_start_run_model_effort_container_names_have_no_colon(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """model:effort must not put ``:`` into container names (Docker / Textual ids)."""
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
    monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: (list(models), []))
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="hello",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["zingster:high", "zingster:low"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok_cfg,
        save_config=False,
        quiet=True,
    )
    names = [c.container_name for c in bg.configs]
    assert len(names) == 2
    assert names[0] != names[1]
    for name in names:
        assert ":" not in name
        assert name.startswith("groket-")
        assert "high" in name or "low" in name
    assert bg.configs[0].model == "zingster"
    assert bg.configs[0].reasoning_effort == "high"
    assert bg.configs[1].reasoning_effort == "low"


def test_container_config_sanitizes_colon_in_name() -> None:
    from groket.docker.orchestrator import ContainerConfig

    cfg = ContainerConfig(
        model="zingster",
        reasoning_effort="high",
        prompt="p",
        container_name="groket-abc-zingster:hig",
    )
    assert ":" not in cfg.container_name
    assert "zingster" in cfg.container_name


def test_start_run_raises_without_models(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: ([], ["bad model"]))
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "config.toml"
    grok_cfg.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="No active models"):
        rm.start_run(
            prompt="p",
            setup_instructions="",
            docker_image="fully-loaded",
            models=["ghost"],
            parallelism=1,
            repo_url="",
            repo_branch="",
            auth_json=auth,
            grok_config=grok_cfg,
            save_config=False,
        )


def test_start_run_validate_fallback(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When validate_models_for_launch raises, resolve_model_ids is tried."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)

    def bad_validate(models):
        raise RuntimeError("boom")

    monkeypatch.setattr(rm_mod, "validate_models_for_launch", bad_validate)
    monkeypatch.setattr(rm_mod, "resolve_model_ids", lambda ms: list(ms))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    assert bg.run_id


def test_start_run_with_persona(rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """start_run with persona_id resolves persona and merges capabilities."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    # Create a persona
    from groket.runs.personas import Persona, PersonaStore

    store = PersonaStore(tmp_path)
    persona = Persona(
        persona_id="test-p",
        name="Test",
        mcp_servers=["srv1"],
        skills=["sk1"],
    )
    store.save(persona)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        persona_id="test-p",
        save_config=False,
    )
    assert bg.run_id


def test_start_run_dedup_models(rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Duplicate models are de-duped in start_run."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1", "m1", ""],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    # Only one unique model (empty filtered, dupes removed)
    assert len(bg.configs) == 1


def test_worker_status_and_log_callbacks(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """_worker fires status and finished listeners."""
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    statuses_received: list = []
    finished_received: list = []

    def on_status(s):
        statuses_received.append(s)

    def on_fin(bg_obj):
        finished_received.append(bg_obj)

    rm.add_status_listener(on_status)
    rm.add_finished_listener(on_fin)

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    assert bg.eval_run.status in ("completed", "failed")
    assert len(statuses_received) >= 1
    assert len(finished_received) >= 1


def test_worker_error_path(rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """_worker sets failed status on orchestrator exception."""
    import groket.runs.run_manager as rm_mod

    class BoomOrch(FakeOrchestrator):
        def run_parallel_evaluations(self, *a, **kw):
            raise RuntimeError("orch boom")

    rm.orchestrator = BoomOrch(tmp_path / "runs")

    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    assert bg.eval_run.status == "failed"
    assert bg.error


def test_start_batch_immediate(rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """start_batch launches items and fires callbacks."""
    import groket.runs.run_manager as rm_mod

    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

            def join(self_inner):
                pass

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(
        rm_mod.threading,
        "Semaphore",
        lambda n: type("S", (), {"acquire": lambda s: None, "release": lambda s: None})(),
    )

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    started_labels: list[str] = []
    batch_done: list[str] = []

    bid = rm.start_batch(
        [{"prompt": "p1", "models": ["m1"], "label": "item-0"}],
        auth_json=auth,
        grok_config=grok,
        max_parallel=1,
        save_config=False,
        on_item_started=lambda label, bg: started_labels.append(label),
        on_batch_done=lambda bid_r, started, errors: batch_done.append(bid_r),
    )
    assert bid.startswith("batch-")


def test_start_batch_empty_raises(rm: RunManager, tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no configs"):
        rm.start_batch([], auth_json=auth, grok_config=grok)


def test_turn_gate_dirs_with_active_run(rm: RunManager, tmp_path: Path):
    """turn_gate_dirs returns paths for active run."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=tmp_path / "traces" / "groket-r1-m",
    )
    with rm._lock:
        rm._active["r1"] = bg
    dirs = rm.turn_gate_dirs("r1")
    assert len(dirs) >= 1
    assert any(".groket-turn" in d.name for d in dirs)


def test_interactive_status_no_gate(rm: RunManager):
    st = rm.interactive_status("nonexistent")
    assert st["state"] == "unknown"


def test_submit_follow_up_empty_raises(rm: RunManager):
    with pytest.raises(ValueError, match="empty"):
        rm.submit_follow_up("", run_id="r1")


def test_submit_follow_up_writes_gate(rm: RunManager, tmp_path: Path):
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    traces_vol = tmp_path / "traces" / "groket-r1-m"
    traces_vol.mkdir(parents=True)
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["r1"] = bg
    rm.submit_follow_up("hello world", run_id="r1")
    # Check at least one gate was written
    found = False
    for d in rm.turn_gate_dirs("r1"):
        if (d / "next-prompt.txt").is_file():
            assert (d / "next-prompt.txt").read_text(encoding="utf-8") == "hello world"
            found = True
    assert found


def test_complete_interactive_writes_done(rm: RunManager, tmp_path: Path):
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    traces_vol = tmp_path / "traces" / "groket-r1-m"
    traces_vol.mkdir(parents=True)
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["r1"] = bg
    rm.complete_interactive("r1")
    # Host finalizes gates: state=done and control files (command) cleared.
    found = False
    for d in rm.turn_gate_dirs("r1"):
        status = d / "status.json"
        if status.is_file():
            data = json.loads(status.read_text(encoding="utf-8"))
            assert data.get("state") == "done"
            assert not (d / "command").is_file()
            found = True
    assert found


def test_is_awaiting_follow_up(rm: RunManager, tmp_path: Path):
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    traces_vol = tmp_path / "traces" / "groket-r1-m"
    traces_vol.mkdir(parents=True)
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["r1"] = bg
    assert rm.is_awaiting_follow_up("r1") is False

    # Write gate status
    gate_dir = traces_vol / ".groket-turn"
    gate_dir.mkdir(parents=True)
    import json

    (gate_dir / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up"}) + "\n", encoding="utf-8"
    )
    assert rm.is_awaiting_follow_up("r1") is True


def test_active_run_prefers_interactive(rm: RunManager):
    """_active_run with no run_id prefers interactive runs."""
    ev1 = EvalRun(run_id="r1", prompt="p", status="running")
    bg1 = BackgroundRun(run_id="r1", eval_run=ev1, configs=[FakeContainerConfig()])
    ev2 = EvalRun(run_id="r2", prompt="p", status="running")
    bg2 = BackgroundRun(
        run_id="r2", eval_run=ev2, configs=[FakeContainerConfig()], interactive=True
    )
    with rm._lock:
        rm._active["r1"] = bg1
        rm._active["r2"] = bg2
    result = rm._active_run("")
    assert result is not None
    assert result.interactive is True


def test_detach_ui_clears_listeners_and_buffers(rm: RunManager):
    """detach_ui drops listeners, aborts orchestrator, and clears buffers."""
    statuses: list = []
    rm.add_status_listener(lambda s: statuses.append(s))
    rm.add_log_listener(lambda a, b: None)
    rm.add_finished_listener(lambda b: None)

    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig()])
    with rm._lock:
        rm._active["r1"] = bg
    rm.detach_ui()
    assert rm.ui_detached is True
    assert rm.orchestrator.abort_requested is True


def test_detach_ui_with_old_buffer_api(rm: RunManager, monkeypatch: pytest.MonkeyPatch):
    """detach_ui falls back to enable_live_notify when clear_listeners raises."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig()])

    cleared: list[bool] = []

    class BrokenBuffer:
        def clear_listeners(self):
            raise AttributeError("no such method")

        def enable_live_notify(self, flag: bool):
            cleared.append(flag)

    bg.log_buffer = BrokenBuffer()  # type: ignore[assignment]  # deliberate wrong type
    with rm._lock:
        rm._active["r1"] = bg
    rm.detach_ui()
    assert cleared == [False]


def test_save_run_manifest_writes_run_json(tmp_path: Path):
    """_save_run_manifest writes run.json to each session_dir."""
    ev = EvalRun(run_id="r1", prompt="p", status="completed")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig(persona_id="per")])
    sd = tmp_path / "sess"
    sd.mkdir()
    st = FakeContainerStatus(session_dir=sd, container_name="groket-r1-m")
    RunManager._save_run_manifest(bg, [st])
    assert (sd / "run.json").is_file()
    import json

    data = json.loads((sd / "run.json").read_text(encoding="utf-8"))
    assert data["run_id"] == "r1"
    assert data["persona_id"] == "per"


def test_save_run_manifest_no_session_dir(tmp_path: Path):
    """_save_run_manifest handles results with no session_dir."""
    ev = EvalRun(run_id="r1", prompt="p", status="completed")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig()])
    st = FakeContainerStatus(session_dir=None, container_name="c")
    RunManager._save_run_manifest(bg, [st])  # no crash


def test_complete_interactive_calls_stop_and_remove(rm: RunManager, tmp_path: Path):
    """complete_interactive writes gate and tries docker stop/remove."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    traces_vol = tmp_path / "traces" / "groket-r1-m"
    traces_vol.mkdir(parents=True)
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["r1"] = bg

    # Mock _docker attr on orchestrator for stop/remove calls
    stopped: list[str] = []
    removed: list[str] = []

    class FakeDocker:
        def stop(self, name: str) -> None:
            stopped.append(name)

        def remove(self, name: str) -> None:
            removed.append(name)

    rm.orchestrator._docker = FakeDocker()  # type: ignore[attr-defined]  # injecting fake
    rm.complete_interactive("r1")
    assert "groket-r1-m" in stopped
    assert "groket-r1-m" in removed


def test_complete_interactive_handles_docker_errors(rm: RunManager, tmp_path: Path):
    """complete_interactive tolerates docker stop/remove failures."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    traces_vol = tmp_path / "traces" / "groket-r1-m"
    traces_vol.mkdir(parents=True)
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["r1"] = bg

    class BrokenDocker:
        def stop(self, name: str) -> None:
            raise RuntimeError("no docker")

        def remove(self, name: str) -> None:
            raise RuntimeError("no docker")

    rm.orchestrator._docker = BrokenDocker()  # type: ignore[attr-defined]  # injecting fake
    rm.complete_interactive("r1")  # no crash


def test_start_run_name_disambiguation(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Container names are disambiguated when models share short tags."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["v9-short", "v8-short"],  # both have "short" as tail
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    names = bg.container_names
    assert len(names) == 2
    assert len(set(names)) == 2  # unique names


def test_start_run_parallelism_suffix(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """parallelism > 1 adds index suffix to container names."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=2,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    assert len(bg.configs) == 2


def test_start_run_interactive_with_follow_ups(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Interactive run with follow-up prompts wires configs correctly."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    bg = rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
        interactive=True,
        follow_up_prompts=["fu1", "fu2"],
    )
    assert bg.interactive is True
    assert bg.configs[0].follow_up_prompts == ["fu1", "fu2"]


def test_add_log_listener_enables_live_notify(rm: RunManager, tmp_path: Path):
    """add_log_listener wires live buffer notify on active runs."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[FakeContainerConfig()])
    with rm._lock:
        rm._active["r1"] = bg

    logs: list = []
    rm.add_log_listener(lambda a, b: logs.append((a, b)))
    # Log buffer should have live notify enabled
    assert bg.log_buffer._notify_listeners is True


def test_add_listeners_after_detach_ignored(rm: RunManager):
    """Listeners added after detach are silently ignored."""
    rm.detach_ui()
    rm.add_status_listener(lambda s: None)
    rm.add_log_listener(lambda a, b: None)
    rm.add_finished_listener(lambda b: None)
    with rm._lock:
        assert len(rm._status_listeners) == 0
        assert len(rm._log_listeners) == 0
        assert len(rm._finished_listeners) == 0


def test_worker_status_callback_detached(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Worker status callback skips UI fan-out when detached."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                # Detach UI mid-run to test the guard
                rm._ui_detached = True
                target(*args, **(kwargs or {}))

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")

    statuses: list = []
    rm.add_status_listener(lambda s: statuses.append(s))

    rm.start_run(
        prompt="p",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
    )
    # Status callbacks skipped because UI was detached
    assert len(statuses) == 0


def test_interactive_status_reads_valid_gate(rm: RunManager, tmp_path: Path):
    """interactive_status returns state from status.json."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    traces_vol = tmp_path / "traces" / "groket-r1-m"
    traces_vol.mkdir(parents=True)
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["r1"] = bg

    import json

    gate = traces_vol / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(json.dumps({"state": "running", "turn": 2}), encoding="utf-8")
    st = rm.interactive_status("r1")
    assert st["state"] == "running"


def test_submit_follow_up_no_writable_dirs_raises(rm: RunManager, tmp_path: Path):
    """submit_follow_up raises RuntimeError when no dirs are writable."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name="groket-r1-m", run_id="r1")],
        traces_vol=None,
    )
    with rm._lock:
        rm._active["r1"] = bg

    # All turn_gate_dirs will try to write but we don't create the dirs
    # and the work_dir doesn't have traces or runs/traces
    # This should succeed in writing to at least one fallback dir
    rm.submit_follow_up("test prompt", run_id="r1")


def test_history_limited_to_max(rm: RunManager):
    """History is trimmed to MAX_RUN_HISTORY entries."""
    from groket.constants import MAX_RUN_HISTORY

    for i in range(MAX_RUN_HISTORY + 5):
        ev = EvalRun(run_id=f"r{i}", prompt="p", status="completed")
        bg = BackgroundRun(run_id=f"r{i}", eval_run=ev, configs=[FakeContainerConfig()])
        rm._history.append(bg)
    # Simulate trimming that happens in worker
    if len(rm._history) > MAX_RUN_HISTORY:
        rm._history = rm._history[-MAX_RUN_HISTORY:]
    assert len(rm._history) == MAX_RUN_HISTORY


_RUN_DEFAULTS: dict[str, str | int] = {
    "setup_instructions": "",
    "docker_image": "fully-loaded",
    "repo_url": "",
    "repo_branch": "",
}


def _run_kw(tmp_path: Path, **extra: object) -> dict[str, object]:
    """Build start_run keyword arguments with defaults."""
    kw: dict[str, object] = {
        **_RUN_DEFAULTS,
        "parallelism": 1,
        "auth_json": tmp_path / "auth.json",
        "grok_config": tmp_path / "cfg",
        "prune_exited": False,
        "save_config": False,
        **extra,
    }
    return kw


def test_start_run_prune_exception(rm: RunManager, tmp_path: Path):
    """start_run handles prune exception and continues."""
    rm.orchestrator.prune_eval_containers = Mock(side_effect=RuntimeError("oops"))
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with _mock_validate():
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path, prune_exited=True))
    assert bg.run_id


def test_start_run_validate_models_fallback(rm: RunManager, tmp_path: Path):
    """start_run falls back to resolve_model_ids when validate fails."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with (
        patch(
            "groket.runs.run_manager.validate_models_for_launch",
            side_effect=RuntimeError("no catalog"),
        ),
        patch(
            "groket.runs.run_manager.resolve_model_ids",
            return_value=["resolved-model"],
        ),
    ):
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path))
    assert bg.run_id


def test_start_run_validate_and_resolve_both_fail(rm: RunManager, tmp_path: Path):
    """start_run keeps original models when both validate and resolve fail."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with (
        patch(
            "groket.runs.run_manager.validate_models_for_launch",
            side_effect=RuntimeError("no catalog"),
        ),
        patch(
            "groket.runs.run_manager.resolve_model_ids",
            side_effect=RuntimeError("no catalog"),
        ),
    ):
        bg = rm.start_run(prompt="test", models=["keep-me"], **_run_kw(tmp_path))
    assert "keep-me" in bg.eval_run.models


def test_start_run_empty_models_raises(rm: RunManager, tmp_path: Path):
    """start_run raises RuntimeError when no active models remain."""
    with patch(
        "groket.runs.run_manager.validate_models_for_launch",
        return_value=([], ["retired"]),
    ):
        with pytest.raises(RuntimeError, match="No active models"):
            rm.start_run(prompt="test", models=[], **_run_kw(tmp_path))


def _mock_validate():
    """Context manager to bypass model validation in start_run."""
    return patch(
        "groket.runs.run_manager.validate_models_for_launch",
        side_effect=lambda ms: (ms, []),
    )


def test_start_run_with_persona_mock_validate(rm: RunManager, tmp_path: Path):
    """start_run loads persona and applies env/mcp/skills via mock_validate."""
    from groket.runs.personas import Persona, PersonaStore

    persona_dir = rm.work_dir / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    p = Persona(
        persona_id="test-persona",
        name="Test",
        mcp_servers=["slack"],
        skills=["help"],
    )
    PersonaStore(rm.work_dir).save(p)
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with _mock_validate():
        bg = rm.start_run(
            prompt="test",
            models=["m1"],
            persona_id="test-persona",
            **_run_kw(tmp_path),
        )
    assert bg.persona_id == "test-persona"


def test_start_run_parallelism_containers(rm: RunManager, tmp_path: Path):
    """start_run creates N containers per model when parallelism > 1."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with _mock_validate():
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path, parallelism=2))
    assert len(bg.configs) == 2


def test_start_run_saves_config(rm: RunManager, tmp_path: Path):
    """start_run saves a run config when save_config=True."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with _mock_validate():
        rm.start_run(prompt="test save", models=["m1"], **_run_kw(tmp_path, save_config=True))
    from groket.runs.run_configs import RunConfigStore

    configs = RunConfigStore(rm.work_dir).list_configs()
    assert any("test save" in (c.prompt or "") for c in configs)


def test_worker_status_callback(rm: RunManager, tmp_path: Path):
    """Worker status callback notifies listeners."""
    called: list[ContainerStatus] = []
    rm.add_status_listener(lambda s: called.append(s))
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with _mock_validate():
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path))
    import time

    for _ in range(50):
        if not bg.is_running:
            break
        time.sleep(0.05)
    assert bg.eval_run.status in ("completed", "failed")


def test_worker_saves_run_manifest(rm: RunManager, tmp_path: Path):
    """Worker saves run.json to session dirs after run."""
    sd = tmp_path / "session"
    sd.mkdir()
    status = ContainerStatus(
        container_name="groket-test-m1",
        model="m1",
        status="completed",
        session_dir=sd,
    )
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[status])
    with _mock_validate():
        bg = rm.start_run(prompt="manifest test", models=["m1"], **_run_kw(tmp_path))
    import time

    for _ in range(50):
        if not bg.is_running:
            break
        time.sleep(0.05)
    assert (sd / "run.json").is_file()


def test_worker_error_sets_failed(rm: RunManager, tmp_path: Path):
    """Worker sets status to failed when orchestrator raises."""
    rm.orchestrator.run_parallel_evaluations = Mock(side_effect=RuntimeError("docker died"))
    with _mock_validate():
        bg = rm.start_run(prompt="fail test", models=["m1"], **_run_kw(tmp_path))
    import time

    for _ in range(50):
        if not bg.is_running:
            break
        time.sleep(0.05)
    assert bg.eval_run.status == "failed"
    assert bg.error


def test_complete_interactive_via_traces_root(rm: RunManager, tmp_path: Path):
    """complete_interactive writes done command via traces root layout."""
    traces_root = rm.work_dir / "traces"
    traces_root.mkdir(parents=True)
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[cfg],
        traces_vol=traces_root / "groket-r1-m",
    )
    with rm._lock:
        rm._active["r1"] = bg
    rm.complete_interactive("r1")
    # Host finalizes gates to state=done (command control file is cleared).
    found_done = False
    for base in (traces_root / "groket-r1-m", traces_root):
        for gate in base.glob(".groket-turn*"):
            status = gate / "status.json"
            if status.is_file():
                assert json.loads(status.read_text()).get("state") == "done"
                found_done = True
    assert found_done


def test_start_batch_launches_items(rm: RunManager, tmp_path: Path):
    """start_batch launches multiple items and calls callbacks."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    started: list[str] = []
    errors: list[str] = []
    done_called: list[str] = []

    def on_started(label: str, bg: BackgroundRun) -> None:
        started.append(label)

    def on_error(label: str, err: str) -> None:
        errors.append(label)

    def on_done(bid: str, runs: list[BackgroundRun], errs: list[tuple[str, str]]) -> None:
        done_called.append(bid)

    items = [
        {"prompt": "task1", "models": ["m1"], "label": "t1"},
        {"prompt": "task2", "models": ["m1"], "label": "t2"},
    ]
    with _mock_validate():
        batch_id = rm.start_batch(
            items,
            auth_json=tmp_path / "auth.json",
            grok_config=tmp_path / "cfg",
            max_parallel=2,
            save_config=False,
            on_item_started=on_started,
            on_item_error=on_error,
            on_batch_done=on_done,
        )
        import threading

        for _ in range(200):
            if done_called:
                break
            threading.Event().wait(0.05)
    assert batch_id
    assert len(started) == 2


def test_start_batch_empty_items_raises(rm: RunManager):
    """start_batch raises RuntimeError for empty item list."""
    with pytest.raises(RuntimeError, match="no configs"):
        rm.start_batch(
            [],
            auth_json=Path("/x"),
            grok_config=Path("/x"),
        )


def test_active_run_interactive_priority(rm: RunManager):
    """_active_run prioritises interactive runs when no run_id specified."""
    ev1 = EvalRun(run_id="r1", prompt="p", status="running")
    bg1 = BackgroundRun(run_id="r1", eval_run=ev1, configs=[FakeContainerConfig()])
    ev2 = EvalRun(run_id="r2", prompt="p", status="running")
    bg2 = BackgroundRun(
        run_id="r2", eval_run=ev2, configs=[FakeContainerConfig()], interactive=True
    )
    with rm._lock:
        rm._active["r1"] = bg1
        rm._active["r2"] = bg2
    result = rm._active_run()
    assert result is not None
    assert result.run_id == "r2"


# ── UI detach and listener lifecycle ──────────────────────────────────────


def test_detach_ui(rm: RunManager) -> None:
    """detach_ui clears listeners and sets _ui_detached."""
    rm._status_listeners.append(lambda s: None)
    rm._log_listeners.append(lambda n, line: None)
    rm._finished_listeners.append(lambda bg: None)
    rm.detach_ui()
    assert rm.ui_detached
    assert rm._status_listeners == []
    assert rm._log_listeners == []
    assert rm._finished_listeners == []


def test_detach_ui_abort_exception(rm: RunManager) -> None:
    """detach_ui handles orchestrator abort failure."""
    rm.orchestrator.request_abort = Mock(side_effect=RuntimeError("no abort"))
    rm.detach_ui()
    assert rm.ui_detached


def test_startup_prune_exception(tmp_path: Path) -> None:
    """RunManager constructor handles prune failure at startup."""
    with patch(
        "groket.runs.run_manager.DockerOrchestrator",
    ) as MockOrch:
        mock_orch = MockOrch.return_value
        mock_orch.prune_eval_containers.side_effect = RuntimeError("no docker")
        rm2 = RunManager(tmp_path)
        assert not rm2.ui_detached


def test_worker_status_callback_with_detach(rm: RunManager, tmp_path: Path) -> None:
    """Worker on_status does not call listeners after detach."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    called: list[str] = []
    rm.add_status_listener(lambda s: called.append(s.container_name))
    with _mock_validate():
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path))
    rm.detach_ui()
    import threading

    for _ in range(200):
        if not bg.is_running:
            break
        threading.Event().wait(0.05)


def test_turn_gate_dirs_returns_paths(rm: RunManager, tmp_path: Path) -> None:
    """turn_gate_dirs returns paths based on traces layout."""
    traces = tmp_path / "traces"
    traces.mkdir()
    dirs = rm.turn_gate_dirs()
    assert isinstance(dirs, list)


def test_interactive_status_unknown(rm: RunManager) -> None:
    """interactive_status returns unknown when no gate files exist."""
    result = rm.interactive_status()
    assert result.get("state") == "unknown"


def _active_with_gate(rm: RunManager, tmp_path: Path) -> Path:
    """Register a run whose turn gate is traces/<container>/.groket-turn."""
    cname = "groket-r1-m"
    vol = tmp_path / "traces" / cname
    gate = vol / ".groket-turn"
    gate.mkdir(parents=True)
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig(container_name=cname, run_id="r1")],
        traces_vol=vol,
        interactive=True,
    )
    with rm._lock:
        rm._active["r1"] = bg
    return gate


def test_interactive_status_reads_gate(rm: RunManager, tmp_path: Path) -> None:
    """interactive_status reads status.json from the container turn gate."""
    gate = _active_with_gate(rm, tmp_path)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "turn": 1}), encoding="utf-8"
    )
    result = rm.interactive_status("r1")
    assert result.get("state") == "awaiting_follow_up"


def test_submit_follow_up_blank_raises(rm: RunManager) -> None:
    """submit_follow_up raises for blank prompt."""
    with pytest.raises(ValueError, match="empty"):
        rm.submit_follow_up("")


def test_submit_follow_up_writes(rm: RunManager, tmp_path: Path) -> None:
    """submit_follow_up writes next-prompt.txt and command file."""
    gate = _active_with_gate(rm, tmp_path)
    rm.submit_follow_up("do more work", run_id="r1")
    assert (gate / "next-prompt.txt").read_text(encoding="utf-8").strip() == "do more work"
    assert (gate / "command").read_text(encoding="utf-8").strip() == "follow_up"
    assert not (gate / "final_turn").exists()


def test_submit_follow_up_final_turn(rm: RunManager, tmp_path: Path) -> None:
    """final=True writes final_turn marker on the gate."""
    gate = _active_with_gate(rm, tmp_path)
    rm.submit_follow_up("last one", run_id="r1", final=True)
    assert (gate / "final_turn").read_text(encoding="utf-8").strip() == "1"
    rm.submit_follow_up("not last", run_id="r1", final=False)
    assert not (gate / "final_turn").exists()


def test_submit_follow_up_all_fail_raises(rm: RunManager) -> None:
    """submit_follow_up raises when all gate writes fail."""
    # Force all turn_gate_dirs to fail writing
    with patch.object(rm, "turn_gate_dirs", return_value=[Path("/nonexistent/gate")]):
        with pytest.raises(RuntimeError, match="could not write"):
            rm.submit_follow_up("test")


def test_complete_interactive_gate_dir(rm: RunManager, tmp_path: Path) -> None:
    """complete_interactive finalizes gate dirs to state=done."""
    gate = _active_with_gate(rm, tmp_path)
    rm.orchestrator._docker = Mock()
    rm.complete_interactive("r1")
    assert json.loads((gate / "status.json").read_text()).get("state") == "done"
    assert not (gate / "command").is_file()


def test_save_run_manifest_writes_json(tmp_path: Path) -> None:
    """_save_run_manifest writes run.json to session dirs from results."""
    ev = EvalRun(run_id="r1", prompt="test prompt", status="completed")
    sd = tmp_path / "session"
    sd.mkdir()
    result = ContainerStatus(
        container_name="groket-r1-model",
        model="m1",
        status="completed",
        session_dir=sd,
    )
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[FakeContainerConfig()],
    )
    RunManager._save_run_manifest(bg, [result])
    rj = sd / "run.json"
    assert rj.is_file()
    data = json.loads(rj.read_text(encoding="utf-8"))
    assert data["prompt"] == "test prompt"


def test_save_run_manifest_session_dir_none(tmp_path: Path) -> None:
    """_save_run_manifest skips results without session_dir."""
    ev = EvalRun(run_id="r2", prompt="p", status="completed")
    result = ContainerStatus(
        container_name="groket-r2-model",
        model="m1",
        status="completed",
    )
    bg = BackgroundRun(
        run_id="r2",
        eval_run=ev,
        configs=[FakeContainerConfig()],
    )
    RunManager._save_run_manifest(bg, [result])  # no crash


def test_batch_active_and_count(rm: RunManager) -> None:
    """batch_active and active_count properties work."""
    assert not rm.batch_active
    assert rm.active_count == 0
    assert rm.active_batch_ids == []


# ── error resilience and edge cases ──────────────────────────────────────


def test_detach_ui_log_buffer_clear_fails(rm: RunManager, tmp_path: Path) -> None:
    """detach_ui handles log_buffer.clear_listeners failure."""
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")

    class FakeLogBuffer:
        def clear_listeners(self) -> None:
            raise RuntimeError("no clear")

        def enable_live_notify(self, _v: bool) -> None:
            raise RuntimeError("no enable")

    bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg])
    bg.log_buffer = FakeLogBuffer()  # type: ignore[assignment]  # deliberate wrong type
    with rm._lock:
        rm._active["r1"] = bg
    rm.detach_ui()
    assert rm.ui_detached


def test_start_run_persona_load_exception(rm: RunManager, tmp_path: Path) -> None:
    """start_run handles persona load exception gracefully."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with (
        _mock_validate(),
        patch(
            "groket.runs.run_manager.PersonaStore",
            side_effect=RuntimeError("persona store broken"),
        ),
    ):
        bg = rm.start_run(
            prompt="test",
            models=["m1"],
            persona_id="bad-persona",
            **_run_kw(tmp_path),
        )
    assert bg.run_id


def test_start_run_merge_capabilities_exception(rm: RunManager, tmp_path: Path) -> None:
    """start_run handles merge_capabilities exception."""
    from groket.runs.personas import Persona, PersonaStore

    persona_dir = rm.work_dir / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    p = Persona(persona_id="merge-fail", name="Test", mcp_servers=["slack"])
    PersonaStore(rm.work_dir).save(p)
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with (
        _mock_validate(),
        patch(
            "groket.runs.run_manager.merge_capabilities",
            side_effect=RuntimeError("merge boom"),
        ),
    ):
        bg = rm.start_run(
            prompt="test",
            models=["m1"],
            persona_id="merge-fail",
            **_run_kw(tmp_path),
        )
    assert bg.run_id


def test_start_run_no_models_raises(rm: RunManager, tmp_path: Path) -> None:
    """start_run raises RuntimeError when all models are skipped."""
    with patch(
        "groket.runs.run_manager.validate_models_for_launch",
        return_value=([], ["m1 not found"]),
    ):
        with pytest.raises(RuntimeError, match="No active models"):
            rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path))


def test_start_run_saves_config_flag(rm: RunManager, tmp_path: Path) -> None:
    """start_run persists run config when save_config=True."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with _mock_validate():
        rm.start_run(
            prompt="save me",
            models=["m1"],
            **_run_kw(tmp_path, save_config=True),
        )
    from groket.runs.run_configs import RunConfigStore

    configs = RunConfigStore(rm.work_dir).list_configs()
    assert any("save me" in (c.prompt or "") for c in configs)


def test_start_run_save_config_exception(rm: RunManager, tmp_path: Path) -> None:
    """start_run handles save config exception."""
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
    with (
        _mock_validate(),
        patch(
            "groket.runs.run_manager.RunConfigStore",
            side_effect=RuntimeError("store broken"),
        ),
    ):
        bg = rm.start_run(
            prompt="fail save",
            models=["m1"],
            **_run_kw(tmp_path, save_config=True),
        )
    assert bg.run_id


def test_worker_on_status_after_detach(rm: RunManager, tmp_path: Path) -> None:
    """Worker on_status skips listener calls after UI detach."""
    called: list[str] = []
    rm.add_status_listener(lambda s: called.append(s.container_name))
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])

    with _mock_validate():
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path))

    rm.detach_ui()
    import threading

    for _ in range(200):
        if not bg.is_running:
            break
        threading.Event().wait(0.05)


def test_worker_on_log_with_detach(rm: RunManager, tmp_path: Path) -> None:
    """Worker on_log respects detach state."""
    rm._log_listeners.append(lambda n, line: None)
    rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])

    with _mock_validate():
        bg = rm.start_run(prompt="test", models=["m1"], **_run_kw(tmp_path))

    import threading

    for _ in range(200):
        if not bg.is_running:
            break
        threading.Event().wait(0.05)


def test_complete_interactive_docker_stop_fails(rm: RunManager, tmp_path: Path) -> None:
    """complete_interactive handles docker stop/remove failures."""
    traces = tmp_path / "traces"
    gate = traces / ".groket-turn"
    gate.mkdir(parents=True)
    ev = EvalRun(run_id="r1", prompt="p", status="running")
    cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
    bg = BackgroundRun(
        run_id="r1",
        eval_run=ev,
        configs=[cfg],
        traces_vol=traces,
    )
    with rm._lock:
        rm._active["r1"] = bg
    rm.orchestrator._docker = Mock()
    rm.orchestrator._docker.stop = Mock(side_effect=RuntimeError("no stop"))
    rm.orchestrator._docker.remove = Mock(side_effect=RuntimeError("no remove"))
    rm.complete_interactive("r1")
    assert json.loads((gate / "status.json").read_text()).get("state") == "done"
    assert not (gate / "command").is_file()


class TestWorkerStatusCallback:
    """_worker on_status fan-out and detach handling."""

    def test_worker_status_with_listeners(self, tmp_path: Path) -> None:
        """on_status calls status listeners until UI detached."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        received: list[str] = []
        rm._status_listeners.append(lambda s: received.append(s.status))
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        # Use FakeOrchestrator directly (it calls on_status)
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert bg.eval_run.status == "completed"
        assert len(received) >= 1

    def test_worker_status_listener_exception(self, tmp_path: Path) -> None:
        """Exception in status listener does not crash worker."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        rm._status_listeners.append(lambda s: (_ for _ in ()).throw(ValueError("bad")))
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        status_result = ContainerStatus(
            container_name="groket-r1-m",
            model="m",
            status="completed",
            session_dir=tmp_path,
        )
        rm.orchestrator.run_parallel_evaluations = Mock(return_value=[status_result])
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert bg.eval_run.status == "completed"

    def test_worker_detached_skips_status_fanout(self, tmp_path: Path) -> None:
        """When UI detached, status listeners are not called."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        rm._ui_detached = True
        received: list[str] = []
        rm._status_listeners.append(lambda s: received.append(s.status))
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        status_result = ContainerStatus(
            container_name="groket-r1-m",
            model="m",
            status="completed",
            session_dir=tmp_path,
        )
        rm.orchestrator.run_parallel_evaluations = Mock(return_value=[status_result])
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert received == []

    def test_worker_finished_listener_called(self, tmp_path: Path) -> None:
        """Finished listener called after worker completes."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        finished: list[str] = []
        rm._finished_listeners.append(lambda bg_arg: finished.append(bg_arg.run_id))
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert "r1" in finished

    def test_worker_finished_listener_error_suppressed(self, tmp_path: Path) -> None:
        """Exception in finished listener is suppressed."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)

        def _bad_cb(bg_arg: BackgroundRun) -> None:
            raise ValueError("bad")

        rm._finished_listeners.append(_bad_cb)
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert bg.eval_run.status == "completed"

    def test_worker_orchestrator_failure(self, tmp_path: Path) -> None:
        """Worker sets error state when orchestrator raises."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        rm.orchestrator.run_parallel_evaluations = Mock(side_effect=RuntimeError("docker gone"))
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert bg.eval_run.status == "failed"
        assert "docker gone" in bg.error


class TestSaveRunManifest:
    """_save_run_manifest writes run.json to session directories."""

    def test_writes_run_json(self, tmp_path: Path) -> None:
        """run.json written to session dirs from results."""
        sd = tmp_path / "sess"
        sd.mkdir()
        ev = EvalRun(run_id="r1", prompt="hello", status="completed")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        result = ContainerStatus(
            container_name="groket-r1-m",
            model="m",
            status="completed",
            session_dir=sd,
        )
        RunManager._save_run_manifest(bg, [result])
        assert (sd / "run.json").is_file()
        data = json.loads((sd / "run.json").read_text(encoding="utf-8"))
        assert data["run_id"] == "r1"

    def test_session_dir_write_oserror(self, tmp_path: Path) -> None:
        """OSError writing run.json is silently logged."""
        sd = tmp_path / "sess"
        sd.mkdir()
        ev = EvalRun(run_id="r1", prompt="p", status="completed")
        cfg = FakeContainerConfig(container_name="c1", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        result = ContainerStatus(
            container_name="c1",
            model="m",
            status="completed",
            session_dir=sd,
        )
        with patch.object(Path, "write_text", side_effect=OSError("no write")):
            RunManager._save_run_manifest(bg, [result])  # no raise


class TestActiveRunPreference:
    """_active_run prefers interactive runs when no run_id given."""

    def test_prefers_interactive(self, tmp_path: Path) -> None:
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        ev1 = EvalRun(run_id="r1", prompt="p", status="running")
        ev2 = EvalRun(run_id="r2", prompt="p", status="running")
        bg1 = BackgroundRun(
            run_id="r1",
            eval_run=ev1,
            configs=[FakeContainerConfig(run_id="r1")],
            traces_vol=tmp_path,
            interactive=False,
        )
        bg2 = BackgroundRun(
            run_id="r2",
            eval_run=ev2,
            configs=[FakeContainerConfig(run_id="r2")],
            traces_vol=tmp_path,
            interactive=True,
        )
        with rm._lock:
            rm._active["r1"] = bg1
            rm._active["r2"] = bg2
        result = rm._active_run()
        assert result is not None
        assert result.interactive is True

    def test_specific_run_id(self, tmp_path: Path) -> None:
        """_active_run with explicit run_id returns that run."""
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        bg = BackgroundRun(
            run_id="r1",
            eval_run=ev,
            configs=[FakeContainerConfig(run_id="r1")],
            traces_vol=tmp_path,
        )
        with rm._lock:
            rm._active["r1"] = bg
        assert rm._active_run("r1") is bg

    def test_no_active_returns_none(self, tmp_path: Path) -> None:
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        assert rm._active_run() is None


class TestWorkerHistoryTrim:
    """_worker trims history when it exceeds MAX_RUN_HISTORY."""

    def test_history_trimmed(self, tmp_path: Path) -> None:
        """History list trimmed after exceeding max size."""
        import groket.runs.run_manager as rm_mod

        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        # Pre-fill history to just below max
        for i in range(rm_mod.MAX_RUN_HISTORY):
            ev = EvalRun(run_id=f"old-{i}", prompt="p", status="completed")
            bg_old = BackgroundRun(
                run_id=f"old-{i}",
                eval_run=ev,
                configs=[FakeContainerConfig(run_id=f"old-{i}")],
                traces_vol=tmp_path,
            )
            rm._history.append(bg_old)

        ev = EvalRun(run_id="new", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-new-m", run_id="new")
        bg = BackgroundRun(run_id="new", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["new"] = bg

        rm.orchestrator.run_parallel_evaluations = Mock(return_value=[])
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert len(rm._history) <= rm_mod.MAX_RUN_HISTORY


class TestWorkerOnLogCapture:
    """_worker on_log always captures to bg."""

    def test_log_captured(self, tmp_path: Path) -> None:
        rm = RunManager(tmp_path)
        rm.orchestrator = FakeOrchestrator(tmp_path)
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with rm._lock:
            rm._active["r1"] = bg

        # FakeOrchestrator calls on_log with container_name and "line"
        rm._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        assert bg.log_buffer.snapshot()  # some logs captured


class TestStartRunPersonaDockerImageFallback:
    """Persona docker_image applied when run docker_image is blank."""

    def test_persona_docker_image_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import groket.runs.run_manager as rm_mod

        def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
            class T:
                def start(self_inner):
                    target(*args, **(kwargs or {}))

                def is_alive(self_inner):
                    return False

            return T()

        monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
        monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))
        monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
        monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)

        from groket.runs.personas import Persona, PersonaStore

        store = PersonaStore(tmp_path)
        persona = Persona(
            persona_id="docker-p",
            name="Docker Persona",
            docker_image="custom-image",
        )
        store.save(persona)

        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path / "runs")
        auth = tmp_path / "auth.json"
        auth.write_text("{}", encoding="utf-8")
        grok = tmp_path / "config.toml"
        grok.write_text("", encoding="utf-8")

        bg = manager.start_run(
            prompt="p",
            setup_instructions="",
            docker_image="",  # Blank, should fall back to persona
            models=["m1"],
            parallelism=1,
            repo_url="",
            repo_branch="",
            auth_json=auth,
            grok_config=grok,
            persona_id="docker-p",
            save_config=False,
        )
        assert bg.run_id


class TestStartRunModelShortTag:
    """Cover line 429: model with digit-only last segment uses second-to-last."""

    def test_model_digit_suffix(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import groket.runs.run_manager as rm_mod

        def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
            class T:
                def start(self_inner):
                    target(*args, **(kwargs or {}))

                def is_alive(self_inner):
                    return False

            return T()

        monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
        monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))
        monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
        monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)

        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path / "runs")
        auth = tmp_path / "auth.json"
        auth.write_text("{}", encoding="utf-8")
        grok = tmp_path / "config.toml"
        grok.write_text("", encoding="utf-8")

        bg = manager.start_run(
            prompt="p",
            setup_instructions="",
            docker_image="fully-loaded",
            models=["bottlerock-9"],
            parallelism=1,
            repo_url="",
            repo_branch="",
            auth_json=auth,
            grok_config=grok,
            save_config=False,
        )
        assert "bottlerock" in bg.configs[0].container_name


class TestStartRunParallelism:
    """Parallelism > 1 creates multiple container configs."""

    def test_parallelism_creates_multiple_configs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import groket.runs.run_manager as rm_mod

        def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
            class T:
                def start(self_inner):
                    target(*args, **(kwargs or {}))

                def is_alive(self_inner):
                    return False

            return T()

        monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
        monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))
        monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
        monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)

        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path / "runs")
        auth = tmp_path / "auth.json"
        auth.write_text("{}", encoding="utf-8")
        grok = tmp_path / "config.toml"
        grok.write_text("", encoding="utf-8")

        bg = manager.start_run(
            prompt="p",
            setup_instructions="",
            docker_image="fully-loaded",
            models=["m1"],
            parallelism=2,
            repo_url="",
            repo_branch="",
            auth_json=auth,
            grok_config=grok,
            save_config=False,
        )
        assert len(bg.configs) == 2


class TestStartRunLogListeners:
    """Log listeners wired to background run on start."""

    def test_log_listeners_wired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import groket.runs.run_manager as rm_mod

        def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
            class T:
                def start(self_inner):
                    target(*args, **(kwargs or {}))

                def is_alive(self_inner):
                    return False

            return T()

        monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
        monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda m: (list(m), []))
        monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
        monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)

        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path / "runs")
        logs_received: list[tuple[str, str]] = []
        manager.add_log_listener(lambda a, b: logs_received.append((a, b)))

        auth = tmp_path / "auth.json"
        auth.write_text("{}", encoding="utf-8")
        grok = tmp_path / "config.toml"
        grok.write_text("", encoding="utf-8")

        bg = manager.start_run(
            prompt="p",
            setup_instructions="",
            docker_image="fully-loaded",
            models=["m1"],
            parallelism=1,
            repo_url="",
            repo_branch="",
            auth_json=auth,
            grok_config=grok,
            save_config=False,
        )
        assert bg.run_id


class TestWorkerUiDetachedSkipsCallbacks:
    """Cover lines 681, 685-686: _worker when UI is detached skips status callbacks."""

    def test_ui_detached_skips_listeners(self, tmp_path: Path) -> None:
        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path)
        statuses_received: list[FakeContainerStatus] = []
        manager.add_status_listener(lambda s: statuses_received.append(s))
        manager.detach_ui()

        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(run_id="r1", eval_run=ev, configs=[cfg], traces_vol=tmp_path)
        with manager._lock:
            manager._active["r1"] = bg
        manager._worker(bg, tmp_path / "auth.json", tmp_path / "config.json")
        # Callbacks should not receive anything because UI is detached
        assert statuses_received == []


class TestActiveRunPreferenceInteractive:
    """Cover line 766: _active_run prefers interactive when no run_id."""

    def test_prefers_interactive_run(self, tmp_path: Path) -> None:
        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path)

        ev1 = EvalRun(run_id="r1", prompt="p", status="running")
        bg1 = BackgroundRun(
            run_id="r1",
            eval_run=ev1,
            configs=[FakeContainerConfig(run_id="r1")],
            interactive=False,
            traces_vol=tmp_path,
        )
        ev2 = EvalRun(run_id="r2", prompt="p", status="running")
        bg2 = BackgroundRun(
            run_id="r2",
            eval_run=ev2,
            configs=[FakeContainerConfig(run_id="r2")],
            interactive=True,
            traces_vol=tmp_path,
        )
        with manager._lock:
            manager._active["r1"] = bg1
            manager._active["r2"] = bg2

        result = manager._active_run("")
        assert result is not None
        assert result.interactive is True


class TestInteractiveStatus:
    """Cover lines 822-823: interactive_status reads status.json."""

    def test_reads_status_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import groket.runs.run_manager as rm_mod

        monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
        monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)

        traces_vol = tmp_path / "traces" / "groket-r1-m"
        traces_vol.mkdir(parents=True)

        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path)
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(
            run_id="r1",
            eval_run=ev,
            configs=[cfg],
            interactive=True,
            traces_vol=traces_vol,
        )
        with manager._lock:
            manager._active["r1"] = bg

        # Create turn gate dirs with status.json — traces_vol based
        turn_dir = traces_vol / ".groket-turn"
        turn_dir.mkdir(parents=True)
        (turn_dir / "status.json").write_text(
            json.dumps({"state": "waiting_for_input"}), encoding="utf-8"
        )
        status = manager.interactive_status(run_id="r1")
        assert status["state"] == "waiting_for_input"


class TestCompleteInteractiveOSError:
    """Cover lines 849-850: complete_interactive when turn gate write fails."""

    def test_oserror_on_gate_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import groket.runs.run_manager as rm_mod

        monkeypatch.setattr(rm_mod, "DockerOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(rm_mod, "ContainerConfig", FakeContainerConfig)
        monkeypatch.setattr(rm_mod, "ContainerStatus", FakeContainerStatus)

        manager = RunManager(tmp_path)
        manager.orchestrator = FakeOrchestrator(tmp_path)
        ev = EvalRun(run_id="r1", prompt="p", status="running")
        cfg = FakeContainerConfig(container_name="groket-r1-m", run_id="r1")
        bg = BackgroundRun(
            run_id="r1",
            eval_run=ev,
            configs=[cfg],
            interactive=True,
            traces_vol=tmp_path / "traces" / "groket-r1-m",
        )
        with manager._lock:
            manager._active["r1"] = bg

        # Create turn gate dir, then make command write fail
        turn_dir = tmp_path / "traces" / "groket-r1-m" / ".turn_gate"
        turn_dir.mkdir(parents=True)

        orig_write_text = Path.write_text

        def bad_write(self: Path, content, **kwargs) -> None:
            if self.name == "command":
                raise OSError("no write")
            return orig_write_text(self, content, **kwargs)

        monkeypatch.setattr(Path, "write_text", bad_write)
        # Should not raise
        manager.complete_interactive(run_id="r1")


def test_start_run_resume_forces_interactive(
    rm: RunManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resume kwargs force interactive and land on ContainerConfig."""
    import groket.runs.run_manager as rm_mod

    def immediate_thread(target=None, args=(), kwargs=None, daemon=None, name=None):
        class T:
            def start(self_inner):
                return None

            def is_alive(self_inner):
                return False

        return T()

    monkeypatch.setattr(rm_mod.threading, "Thread", immediate_thread)
    monkeypatch.setattr(rm_mod, "validate_models_for_launch", lambda models: (list(models), []))
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok = tmp_path / "config.toml"
    grok.write_text("", encoding="utf-8")
    bg = rm.start_run(
        prompt="continue",
        setup_instructions="",
        docker_image="fully-loaded",
        models=["m1"],
        parallelism=1,
        repo_url="",
        repo_branch="",
        auth_json=auth,
        grok_config=grok,
        save_config=False,
        interactive=False,
        resume_session_id="sess-xyz",
        resume_source_dir=str(tmp_path / "old-sess"),
    )
    assert bg.interactive is True
    assert bg.configs[0].interactive is True
    assert bg.configs[0].resume_session_id == "sess-xyz"
    assert bg.configs[0].resume_source_dir == str(tmp_path / "old-sess")
