"""User extension scaffolding (``groket gen``)."""

from __future__ import annotations

from .scaffold import (
    append_analysis_plugin_to_config,
    slug_name,
    write_analysis_plugin,
    write_detector,
    write_rule,
    write_tasks_file,
)

__all__ = [
    "append_analysis_plugin_to_config",
    "slug_name",
    "write_analysis_plugin",
    "write_detector",
    "write_rule",
    "write_tasks_file",
]
