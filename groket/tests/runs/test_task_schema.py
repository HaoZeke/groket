"""Tasks YAML schema validation and loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from groket.runs.batch import load_tasks
from groket.runs.task_schema import (
    TaskFile,
    emit_tasks_schema,
    load_task_file,
    tasks_json_schema,
    validate_tasks_path,
)
from pydantic import ValidationError


def test_load_demo_tasks():
    demo = Path("examples/tasks/demo_tasks.yaml")
    if not demo.is_file():
        pytest.skip("demo tasks missing")
    tasks = load_tasks(demo)
    assert len(tasks) >= 1
    assert tasks[0].task_id


def test_validate_and_defaults(tmp_path: Path):
    path = tmp_path / "t.yaml"
    path.write_text(
        yaml.dump(
            {
                "schema_version": 1,
                "defaults": {"category": "custom", "domain": "ml-data"},
                "tasks": [
                    {
                        "task_id": "a",
                        "prompt": "hello world",
                        "turns": ["second turn"],
                        "tags": ["x"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    doc = validate_tasks_path(path)
    assert doc.schema_version == 1
    tasks = load_tasks(path)
    assert tasks[0].category == "custom"
    assert tasks[0].domain == "ml-data"
    assert tasks[0].turns == ["second turn"]
    assert tasks[0].tags == ["x"]


def test_missing_prompt_fails(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("tasks:\n  - task_id: x\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_task_file(path)


def test_emit_schema(tmp_path: Path):
    out = tmp_path / "s.json"
    text = emit_tasks_schema(out)
    assert "task_id" in text
    assert out.is_file()
    schema = tasks_json_schema()
    assert schema.get("title")


def test_empty_tasks_fails():
    with pytest.raises(ValidationError):
        TaskFile.model_validate({"tasks": []})


def test_run_manager_turn_gate(tmp_path: Path):
    from groket.docker.orchestrator import ContainerConfig
    from groket.models import EvalRun
    from groket.runs.run_manager import BackgroundRun, RunManager

    rm = RunManager(tmp_path)
    # Simulate active run whose container traces volume is work_dir/traces/<name>
    cname = "groket-abc-model"
    traces_vol = tmp_path / "traces" / cname
    traces_vol.mkdir(parents=True)
    ev = EvalRun(
        run_id="abc",
        prompt="p",
        models=["m"],
        status="running",
        created_at="t",
    )
    cfg = ContainerConfig(model="m", prompt="p", container_name=cname, run_id="abc")
    bg = BackgroundRun(
        run_id="abc",
        eval_run=ev,
        configs=[cfg],
        interactive=True,
        traces_vol=traces_vol,
    )
    with rm._lock:
        rm._active["abc"] = bg

    rm.submit_follow_up("next please", run_id="abc")
    gate = traces_vol / ".groket-turn"
    assert gate.is_dir()
    assert (gate / "next-prompt.txt").read_text(encoding="utf-8") == "next please"
    assert "follow_up" in (gate / "command").read_text(encoding="utf-8")

    (gate / "status.json").write_text(
        '{"state": "awaiting_follow_up", "session_id": "s1", "turn": 1}\n',
        encoding="utf-8",
    )
    assert rm.is_awaiting_follow_up("abc") is True
    st = rm.interactive_status("abc")
    assert st.get("state") == "awaiting_follow_up"

    rm.complete_interactive("abc")
    assert json.loads((gate / "status.json").read_text(encoding="utf-8")).get("state") == "done"
    assert not (gate / "command").is_file()


def test_task_definition_setup_shell_list():
    from groket.runs.task_schema import TaskDefinition

    t = TaskDefinition(task_id="t", prompt="p", initial_commands=["a", "b"])
    assert t.setup_shell() == "a\nb"


def test_task_definition_setup_shell_string():
    from groket.runs.task_schema import TaskDefinition

    t = TaskDefinition(task_id="t", prompt="p", setup="echo hi")
    assert t.setup_shell() == "echo hi"


def test_task_definition_setup_shell_empty():
    from groket.runs.task_schema import TaskDefinition

    t = TaskDefinition(task_id="t", prompt="p")
    assert t.setup_shell() == ""


def test_task_definition_effective_repo_branch_default():
    from groket.runs.task_schema import TaskDefinition

    # Empty branch → remote default HEAD (do not invent "main").
    t = TaskDefinition(task_id="t", prompt="p", repo_url="https://github.com/x/y")
    assert t.effective_repo_branch() == ""
    t2 = TaskDefinition(
        task_id="t", prompt="p", repo_url="https://github.com/x/y", repo_branch="master"
    )
    assert t2.effective_repo_branch() == "master"


def test_task_definition_effective_repo_branch_no_url():
    from groket.runs.task_schema import TaskDefinition

    t = TaskDefinition(task_id="t", prompt="p")
    assert t.effective_repo_branch() == ""


def test_task_file_root_not_mapping(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_task_file(path)


def test_task_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_task_file(Path("/nonexistent/tasks.yaml"))


def test_resolved_tasks_with_defaults_env_merge(tmp_path: Path):
    path = tmp_path / "t.yaml"
    path.write_text(
        yaml.dump(
            {
                "defaults": {"env": {"A": "1"}, "models": ["m1"]},
                "tasks": [{"task_id": "t1", "prompt": "go", "env": {"B": "2"}}],
            }
        ),
        encoding="utf-8",
    )
    doc = load_task_file(path)
    resolved = doc.resolved_tasks()
    assert resolved[0].env == {"A": "1", "B": "2"}
    assert resolved[0].models == ["m1"]


def test_resolved_tasks_defaults_override_string_fields(tmp_path: Path):
    path = tmp_path / "t.yaml"
    path.write_text(
        yaml.dump(
            {
                "defaults": {"description": "default-desc", "horizon": "short"},
                "tasks": [{"task_id": "t1", "prompt": "go"}],
            }
        ),
        encoding="utf-8",
    )
    doc = load_task_file(path)
    resolved = doc.resolved_tasks()
    assert resolved[0].description == "default-desc"
    assert resolved[0].horizon == "short"


def test_task_definition_to_eval_task():
    from groket.runs.task_schema import TaskDefinition, task_definition_to_eval_task

    td = TaskDefinition(
        task_id="t1",
        prompt="do stuff",
        repo_url="https://github.com/x/y",
        tags=["a"],
        turns=["follow up"],
        success_hints=["check output"],
        env={"K": "V"},
    )
    et = task_definition_to_eval_task(td)
    assert et.task_id == "t1"
    assert et.prompt == "do stuff"
    assert et.repo_branch == ""
    assert et.turns == ["follow up"]
    assert et.tags == ["a"]
    assert et.success_hints == ["check output"]
    assert et.env == {"K": "V"}


def test_emit_schema_no_output():
    text = emit_tasks_schema(out=None)
    assert "task_id" in text


def test_none_repo_url_coerced():
    from groket.runs.task_schema import TaskDefinition

    td = TaskDefinition(task_id="t", prompt="p", repo_url=None)  # type: ignore[arg-type]  # deliberate wrong type
    assert td.repo_url == ""


def test_repo_path_on_task_and_eval():
    from groket.runs.task_schema import TaskDefinition, task_definition_to_eval_task

    td = TaskDefinition(
        task_id="t",
        prompt="p",
        repo_path="~/src/proj",
        repo_url=None,  # type: ignore[arg-type]
    )
    assert td.repo_path == "~/src/proj"
    et = task_definition_to_eval_task(td)
    assert et.repo_path == "~/src/proj"
    assert et.has_local_path is True
    assert et.has_repo is True


def test_batch_task_turns_on_eval_task():
    from groket.runs.batch import EvalTask

    t = EvalTask(task_id="t", prompt="p", turns=["a", "b"])
    assert t.turns == ["a", "b"]


def test_turns_accept_string_list_and_legacy_maps(tmp_path: Path) -> None:
    from groket.runs.task_schema import TaskDefinition

    assert TaskDefinition(task_id="t", prompt="p", turns=["a", "b"]).turns == ["a", "b"]
    assert TaskDefinition(
        task_id="t",
        prompt="p",
        turns=[{"prompt": "legacy"}, {"text": "also"}],
    ).turns == ["legacy", "also"]


def test_resume_fields_on_task_and_eval_task(tmp_path: Path) -> None:
    from groket.runs.task_schema import TaskDefinition, task_definition_to_eval_task

    sess = tmp_path / "ended-sess"
    sess.mkdir()
    (sess / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    td = TaskDefinition(
        task_id="fork-me",
        prompt="continue the design",
        resume_session_dir=str(sess),
        turns=["and then verify"],
    )
    assert td.resolved_resume_session_dir() == sess.resolve()
    et = task_definition_to_eval_task(td)
    assert et.resume_session_dir == str(sess.resolve())
    assert et.resume_session_id == "ended-sess"
    assert et.has_resume is True
    assert et.turns == ["and then verify"]


def test_resume_session_id_override(tmp_path: Path) -> None:
    from groket.runs.task_schema import TaskDefinition, task_definition_to_eval_task

    sess = tmp_path / "folder-name"
    sess.mkdir()
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    td = TaskDefinition(
        task_id="t",
        prompt="p",
        resume_session_dir=str(sess),
        resume_session_id="019f-parent-id",
    )
    et = task_definition_to_eval_task(td)
    assert et.resume_session_id == "019f-parent-id"


def test_max_turns_default_and_override(tmp_path: Path) -> None:
    from groket.constants import DEFAULT_MAX_TURNS
    from groket.runs.task_schema import TaskDefinition, load_task_file, task_definition_to_eval_task

    td = TaskDefinition(task_id="t", prompt="p")
    assert td.max_turns == DEFAULT_MAX_TURNS
    assert task_definition_to_eval_task(td).max_turns == DEFAULT_MAX_TURNS

    td2 = TaskDefinition(task_id="t2", prompt="p", max_turns=200)
    assert task_definition_to_eval_task(td2).max_turns == 200

    path = tmp_path / "tasks.yaml"
    path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "defaults:",
                "  max_turns: 75",
                "tasks:",
                "  - task_id: a",
                "    prompt: hi",
                "  - task_id: b",
                "    prompt: hi",
                "    max_turns: 10",
            ]
        ),
        encoding="utf-8",
    )
    doc = load_task_file(path)
    resolved = doc.resolved_tasks()
    by_id = {t.task_id: t for t in resolved}
    assert by_id["a"].max_turns == 75
    assert by_id["b"].max_turns == 10
