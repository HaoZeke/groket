"""Session-derived views on traces (usage stats, workspace diffs).

Complements :mod:`groket.parser` (raw timeline/tool extraction) with higher-level
aggregates used by the session Summary pane — still UI-agnostic.
"""

from __future__ import annotations

from .export_bundle import (
    ExportBundleResult,
    export_session_bundle,
    run_volume_for_session,
)
from .export_spec import (
    DEFAULT_PROFILE_ID,
    ExportSpec,
    IncludeUnit,
    Packaging,
    get_export_profile,
    list_export_profiles,
)
from .sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    SessionScanRoot,
    collect_session_dirs,
    host_grok_sessions_root,
    is_host_grok_sessions_root,
    is_under_host_grok_sessions,
    session_scan_roots,
    work_traces_root,
)
from .usage_stats import (
    McpServerUsage,
    SessionUsageStats,
    SkillUsageRow,
    ToolUsageRow,
    collect_session_usage,
    format_usage_markdown,
    format_usage_plain,
    format_usage_stats_text,
)
from .workspace_diff import format_diff_meta_line, load_workspace_diff

__all__ = [
    "DEFAULT_PROFILE_ID",
    "ORIGIN_HOST",
    "ORIGIN_WORK",
    "ExportBundleResult",
    "ExportSpec",
    "IncludeUnit",
    "McpServerUsage",
    "Packaging",
    "SessionScanRoot",
    "SessionUsageStats",
    "SkillUsageRow",
    "ToolUsageRow",
    "collect_session_dirs",
    "collect_session_usage",
    "export_session_bundle",
    "format_diff_meta_line",
    "format_usage_markdown",
    "format_usage_plain",
    "format_usage_stats_text",
    "get_export_profile",
    "host_grok_sessions_root",
    "is_host_grok_sessions_root",
    "is_under_host_grok_sessions",
    "list_export_profiles",
    "load_workspace_diff",
    "run_volume_for_session",
    "session_scan_roots",
    "work_traces_root",
]
