"""Semantic style constants for Rich markup and Text objects.

All Python-side color choices live here.  Screens and widgets import from
this module instead of hardcoding color names.  The TCSS layer (``app.tcss``)
uses Textual ``$`` design tokens and is maintained separately.

One-off structural markup (``[dim]``, ``[bold]``) is fine inline — only
*semantic concepts* that repeat across files belong in this module.
"""

from __future__ import annotations

from contextlib import suppress

from textual.app import App

# Brand hex (same as brand/build.py). Caps = complete / failed / running.
COMPLETE = "#98971A"
FAILED = "#CC241D"
RUNNING = "#D79921"
CANCELLED = "#928374"
CREAM = "#FBF1C7"

SEVERITY_STYLE: dict[str, str] = {
    "high": f"{FAILED} bold",
    "medium": f"{RUNNING} bold",
    "low": RUNNING,
}

SEVERITY_LABEL: dict[str, str] = {
    "high": f"[{FAILED} bold]High[/]",
    "medium": f"[{RUNNING} bold]Medium[/]",
    "low": f"[{RUNNING}]Low[/]",
}


def finding_mark(severity: str) -> str:
    """Timeline / report glyph for an automated finding (⚠ + severity color)."""
    sev = (severity or "low").lower()
    style = SEVERITY_STYLE.get(sev, SEVERITY_STYLE["low"])
    return f"[{style}]⚠[/]"


# Small palette by *role* (not a rainbow per label):
#   cream  = human input / model stream
#   complete green = tools / writes
#   running yellow = session runtime
#   failed red = error

# Grok sessionUpdate / events.jsonl type → Rich style (identity keys).
EVENT_TYPE_STYLE: dict[str, str] = {
    "user_message_chunk": f"bold {CREAM}",
    "agent_message_chunk": CREAM,
    "agent_thought_chunk": f"dim {CREAM} italic",
    "plan": CREAM,
    "tool_call": f"bold {COMPLETE}",
    "tool_call_update": f"dim {COMPLETE}",
    "task_backgrounded": f"bold {RUNNING}",
    "task_completed": RUNNING,
    "turn_completed": RUNNING,
    "subagent_spawned": CREAM,
    "subagent_finished": CREAM,
    "current_mode_update": f"dim {RUNNING}",
    "retry_state": f"dim {RUNNING}",
    "turn_started": RUNNING,
    "turn_ended": RUNNING,
    "session_error": f"bold {FAILED}",
    "error": f"bold {FAILED}",
    "turn_error": f"bold {FAILED}",
    "fatal_error": f"bold {FAILED}",
    "system": CANCELLED,
    # legacy pre-taxonomy names (cached / old tests)
    "user": f"bold {CREAM}",
    "assistant": CREAM,
    "thought": f"dim {CREAM} italic",
    "tool_result": f"dim {COMPLETE}",
    "subagent": CREAM,
    "session": RUNNING,
}

# Type column uses Grok identifiers (spaces from underscores in type_label).
EVENT_TYPE_LABEL: dict[str, str] = {
    k: f"[{v}]{k.replace('_', ' ')}[/]" for k, v in EVENT_TYPE_STYLE.items()
}

# Color by *action family*, not per-tool identity (keeps the column scannable):
#   cream  = read / search / inspect
#   complete green = write / edit / mutate workspace
#   running yellow = shell / process / wait
#   cream  = agent / UI / plan / other (default)

_TOOL_FAMILY_READ = frozenset(
    {
        "read_file",
        "grep",
        "list_dir",
        "web_search",
        "read_resource",
        "list_resources",
    }
)
_TOOL_FAMILY_WRITE = frozenset(
    {
        "search_replace",
        "write_file",
        "create_file",
        "todo_write",
        "update_goal",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
    }
)
_TOOL_FAMILY_SHELL = frozenset(
    {
        "run_terminal_command",
        "get_command_or_subagent_output",
        "kill_command_or_subagent",
        "wait_commands_or_subagents",
        "monitor",
        "scheduler_create",
        "scheduler_delete",
        "scheduler_list",
    }
)
_TOOL_FAMILY_AGENT = frozenset(
    {
        "spawn_subagent",
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode",
        "use_tool",
        "search_tool",
        "call_mcp",
        "search_mcp",
    }
)

TOOL_FAMILY_STYLE: dict[str, str] = {
    "read": CREAM,
    "write": COMPLETE,
    "shell": RUNNING,
    "agent": CREAM,
    "mcp": CANCELLED,
    "other": "dim",
}

# Explicit overrides (optional); prefer families via tool_style().
TOOL_STYLE: dict[str, str] = {}


def tool_family(name: str) -> str:
    """Map a tool name to read | write | shell | agent | mcp | other."""
    n = (name or "").strip()
    # MCP / plugin tools often look like server__method
    if "__" in n or n.startswith("mcp_"):
        return "mcp"
    if n in _TOOL_FAMILY_READ:
        return "read"
    if n in _TOOL_FAMILY_WRITE:
        return "write"
    if n in _TOOL_FAMILY_SHELL:
        return "shell"
    if n in _TOOL_FAMILY_AGENT:
        return "agent"
    # Heuristics for MCP / unknown tools
    low = n.lower()
    if any(k in low for k in ("read", "get", "list", "search", "grep", "find")):
        return "read"
    if any(k in low for k in ("write", "edit", "create", "update", "delete", "save")):
        return "write"
    if any(k in low for k in ("run", "exec", "shell", "terminal", "wait", "kill")):
        return "shell"
    return "other"


def format_tool_display(name: str) -> str:
    """Human tool column text: host ids stay snake_case; MCP as ``server · method``."""
    n = (name or "").strip()
    if not n:
        return "?"
    if "__" in n:
        server, method = n.split("__", 1)
        server, method = server.strip(), method.strip()
        if server and method:
            return f"{server} · {method}"
    if n.startswith("mcp_") and len(n) > 4:
        return f"mcp · {n[4:]}"
    return n


# Run / container lifecycle — one palette for tables, activity bar, labels.
STATUS_RICH_STYLE: dict[str, str] = {
    "pending": "dim",
    "building": f"bold {RUNNING}",
    "running": f"bold {RUNNING}",
    "ending": f"bold {CANCELLED}",
    "awaiting": f"bold {CANCELLED}",
    "extracting": f"bold {RUNNING}",
    "completed": f"bold {COMPLETE}",
    "failed": f"bold {FAILED}",
    "idle": "dim",
}

STATUS_LABEL: dict[str, str] = {
    "pending": f"[{STATUS_RICH_STYLE['pending']}]Pending[/]",
    "building": f"[{STATUS_RICH_STYLE['building']}]Building…[/]",
    "running": f"[{STATUS_RICH_STYLE['running']}]Running…[/]",
    "ending": f"[{STATUS_RICH_STYLE['ending']}]Ending…[/]",
    "extracting": f"[{STATUS_RICH_STYLE['extracting']}]Extracting…[/]",
    "completed": f"[{STATUS_RICH_STYLE['completed']}]Completed[/]",
    "failed": f"[{STATUS_RICH_STYLE['failed']}]Failed[/]",
}


def status_rich_style(status: str) -> str:
    """Rich style for a container/run status name (``running``, ``failed``, …)."""
    return STATUS_RICH_STYLE.get((status or "").strip().lower(), STATUS_RICH_STYLE["idle"])


SYNTAX_THEME_LIGHT = "friendly"
SYNTAX_THEME_DARK = "monokai"


def syntax_theme_for_app(app: App) -> str:
    """Pick a Syntax highlight theme matching the active Textual theme.

    ``app`` is the Textual ``App`` instance (or any object with a ``.theme``
    attribute).  Falls back to the dark theme when detection fails.
    """
    with suppress(Exception):
        name = getattr(app, "theme", "") or ""
        if "light" in name.lower():
            return SYNTAX_THEME_LIGHT
    return SYNTAX_THEME_DARK


def severity_style(value: str) -> str:
    """Rich style string for a severity value (``"high"`` / ``"medium"`` / ``"low"``)."""
    return SEVERITY_STYLE.get(value, "white")


def tool_style(name: str) -> str:
    """Rich style for a tool name (family palette, optional per-name override)."""
    if name and name in TOOL_STYLE:
        return TOOL_STYLE[name]
    return TOOL_FAMILY_STYLE.get(tool_family(name or ""), TOOL_FAMILY_STYLE["other"])


def tool_label(name: str, *, max_len: int = 32) -> str:
    """Rich markup label for a tool name in tables (MCP shown as server · method)."""
    display = format_tool_display(name or "?")
    if len(display) > max_len:
        display = display[: max_len - 1] + "…"
    style = tool_style(name)
    safe = display.replace("[", "\\[").replace("]", "\\]")
    return f"[{style}]{safe}[/]"
