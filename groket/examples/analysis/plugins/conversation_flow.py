"""Conversation flow analyzer — maps interaction patterns in a session.

Identifies loops (repeated tool calls), error-recovery patterns, planning vs
execution ratio, and conversation depth.  Useful for spotting sessions where
the model got stuck in retry loops or spent too long planning.

Config:
    "plugins": ["conversation_flow:ConversationFlowAnalyzer"]
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import JsonObject, Severity
from groket.parser import parse_timeline, parse_tool_calls

logger = logging.getLogger(__name__)

LOOP_THRESHOLD = 4

class ConversationFlowAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="conversation-flow",
            name="Conversation Flow",
            description="Maps interaction patterns — loops, retries, planning vs execution",
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        tool_calls = parse_tool_calls(session_dir)
        timeline = parse_timeline(session_dir)

        if not tool_calls and not timeline:
            return AnalysisResult(
                session_id=session_dir.name,
                analyzer_id="conversation-flow",
                ok=True,
                summary="No trace data",
            )


        type_counts: Counter[str] = Counter()
        for ev in timeline:
            type_counts[ev.event_type] += 1

        user_turns = type_counts.get("user", 0)
        assistant_turns = type_counts.get("assistant", 0)


        tool_sequence = [tc.tool_name for tc in tool_calls]
        tool_counts: Counter[str] = Counter(tool_sequence)
        error_count = sum(1 for tc in tool_calls if tc.is_error)


        loops: list[JsonObject] = []
        if tool_sequence:
            current_tool = tool_sequence[0]
            run_length = 1
            for tool in tool_sequence[1:]:
                if tool == current_tool:
                    run_length += 1
                else:
                    if run_length >= LOOP_THRESHOLD:
                        loops.append({"tool": current_tool, "consecutive_calls": run_length})
                    current_tool = tool
                    run_length = 1
            if run_length >= LOOP_THRESHOLD:
                loops.append({"tool": current_tool, "consecutive_calls": run_length})


        read_tools = {"read_file", "list_dir", "grep"}
        write_tools = {"search_replace", "write_file", "create_file", "edit_file"}
        exec_tools = {"run_terminal_command"}

        read_count = sum(tool_counts[t] for t in read_tools if t in tool_counts)
        write_count = sum(tool_counts[t] for t in write_tools if t in tool_counts)
        exec_count = sum(tool_counts[t] for t in exec_tools if t in tool_counts)
        total_tools = len(tool_sequence)

        findings: list[Finding] = []
        for loop in loops:
            findings.append(Finding(
                id=f"retry-loop-{loop['tool']}",
                plugin_id="conversation-flow",
                severity=Severity.MEDIUM,
                title=f"Retry loop: {loop['tool']} called {loop['consecutive_calls']}x consecutively",
                category="Retry Loop",
            ))
        if total_tools > 3 and error_count > total_tools * 0.3:
            rate = error_count / max(total_tools, 1)
            findings.append(Finding(
                id="high-error-rate",
                plugin_id="conversation-flow",
                severity=Severity.HIGH,
                title=f"High error rate: {error_count}/{total_tools} ({rate:.0%})",
                category="Error Rate",
            ))

        autonomy = assistant_turns / max(user_turns, 1)
        summary = (
            f"{assistant_turns} assistant turns, {total_tools} tool calls "
            f"(read={read_count} write={write_count} exec={exec_count}), "
            f"{error_count} errors, {len(loops)} loop(s), "
            f"autonomy={autonomy:.1f}x"
        )

        return AnalysisResult(
            session_id=session_dir.name,
            analyzer_id="conversation-flow",
            ok=True,
            findings=findings,
            summary=summary,
            extras={
                "tool_counts": dict(tool_counts.most_common(15)),
                "type_counts": dict(type_counts),
                "loops": loops,
                "error_count": error_count,
                "autonomy_ratio": round(autonomy, 2),
                "read_calls": read_count,
                "write_calls": write_count,
                "exec_calls": exec_count,
            },
        )

