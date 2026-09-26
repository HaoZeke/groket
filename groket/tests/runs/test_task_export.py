"""Export run recipes as batch task YAML."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.runs.run_configs import RunConfig
from groket.runs.task_export import (
    default_task_export_path,
    render_task_catalog_yaml,
    slug_task_id,
    source_from_recipe_mapping,
    source_from_run_config,
    write_task_export,
)
from groket.runs.task_schema import load_task_file


def test_slug_task_id() -> None:
    assert slug_task_id("Hello World!") == "hello-world"
    assert slug_task_id("") == "exported-task"


def test_source_from_run_config_and_write(tmp_path: Path) -> None:
    cfg = RunConfig(
        config_id="abc",
        name="My Recipe",
        prompt="Do the thing carefully.",
        repo_path="~/proj",
        persona_id="superpowers",
        models=["grok-4.5:high"],
        max_turns=40,
        yolo=True,
        run_plugins=["superpowers"],
        setup_instructions="echo setup",
    )
    src = source_from_run_config(cfg)
    assert src.task_id == "my-recipe"
    text = render_task_catalog_yaml(src)
    assert "task_id: my-recipe" in text
    assert "yolo: true" in text
    assert "run_plugins: superpowers" in text  # comment note
    out = write_task_export(tmp_path / "custom" / "job.yaml", src)
    assert out.is_file()
    doc = load_task_file(out)
    assert doc.tasks[0].task_id == "my-recipe"
    assert doc.tasks[0].yolo is True
    assert doc.tasks[0].max_turns == 40
    assert doc.tasks[0].persona_id == "superpowers"


def test_source_from_recipe_mapping() -> None:
    src = source_from_recipe_mapping(
        {
            "prompt": "p" * 20,
            "run_id": "deadbeef",
            "models": ["m1"],
            "yolo": False,
        }
    )
    assert src.task_id == "deadbeef"
    assert src.models == ("m1",)


def test_write_requires_prompt(tmp_path: Path) -> None:
    cfg = RunConfig(config_id="x", prompt="")
    with pytest.raises(ValueError, match="prompt"):
        write_task_export(tmp_path / "x.yaml", source_from_run_config(cfg))


def test_default_path_under_user_tasks() -> None:
    p = default_task_export_path("Foo Bar")
    assert p.name == "foo-bar.yaml"
    assert p.parent.name == "tasks"
