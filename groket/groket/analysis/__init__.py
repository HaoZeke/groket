"""Pluggable analysis — enabled plugins run on every analysis call.

Built-ins: ``basic``, ``engine`` (always enabled). Everything else is
external: load via ``analysis.plugins`` in config.json (see ``plugins/``
and ``examples/analysis/plugins/``). Only analyzers registered for *this* config
are enabled — not leftovers in the process registry or on-disk cache.

All UI/CLI/screen code should use :class:`AnalysisService` as the sole
interface — never import a specific analyzer or engine module directly.
"""

from __future__ import annotations

from .base import (
    AnalysisResult,
    Analyzer,
    AnalyzerInfo,
    Finding,
    NoopAnalyzer,
)
from .config import AnalysisPipelineConfig, load_pipeline_config, save_pipeline_config
from .registry import (
    BUILTIN_ANALYZER_IDS,
    analyze_session_path,
    analyzer_from_module_attr,
    get_analyzer,
    list_analyzers,
    load_config_plugins,
    register_analyzer,
    set_default_analyzer,
)

__all__ = [
    "AnalysisPipelineConfig",
    "AnalysisResult",
    "AnalysisService",
    "Analyzer",
    "AnalyzerInfo",
    "BUILTIN_ANALYZER_IDS",
    "Finding",
    "NoopAnalyzer",
    "analyze_session_path",
    "get_analysis_service",
    "get_analyzer",
    "list_analyzers",
    "analyzer_from_module_attr",
    "load_config_plugins",
    "load_pipeline_config",
    "register_analyzer",
    "save_pipeline_config",
    "set_analysis_service",
    "set_default_analyzer",
]

_SERVICE_EXPORTS = frozenset({"AnalysisService", "get_analysis_service", "set_analysis_service"})


def __getattr__(name: str) -> object:
    """Load the service façade and built-in plugins on first use."""
    if name in _SERVICE_EXPORTS:
        from . import service as _service
        from .registry import ensure_builtins

        ensure_builtins()
        return getattr(_service, name)
    import importlib

    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
