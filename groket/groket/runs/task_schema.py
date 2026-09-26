"""Pydantic models and JSON Schema for batch ``tasks.yaml`` files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from groket.constants import DEFAULT_MAX_TURNS
from groket.models import JsonObject

if TYPE_CHECKING:
    from .batch import EvalTask

SCHEMA_VERSION = 1
SCHEMA_TITLE = "groket-tasks"
SCHEMA_ID = "https://indynull.github.io/groket/schemas/tasks.schema.json"


class TaskDefaults(BaseModel):
    """Document-level defaults inherited by each task unless overridden."""

    model_config = ConfigDict(extra="forbid")

    docker_image: str | None = None
    description: str | None = None
    category: str | None = None
    domain: str | None = None
    horizon: str | None = None
    persona_id: str | None = None
    models: list[str] | None = None
    env: dict[str, str] | None = None
    max_turns: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Grok --max-turns (agent steps per prompt). "
            f"Default {DEFAULT_MAX_TURNS} when omitted on a task."
        ),
    )


class TaskDefinition(BaseModel):
    """One evaluation task entry under ``tasks:``."""

    model_config = ConfigDict(extra="ignore")  # allow unknown keys with warn path in loader

    task_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    repo_url: str = ""
    repo_branch: str = ""
    # Host directory bind-mounted as /workspace (live tree; no git clone / CoW).
    repo_path: str = Field(
        default="",
        description=(
            "Absolute or ~ path on the host to bind-mount as /workspace. "
            "Edits land in that directory. Single model only (no multi-model fan-out)."
        ),
    )
    initial_commands: str | list[str] | None = None
    setup_instructions: str | list[str] | None = None
    setup: str | list[str] | None = None
    docker_image: str = "fully-loaded"
    description: str = ""
    category: str = "regular"
    domain: str = "general-swe"
    horizon: str = "long"
    persona_id: str = ""
    models: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # Scripted follow-ups after the primary prompt (plain strings).
    # Also accepts legacy ``[{prompt: "…"}]`` maps for older catalogs.
    turns: list[str] = Field(default_factory=list)
    success_hints: list[str] = Field(default_factory=list)
    # Fork an ended on-disk Grok session (same as TUI f): seed + --resume --fork-session.
    # ``prompt`` is the first message on the new branch; ``turns`` are further scripted turns.
    resume_session_dir: str = Field(
        default="",
        description=(
            "Host path to an ended session directory (…/%2Fworkspace/<session_id>/). "
            "Launches fork from that history; new Grok session id per run."
        ),
    )
    resume_session_id: str = Field(
        default="",
        description=(
            "Optional parent Grok session id when resuming. Defaults to the "
            "basename of resume_session_dir when empty."
        ),
    )
    max_turns: int = Field(
        default=DEFAULT_MAX_TURNS,
        ge=1,
        description=(
            "Grok --max-turns: agent tool/plan steps allowed per prompt "
            f"(default {DEFAULT_MAX_TURNS})."
        ),
    )
    yolo: bool = Field(
        default=False,
        description=(
            "When true, launch with ``grok --yolo`` (aggressive auto-approve). "
            "Default false uses ``--always-approve`` only."
        ),
    )

    @field_validator(
        "repo_url",
        "repo_branch",
        "repo_path",
        "persona_id",
        "resume_session_dir",
        "resume_session_id",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v: str | None) -> str:
        return "" if v is None else v

    @field_validator("turns", mode="before")
    @classmethod
    def _coerce_turns(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise TypeError("turns must be a list of strings (or {prompt: …} maps)")
        out: list[str] = []
        for item in v:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                raw = item.get("prompt", item.get("text", ""))
                text = str(raw).strip() if raw is not None else ""
            else:
                raise TypeError("each turn must be a string or a mapping with prompt")
            if text:
                out.append(text)
        return out

    def setup_shell(self) -> str:
        for val in (self.initial_commands, self.setup_instructions, self.setup):
            if val is None:
                continue
            if isinstance(val, list):
                return "\n".join(str(x) for x in val)
            text = str(val)
            if text.strip():
                return text
        return ""

    def effective_repo_branch(self) -> str:
        """Branch for clone, or empty to use the remote default HEAD.

        Do not invent ``main`` — many repos still use ``master`` (e.g. coredis).
        Empty string lets :func:`~groket.session.workspace._git_clone` omit
        ``--branch`` so git follows the remote's default.
        """
        return (self.repo_branch or "").strip()

    def resolved_resume_session_dir(self) -> Path | None:
        """Absolute path to the resume seed dir, or None when not set."""
        raw = (self.resume_session_dir or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()


class TaskFile(BaseModel):
    """Root document for a tasks YAML file."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    defaults: TaskDefaults | None = None
    tasks: list[TaskDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _need_tasks(self) -> Self:
        if not self.tasks:
            raise ValueError("tasks file must contain a non-empty 'tasks' list")
        return self

    def resolved_tasks(self) -> list[TaskDefinition]:
        """Apply document defaults onto each task (task fields win)."""
        d = self.defaults
        if d is None:
            return list(self.tasks)
        out: list[TaskDefinition] = []
        for t in self.tasks:
            data = t.model_dump()
            if d.docker_image and data.get("docker_image") == "fully-loaded":
                # only fill default image when task left the built-in default
                pass
            for key in (
                "docker_image",
                "description",
                "category",
                "domain",
                "horizon",
                "persona_id",
            ):
                dv = getattr(d, key, None)
                if dv is None or dv == "":
                    continue
                # inherit when task still at model default for strings
                cur = data.get(key)
                defaults_map = {
                    "docker_image": "fully-loaded",
                    "description": "",
                    "category": "regular",
                    "domain": "general-swe",
                    "horizon": "long",
                    "persona_id": "",
                }
                if cur == defaults_map.get(key, cur):
                    data[key] = dv
            if d.max_turns is not None and data.get("max_turns") == DEFAULT_MAX_TURNS:
                data["max_turns"] = int(d.max_turns)
            if d.models and not data.get("models"):
                data["models"] = list(d.models)
            if d.env:
                merged = dict(d.env)
                merged.update(data.get("env") or {})
                data["env"] = merged
            out.append(TaskDefinition.model_validate(data))
        return out


def load_task_file(path: Path) -> TaskFile:
    """Parse and validate a tasks YAML file."""
    tasks_path = Path(path).expanduser()
    if not tasks_path.is_file():
        raise FileNotFoundError(f"tasks file not found: {tasks_path}")
    with tasks_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("tasks file root must be a mapping")
    return TaskFile.model_validate(raw)


def validate_tasks_path(path: Path) -> TaskFile:
    """Validate *path*; raise ``ValueError`` / ``FileNotFoundError`` on failure."""
    return load_task_file(path)


def tasks_json_schema() -> JsonObject:
    """JSON Schema for TaskFile (draft 2020-12 via Pydantic)."""
    schema = TaskFile.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = SCHEMA_TITLE
    return schema


def emit_tasks_schema(out: Path | None = None) -> str:
    """Serialize schema JSON; optionally write *out*. Returns the JSON text."""
    text = json.dumps(tasks_json_schema(), indent=2) + "\n"
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return text


def task_definition_to_eval_task(task: TaskDefinition) -> EvalTask:
    """Build an :class:`~groket.runs.batch.EvalTask` from a validated task definition."""
    from .batch import EvalTask

    resume_dir = task.resolved_resume_session_dir()
    resume_sid = (task.resume_session_id or "").strip()
    if resume_dir is not None and not resume_sid:
        resume_sid = resume_dir.name
    from groket.constants import normalize_max_turns

    return EvalTask(
        task_id=task.task_id,
        prompt=task.prompt,
        repo_url=(task.repo_url or "").strip(),
        repo_branch=task.effective_repo_branch(),
        repo_path=(task.repo_path or "").strip(),
        setup_instructions=task.setup_shell(),
        docker_image=task.docker_image or "fully-loaded",
        description=task.description or "",
        category=task.category or "regular",
        domain=task.domain or "general-swe",
        horizon=task.horizon or "long",
        persona_id=(task.persona_id or "").strip(),
        models=list(task.models or []) or None,
        tags=list(task.tags or []) or None,
        env=dict(task.env or {}) or None,
        turns=list(task.turns) or None,
        success_hints=list(task.success_hints or []) or None,
        resume_session_dir=str(resume_dir) if resume_dir is not None else "",
        resume_session_id=resume_sid,
        max_turns=normalize_max_turns(task.max_turns, default=DEFAULT_MAX_TURNS),
        yolo=bool(task.yolo),
    )


__all__ = [
    "SCHEMA_ID",
    "SCHEMA_TITLE",
    "SCHEMA_VERSION",
    "TaskDefaults",
    "TaskDefinition",
    "TaskFile",
    "emit_tasks_schema",
    "load_task_file",
    "task_definition_to_eval_task",
    "tasks_json_schema",
    "validate_tasks_path",
]
