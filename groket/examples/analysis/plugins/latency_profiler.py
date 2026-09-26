"""Latency profiler — measures tool call durations and identifies slow operations.

Pairs tool_call and tool_result events from the timeline to compute
per-call durations. Flags tool calls that exceed the slow threshold.

Config:
    "plugins": ["latency_profiler:LatencyProfilerAnalyzer"]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import JsonObject, Severity
from groket.parser import load_session_meta, parse_timeline

logger = logging.getLogger(__name__)

SLOW_TOOL_THRESHOLD_SECONDS = 30.0

class LatencyProfilerAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="latency-profiler",
            name="Latency Profiler",
            description="Profiles tool call durations and flags slow operations",
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        timeline = parse_timeline(session_dir)
        meta = load_session_meta(session_dir)

        if not timeline:
            return AnalysisResult(
                session_id=session_dir.name,
                analyzer_id="latency-profiler",
                ok=True,
                summary="No timeline data",
            )


        call_timestamps: dict[str, int] = {}
        tool_durations: list[JsonObject] = []

        for ev in timeline:
            if ev.event_type == "tool_call" and ev.tool_call_id and ev.timestamp:
                call_timestamps[ev.tool_call_id] = ev.timestamp
            elif ev.event_type == "tool_result" and ev.tool_call_id and ev.timestamp:
                start = call_timestamps.get(ev.tool_call_id)
                if start is not None:
                    duration = ev.timestamp - start
                    tool_durations.append({
                        "tool_call_id": ev.tool_call_id,
                        "tool_name": ev.tool_name or "unknown",
                        "duration_seconds": duration,
                        "slow": duration > SLOW_TOOL_THRESHOLD_SECONDS,
                    })

        total_seconds = meta.duration_seconds or 0
        slow_tools = [t for t in tool_durations if t["slow"]]
        slowest = max(
            (t["duration_seconds"] for t in tool_durations), default=0.0,
        )
        tool_count = sum(1 for ev in timeline if ev.event_type == "tool_call")

        findings = [
            Finding(
                id=f"slow-tool-{t['tool_call_id'][:12]}",
                plugin_id="latency-profiler",
                severity=Severity.MEDIUM,
                title=f"Slow tool call: {t['tool_name']} ({t['duration_seconds']:.0f}s)",
                detail=f"Tool call took {t['duration_seconds']}s (threshold: {SLOW_TOOL_THRESHOLD_SECONDS}s)",
                category="Slow Tool",
                tool_call_ids=[t["tool_call_id"]],
            )
            for t in slow_tools
        ]

        summary = (
            f"{tool_count} tool calls, {total_seconds:.0f}s total, "
            f"{len(slow_tools)} slow (>{SLOW_TOOL_THRESHOLD_SECONDS:.0f}s), "
            f"slowest: {slowest:.0f}s"
        )

        return AnalysisResult(
            session_id=session_dir.name,
            analyzer_id="latency-profiler",
            ok=True,
            findings=findings,
            summary=summary,
            extras={
                "total_seconds": round(total_seconds, 2),
                "tool_count": tool_count,
                "slow_tool_count": len(slow_tools),
                "slowest_tool_seconds": slowest,
                "durations": tool_durations[:20],
            },
        )

