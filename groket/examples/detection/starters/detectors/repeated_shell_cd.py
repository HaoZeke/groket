"""Example user detector — flag many consecutive shell `cd` commands.

Install for local use (copy, do not edit package source)::

    mkdir -p ~/.groket/detectors
    cp examples/detection/starters/detectors/repeated_shell_cd.py ~/.groket/detectors/

Or generate a stub and adapt it::

    uv run groket gen detector repeated_shell_cd

Pair with the example rule (see ``examples/detection/starters/rules/``)::

    cp examples/detection/starters/rules/repeated-shell-cd.yaml ~/.groket/rules/

Restart the TUI (or reload rules) so the engine imports detectors and merges rules.
"""

from __future__ import annotations

from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, ToolCall


@detector("repeated_shell_cd")
def detect_repeated_shell_cd(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Count consecutive terminal ``cd`` invocations; fire if streak >= min_streak."""
    _ = messages
    min_streak = params.as_int("min_streak", 4)
    tool_names = params.as_str_list("tools", ["run_terminal_command", "bash", "shell"])
    if isinstance(tool_names, str):
        tool_names = [tool_names]
    allowed = {str(t).lower() for t in tool_names}

    streak: list[ToolCall] = []
    best: list[ToolCall] = []

    for tc in tool_calls:
        name = (tc.tool_name or "").lower()
        cmd = tc.raw_input.get("command", "")
        if not isinstance(cmd, str):
            cmd = str(cmd)
        is_cd = name in allowed and cmd.strip().startswith("cd ")
        if is_cd:
            streak.append(tc)
            if len(streak) > len(best):
                best = list(streak)
        else:
            streak = []

    if len(best) < min_streak:
        return []

    return [
        Match(
            tool_calls=best,
            variables={
                "count": len(best),
                "min_streak": min_streak,
                "first_cmd": (best[0].raw_input.get("command") or "")[:80],
            },
        )
    ]
