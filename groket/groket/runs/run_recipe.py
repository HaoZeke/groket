"""Run recipe manifest (``run.json``) for launch reuse / fork prefill.

Written at **container start** under the traces volume so interactive and
interrupted sessions still expose persona / run-only extras for fork and
re-run. Also written to each session dir when the run finishes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import JsonObject, as_json_object
from ..paths import is_run_dir_name

if TYPE_CHECKING:
    from ..docker.orchestrator import ContainerConfig
    from ..models import EvalRun

logger = logging.getLogger(__name__)

RUN_RECIPE_FILENAME = "run.json"


def build_run_recipe(
    *,
    run_id: str = "",
    prompt: str = "",
    repo_url: str = "",
    repo_branch: str = "",
    repo_path: str = "",
    docker_image: str = "",
    setup_instructions: str = "",
    models: list[str] | None = None,
    persona_id: str = "",
    run_plugins: list[str] | None = None,
    run_skills: list[str] | None = None,
    run_mcp_servers: list[str] | None = None,
    sessions: dict[str, str] | None = None,
    created_at: str = "",
) -> JsonObject:
    """Build the operator-facing run recipe (persona id + run-only extras)."""
    return {
        "run_id": (run_id or "").strip(),
        "created_at": (created_at or datetime.now(UTC).isoformat()).strip(),
        "prompt": prompt or "",
        "repo_url": repo_url or "",
        "repo_branch": repo_branch or "",
        "repo_path": repo_path or "",
        "docker_image": docker_image or "",
        "setup_instructions": setup_instructions or "",
        "models": list(models or []),
        "sessions": dict(sessions or {}),
        "persona_id": (persona_id or "").strip(),
        "run_plugins": list(run_plugins or []),
        "run_skills": list(run_skills or []),
        "run_mcp_servers": list(run_mcp_servers or []),
    }


def recipe_from_container_config(
    config: ContainerConfig,
    *,
    models: list[str] | None = None,
    sessions: dict[str, str] | None = None,
) -> JsonObject:
    """Recipe fields from a container config (persona + run-only extras)."""
    from .batch import join_model_effort

    model_token = join_model_effort(config.model, config.reasoning_effort)
    model_list = list(models) if models is not None else ([model_token] if model_token else [])
    return build_run_recipe(
        run_id=config.run_id or "",
        prompt=config.prompt or "",
        repo_url=config.repo_url or "",
        repo_branch=config.repo_branch or "",
        repo_path=config.repo_path or "",
        docker_image=config.docker_image or "",
        setup_instructions=config.setup_instructions or "",
        models=model_list,
        persona_id=config.persona_id or "",
        run_plugins=list(config.run_plugins or []),
        run_skills=list(config.run_skills or []),
        run_mcp_servers=list(config.run_mcp_servers or []),
        sessions=sessions,
    )


def recipe_from_background(
    *,
    run_id: str,
    eval_run: EvalRun,
    config: ContainerConfig | None,
    persona_id: str = "",
    sessions: dict[str, str] | None = None,
) -> JsonObject:
    """Recipe for end-of-run write from BackgroundRun fields."""
    return build_run_recipe(
        run_id=run_id,
        prompt=getattr(eval_run, "prompt", "") or "",
        repo_url=getattr(eval_run, "repo_url", "") or "",
        repo_branch=getattr(eval_run, "repo_branch", "") or "",
        repo_path=getattr(eval_run, "repo_path", "") or "",
        docker_image=getattr(eval_run, "docker_image", "") or "",
        setup_instructions=getattr(eval_run, "setup_instructions", "") or "",
        models=list(getattr(eval_run, "models", None) or []),
        persona_id=(persona_id or (config.persona_id if config else "") or "").strip(),
        run_plugins=list(config.run_plugins or []) if config else [],
        run_skills=list(config.run_skills or []) if config else [],
        run_mcp_servers=list(config.run_mcp_servers or []) if config else [],
        sessions=sessions,
    )


def write_run_recipe(dest: Path, recipe: JsonObject) -> Path:
    """Write *recipe* as ``run.json`` under *dest* (file or directory).

    :param dest: Directory to place ``run.json`` in, or an explicit ``run.json`` path.
    :returns: Path written.
    """
    path = dest if dest.name == RUN_RECIPE_FILENAME else dest / RUN_RECIPE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    return path


def write_run_recipe_for_config(traces_vol: Path, config: ContainerConfig) -> Path | None:
    """Persist launch recipe on the traces volume at container start."""
    try:
        recipe = recipe_from_container_config(config)
        return write_run_recipe(traces_vol, recipe)
    except OSError:
        logger.warning("Failed to write run recipe under %s", traces_vol, exc_info=True)
        return None


def _try_load(path: Path) -> JsonObject | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Failed to read run recipe %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    return as_json_object(data)


def find_run_recipe_path(session_dir: Path) -> Path | None:
    """Locate ``run.json`` for a session (session dir, run volume, fork parent seed)."""
    sd = Path(session_dir)
    direct = sd / RUN_RECIPE_FILENAME
    if direct.is_file():
        return direct
    for anc in sd.parents:
        candidate = anc / RUN_RECIPE_FILENAME
        if candidate.is_file():
            return candidate
        if is_run_dir_name(anc.name) or anc.name == "traces":
            break
    try:
        from ..session.resume import fork_parent_session_dir

        parent = fork_parent_session_dir(sd)
    except Exception:
        logger.debug("fork parent lookup failed for %s", sd, exc_info=True)
        parent = None
    if parent is not None:
        parent_recipe = parent / RUN_RECIPE_FILENAME
        if parent_recipe.is_file():
            return parent_recipe
        for anc in parent.parents:
            candidate = anc / RUN_RECIPE_FILENAME
            if candidate.is_file():
                return candidate
            if is_run_dir_name(anc.name) or anc.name == "traces":
                break
    return None


def load_run_recipe(session_dir: Path) -> JsonObject:
    """Load run recipe for *session_dir*, or ``{}`` when absent/invalid."""
    path = find_run_recipe_path(session_dir)
    if path is None:
        return {}
    data = _try_load(path)
    return data if data is not None else {}
