"""Run recipe (run.json) write/load for fork prefill and launch reuse."""

from __future__ import annotations

import json
from pathlib import Path

from groket.docker.orchestrator import ContainerConfig
from groket.runs.launch_meta import build_launch_meta, write_launch_meta
from groket.runs.run_recipe import (
    find_run_recipe_path,
    load_run_recipe,
    recipe_from_container_config,
    write_run_recipe,
    write_run_recipe_for_config,
)
from groket.session.resume import RESUME_SEED_DIRNAME


def test_write_run_recipe_for_config_on_traces_vol(tmp_path: Path) -> None:
    traces = tmp_path / "traces" / "groket-abc-4.5-high"
    traces.mkdir(parents=True)
    cfg = ContainerConfig(
        model="grok-4.5",
        reasoning_effort="high",
        prompt="hi",
        docker_image="fully-loaded",
        repo_url="https://github.com/ex/repo",
        repo_path="/home/me/src/proj",
        container_name="groket-abc-4.5-high",
        run_id="abc",
        persona_id="tree-sitter-analyzer",
        run_plugins=["superpowers"],
        run_mcp_servers=["srv"],
        run_skills=["sk"],
    )
    path = write_run_recipe_for_config(traces, cfg)
    assert path is not None and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["persona_id"] == "tree-sitter-analyzer"
    assert data["run_plugins"] == ["superpowers"]
    assert data["run_mcp_servers"] == ["srv"]
    assert data["run_skills"] == ["sk"]
    assert data["repo_url"] == "https://github.com/ex/repo"
    assert data["repo_path"] == "/home/me/src/proj"
    assert any("grok-4.5" in m for m in data["models"])


def test_load_run_recipe_from_session_and_traces_vol(tmp_path: Path) -> None:
    run = tmp_path / "traces" / "groket-r1-m"
    session = run / "%2Fworkspace" / "sess-1"
    session.mkdir(parents=True)
    write_run_recipe(
        run,
        {
            "persona_id": "p1",
            "run_plugins": ["pl"],
            "skills": [],
            "mcp_servers": [],
        },
    )
    assert find_run_recipe_path(session) == run / "run.json"
    data = load_run_recipe(session)
    assert data["persona_id"] == "p1"
    assert data["run_plugins"] == ["pl"]


def test_load_run_recipe_falls_back_to_fork_parent_seed(tmp_path: Path) -> None:
    """Child without run.json still gets parent persona/plugins via seed."""
    run = tmp_path / "traces" / "groket-fork-m"
    token = "%2Fworkspace"
    parent_id = "parent-sess-id"
    child_id = "child-sess-id"
    seed = run / RESUME_SEED_DIRNAME / token / parent_id
    seed.mkdir(parents=True)
    # Parent seed must look resumeable (chat/events/summary) for fork_parent lookup.
    (seed / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    (seed / "events.jsonl").write_text("", encoding="utf-8")
    (seed / "summary.json").write_text("{}", encoding="utf-8")
    write_run_recipe(
        seed,
        {
            "persona_id": "tree-sitter-analyzer",
            "run_plugins": ["superpowers"],
            "skills": [],
            "mcp_servers": [],
        },
    )
    child = run / token / child_id
    child.mkdir(parents=True)
    # Launch meta links child fork to parent.
    write_launch_meta(
        run,
        build_launch_meta(
            model="grok-4.5",
            reasoning_effort="high",
            container_name=run.name,
            resume_parent_session_id=parent_id,
            resume_fork_session_id=child_id,
        ),
    )
    data = load_run_recipe(child)
    assert data["persona_id"] == "tree-sitter-analyzer"
    assert data["run_plugins"] == ["superpowers"]


def test_recipe_from_container_config_models_token() -> None:
    cfg = ContainerConfig(
        model="grok-4.5",
        reasoning_effort="high",
        prompt="p",
        persona_id="x",
        run_plugins=["a"],
    )
    recipe = recipe_from_container_config(cfg)
    assert recipe["persona_id"] == "x"
    assert recipe["run_plugins"] == ["a"]
    assert recipe["models"] == ["grok-4.5:high"]


def test_load_run_recipe_missing(tmp_path: Path) -> None:
    sd = tmp_path / "empty-sess"
    sd.mkdir()
    assert load_run_recipe(sd) == {}
    assert find_run_recipe_path(sd) is None
