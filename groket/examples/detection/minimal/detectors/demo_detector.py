"""Minimal detector for docs/tests: flags error terminal calls."""
from __future__ import annotations

from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, ToolCall


@detector("demo_error_shell")
def demo_error_shell(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    _ = messages, params
    out: list[Match] = []
    for tc in tool_calls:
        if tc.tool_name == "run_terminal_command" and tc.is_error:
            out.append(
                Match(
                    tool_calls=[tc],
                    variables={"command": str(tc.input_str("command") or "")[:80]},
                )
            )
    return out
