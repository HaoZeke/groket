"""Session health grader — scores sessions across multiple quality dimensions.

Computes a letter grade (A–F) based on tool error rate, retry density,
latency P95, and estimated token cost.  Emits a single summary Finding
with the overall grade plus per-dimension breakdowns.

Config:
    "plugins": ["session_health:SessionHealthAnalyzer"]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import Severity
from groket.parser import parse_timeline, parse_tool_calls

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4



ERROR_RATE_THRESHOLDS = (0.05, 0.20)
RETRY_DENSITY_THRESHOLDS = (0.10, 0.30)
LATENCY_P95_THRESHOLDS = (60, 180)
TOKEN_THRESHOLDS = (50_000, 120_000)

GRADE_LABELS = ["A", "B", "C", "D", "F"]

def _dimension_score(value: float, good: float, bad: float) -> float:
    """0.0 (best) to 1.0 (worst) for a value in [good..bad] range."""
    if value <= good:
        return 0.0
    if value >= bad:
        return 1.0
    return (value - good) / (bad - good)

def _letter_grade(score: float) -> str:
    """Convert 0..1 composite score to A–F letter grade."""
    if score <= 0.15:
        return "A"
    if score <= 0.35:
        return "B"
    if score <= 0.55:
        return "C"
    if score <= 0.75:
        return "D"
    return "F"

class SessionHealthAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="session-health",
            name="Session Health",
            description="Scores sessions A–F across error rate, retries, latency, and cost",
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        tool_calls = parse_tool_calls(session_dir)
        timeline = parse_timeline(session_dir)

        if not tool_calls and not timeline:
            return AnalysisResult(
                session_id=session_dir.name,
                analyzer_id="session-health",
                ok=True,
                summary="No trace data",
            )

        total_calls = len(tool_calls)
        error_calls = sum(1 for tc in tool_calls if tc.is_error)
        error_rate = error_calls / max(total_calls, 1)

        tool_sequence = [tc.tool_name for tc in tool_calls]
        retry_count = 0
        if tool_sequence:
            prev = tool_sequence[0]
            run = 1
            for t in tool_sequence[1:]:
                if t == prev:
                    run += 1
                else:
                    if run >= 4:
                        retry_count += run
                    prev = t
                    run = 1
            if run >= 4:
                retry_count += run
        retry_density = retry_count / max(total_calls, 1)

        durations: list[float] = []
        pending: dict[str, float] = {}
        for ev in timeline:
            if ev.event_type == "tool_call" and ev.tool_call_id:
                pending[ev.tool_call_id] = ev.timestamp
            elif ev.event_type == "tool_result" and ev.tool_call_id:
                start = pending.pop(ev.tool_call_id, None)
                if start is not None:
                    durations.append(ev.timestamp - start)

        if durations:
            durations.sort()
            p95_idx = int(len(durations) * 0.95)
            latency_p95 = durations[min(p95_idx, len(durations) - 1)]
        else:
            latency_p95 = 0.0

        total_chars = 0
        for ev in timeline:
            if isinstance(ev.content, str):
                total_chars += len(ev.content)
        for tc in tool_calls:
            total_chars += sum(len(str(v)) for v in tc.raw_input.values())
            total_chars += len(tc.result_content)
        est_tokens = total_chars // CHARS_PER_TOKEN

        scores = {
            "error_rate": _dimension_score(error_rate, *ERROR_RATE_THRESHOLDS),
            "retry_density": _dimension_score(retry_density, *RETRY_DENSITY_THRESHOLDS),
            "latency_p95": _dimension_score(latency_p95, *LATENCY_P95_THRESHOLDS),
            "token_usage": _dimension_score(est_tokens, *TOKEN_THRESHOLDS),
        }
        composite = sum(scores.values()) / len(scores)
        grade = _letter_grade(composite)

        severity = Severity.LOW
        if grade in ("D", "F"):
            severity = Severity.HIGH
        elif grade in ("B", "C"):
            severity = Severity.MEDIUM

        dim_lines = [
            f"Error rate: {error_rate:.0%} ({error_calls}/{total_calls})",
            f"Retry density: {retry_density:.0%} ({retry_count} calls in loops)",
            f"Latency P95: {latency_p95:.1f}s",
            f"Est. tokens: ~{est_tokens:,}",
        ]

        findings = [
            Finding(
                id="health-grade",
                plugin_id="session-health",
                severity=severity,
                title=f"Session grade: {grade}",
                detail="\n".join(dim_lines),
                category="Health",
                extras={
                    "grade": grade,
                    "composite_score": round(composite, 3),
                    "dimensions": {k: round(v, 3) for k, v in scores.items()},
                    "error_rate": round(error_rate, 4),
                    "retry_density": round(retry_density, 4),
                    "latency_p95": round(latency_p95, 2),
                    "est_tokens": est_tokens,
                },
            )
        ]

        return AnalysisResult(
            session_id=session_dir.name,
            analyzer_id="session-health",
            ok=True,
            findings=findings,
            summary=f"Grade {grade} (err={error_rate:.0%}, retries={retry_density:.0%}, "
                    f"P95={latency_p95:.0f}s, ~{est_tokens:,} tokens)",
        )

