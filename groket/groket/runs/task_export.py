"""Export run recipes / launch fields as batch task YAML catalogs."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..constants import DEFAULT_MAX_TURNS, normalize_max_turns
from ..models import JsonObject, JsonValue
from ..paths import user_tasks_dir
from .run_configs import RunConfig
from .task_schema import SCHEMA_VERSION, TaskDefinition, TaskFile, load_task_file

_SCHEMA_COMMENT = (
    "# yaml-language-server: $schema=https://indynull.github.io/groket/schemas/tasks.schema.json\n"
)


def slug_task_id(text: str, *, max_len: int = 48) -> str:
    """Filesystem-safe task_id from a name or prompt fragment."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "").strip().lower())
    s = s.strip("-._")[:max_len].strip("-._")
    return s or "exported-task"


def default_task_export_path(task_id: str) -> Path:
    """Default write path under ``~/.groket/tasks/<task_id>.yaml``."""
    tid = slug_task_id(task_id)
    return user_tasks_dir() / f"{tid}.yaml"


@dataclass(frozen=True)
class TaskExportSource:
    """Fields needed to build one batch task entry."""

    prompt: str
    task_id: str = ""
    description: str = ""
    docker_image: str = "fully-loaded"
    repo_url: str = ""
    repo_branch: str = ""
    repo_path: str = ""
    setup_instructions: str = ""
    persona_id: str = ""
    models: tuple[str, ...] = ()
    max_turns: int = DEFAULT_MAX_TURNS
    yolo: bool = False
    category: str = ""
    env: Mapping[str, str] | None = None
    # Not in task schema — surfaced as YAML comments only.
    run_plugins: tuple[str, ...] = ()
    run_skills: tuple[str, ...] = ()
    run_mcp_servers: tuple[str, ...] = ()


def source_from_run_config(cfg: RunConfig) -> TaskExportSource:
    """Map a saved recipe to export fields."""
    tid = (cfg.task_id or "").strip() or slug_task_id(cfg.name or cfg.config_id or "recipe")
    return TaskExportSource(
        prompt=cfg.prompt or "",
        task_id=tid,
        description=(cfg.notes or cfg.name or "").strip(),
        docker_image=cfg.docker_image or "fully-loaded",
        repo_url=cfg.repo_url or "",
        repo_branch=cfg.repo_branch or "",
        repo_path=cfg.repo_path or "",
        setup_instructions=cfg.setup_instructions or "",
        persona_id=cfg.persona_id or "",
        models=tuple(cfg.models or ()),
        max_turns=normalize_max_turns(cfg.max_turns, default=DEFAULT_MAX_TURNS),
        yolo=bool(cfg.yolo),
        category=(cfg.category or "").strip(),
        env=dict(cfg.run_env_vars or {}),
        run_plugins=tuple(cfg.run_plugins or ()),
        run_skills=tuple(cfg.run_skills or ()),
        run_mcp_servers=tuple(cfg.run_mcp_servers or ()),
    )


def source_from_recipe_mapping(data: Mapping[str, JsonValue]) -> TaskExportSource:
    """Map a ``run.json`` / recipe dict to export fields."""
    models_raw = data.get("models")
    models: list[str] = []
    if isinstance(models_raw, list):
        models = [str(m).strip() for m in models_raw if str(m).strip()]
    env_raw = data.get("run_env_vars") or data.get("env")
    env: dict[str, str] = {}
    if isinstance(env_raw, dict):
        env = {str(k): str(v) for k, v in env_raw.items() if str(k).strip()}
    plugins = data.get("run_plugins") or data.get("plugins") or []
    skills = data.get("run_skills") or data.get("skills") or []
    mcp = data.get("run_mcp_servers") or data.get("mcp_servers") or []
    name = str(data.get("name") or data.get("run_id") or data.get("task_id") or "run")
    tid = str(data.get("task_id") or "").strip() or slug_task_id(name)
    return TaskExportSource(
        prompt=str(data.get("prompt") or ""),
        task_id=tid,
        description=str(data.get("description") or data.get("notes") or name).strip(),
        docker_image=str(data.get("docker_image") or "fully-loaded"),
        repo_url=str(data.get("repo_url") or ""),
        repo_branch=str(data.get("repo_branch") or ""),
        repo_path=str(data.get("repo_path") or ""),
        setup_instructions=str(
            data.get("setup_instructions") or data.get("initial_commands") or ""
        ),
        persona_id=str(data.get("persona_id") or ""),
        models=tuple(models),
        max_turns=normalize_max_turns(data.get("max_turns"), default=DEFAULT_MAX_TURNS),
        yolo=bool(data.get("yolo")),
        category=str(data.get("category") or "").strip(),
        env=env,
        run_plugins=tuple(str(x).strip() for x in plugins if str(x).strip())
        if isinstance(plugins, list)
        else (),
        run_skills=tuple(str(x).strip() for x in skills if str(x).strip())
        if isinstance(skills, list)
        else (),
        run_mcp_servers=tuple(str(x).strip() for x in mcp if str(x).strip())
        if isinstance(mcp, list)
        else (),
    )


def task_definition_from_source(src: TaskExportSource) -> TaskDefinition:
    """Build a validated :class:`TaskDefinition` (batch-loadable fields only)."""
    prompt = (src.prompt or "").strip()
    if not prompt:
        raise ValueError("prompt is required to export a task")
    tid = slug_task_id(src.task_id or src.description or "exported-task")
    setup = (src.setup_instructions or "").strip() or None
    data: JsonObject = {
        "task_id": tid,
        "prompt": prompt,
        "docker_image": (src.docker_image or "fully-loaded").strip() or "fully-loaded",
        "repo_url": (src.repo_url or "").strip(),
        "repo_branch": (src.repo_branch or "").strip(),
        "repo_path": (src.repo_path or "").strip(),
        "persona_id": (src.persona_id or "").strip(),
        "models": list(src.models),
        "max_turns": normalize_max_turns(src.max_turns, default=DEFAULT_MAX_TURNS),
        "yolo": bool(src.yolo),
    }
    if (src.description or "").strip():
        data["description"] = src.description.strip()
    if (src.category or "").strip():
        data["category"] = src.category.strip()
    if setup:
        data["setup_instructions"] = setup
    if src.env:
        data["env"] = dict(src.env)
    return TaskDefinition.model_validate(data)


def render_task_catalog_yaml(src: TaskExportSource) -> str:
    """Render a one-task catalog YAML string (with schema header + notes)."""
    task = task_definition_from_source(src)
    doc = TaskFile(schema_version=SCHEMA_VERSION, defaults=None, tasks=[task])
    payload = doc.model_dump(mode="python", exclude_none=True)
    # Drop empty defaults key if None was coerced away
    if payload.get("defaults") is None:
        payload.pop("defaults", None)
    # Compact empty strings that pydantic still emits for optional blanks we keep
    task_map = payload.get("tasks")
    if isinstance(task_map, list) and task_map and isinstance(task_map[0], dict):
        t0 = task_map[0]
        for key in (
            "repo_url",
            "repo_branch",
            "repo_path",
            "persona_id",
            "description",
            "category",
            "domain",
            "horizon",
            "setup_instructions",
            "setup",
            "initial_commands",
            "resume_session_dir",
            "resume_session_id",
        ):
            if key in t0 and t0[key] in ("", None, []):
                del t0[key]
        if not t0.get("models"):
            t0.pop("models", None)
        if not t0.get("env"):
            t0.pop("env", None)
        if not t0.get("tags"):
            t0.pop("tags", None)
        if not t0.get("turns"):
            t0.pop("turns", None)
        if not t0.get("success_hints"):
            t0.pop("success_hints", None)
        if t0.get("yolo") is False:
            t0.pop("yolo", None)
        if t0.get("max_turns") == DEFAULT_MAX_TURNS:
            t0.pop("max_turns", None)

    body = yaml.safe_dump(
        payload,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    notes: list[str] = [
        "# Exported by groket (run config / runner / session recipe).",
        f"# task_id: {task.task_id}",
    ]
    if src.run_plugins or src.run_skills or src.run_mcp_servers:
        notes.append("# Note: run-only plugins/skills/MCP extras are not batch task fields;")
        notes.append("# put them on the persona (or re-apply as runner extras after import).")
        if src.run_plugins:
            notes.append("# run_plugins: " + ", ".join(src.run_plugins))
        if src.run_skills:
            notes.append("# run_skills: " + ", ".join(src.run_skills))
        if src.run_mcp_servers:
            notes.append("# run_mcp_servers: " + ", ".join(src.run_mcp_servers))
    notes.append("#")
    notes.append("#   uv run groket batch validate <this-file>")
    notes.append(f"#   uv run groket batch run -t <this-file> -i {task.task_id} -m <model>")
    notes.append("")
    return _SCHEMA_COMMENT + "\n".join(notes) + "\n" + body


def write_task_export(path: Path, src: TaskExportSource) -> Path:
    """Write a task catalog YAML to *path* (parents created).

    :returns: Resolved path written.
    :raises ValueError: Missing prompt or invalid task fields.
    :raises OSError: Path not writable.
    """
    out = Path(path).expanduser()
    if out.suffix.lower() not in (".yaml", ".yml"):
        out = out.with_suffix(".yaml")
    text = render_task_catalog_yaml(src)
    # Validate round-trip before write
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    try:
        load_task_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out.resolve()


__all__ = [
    "TaskExportSource",
    "default_task_export_path",
    "render_task_catalog_yaml",
    "slug_task_id",
    "source_from_recipe_mapping",
    "source_from_run_config",
    "task_definition_from_source",
    "write_task_export",
]
