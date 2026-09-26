"""Eval run domain — personas, recipes, batch launch, background manager, shares.

UI screens import from here (or submodules); never the reverse.
"""

from __future__ import annotations

from .batch import (
    EvalTask,
    active_model_catalog,
    active_model_ids,
    default_model_id,
    load_models,
    load_tasks,
    resolve_model_ids,
    run_batch,
    validate_models_for_launch,
)
from .live_share import get_share_display, get_share_url, refresh_share_from_disk
from .personas import Persona, PersonaStore, personas_dir
from .run_configs import RunConfig, RunConfigStore
from .run_manager import BackgroundRun, RunManager
from .services import LogBuffer

__all__ = [
    "BackgroundRun",
    "EvalTask",
    "LogBuffer",
    "Persona",
    "PersonaStore",
    "RunConfig",
    "RunConfigStore",
    "RunManager",
    "active_model_catalog",
    "active_model_ids",
    "default_model_id",
    "get_share_display",
    "get_share_url",
    "load_models",
    "load_tasks",
    "personas_dir",
    "refresh_share_from_disk",
    "resolve_model_ids",
    "run_batch",
    "validate_models_for_launch",
]
