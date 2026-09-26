"""Minimal built-in analyzer: session meta only (no detector engine)."""

from __future__ import annotations

from pathlib import Path

from ..models import JsonObject
from ..parser import load_session_meta
from .base import AnalysisResult, AnalyzeContext, AnalyzerInfo


class BasicAnalyzer:
    """Lightweight: load session meta and report turn outcome / tool count."""

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="basic",
            name="Basic",
            description="Session metadata only (outcome, model, tool count). No detectors.",
            optional=False,
        )

    def analyze(self, session_dir: Path, context: AnalyzeContext | None = None) -> AnalysisResult:
        sid = session_dir.name
        summary_parts: list[str] = []
        extras: JsonObject = {}
        try:
            meta = load_session_meta(session_dir)
            if meta:
                sid = meta.session_id or sid
                extras["model_id"] = meta.model_id
                extras["turn_outcome"] = meta.turn_outcome
                extras["tool_count"] = meta.tool_call_count
                extras["task_id"] = meta.task_id
                if meta.turn_outcome:
                    summary_parts.append(f"outcome={meta.turn_outcome}")
                if meta.model_id:
                    summary_parts.append(f"model={meta.model_id}")
                if meta.tool_call_count:
                    summary_parts.append(f"tools={meta.tool_call_count}")
        except Exception as exc:
            return AnalysisResult(
                session_id=sid,
                session_dir=str(session_dir),
                analyzer_id="basic",
                ok=False,
                error=str(exc),
            )
        return AnalysisResult(
            session_id=sid,
            session_dir=str(session_dir),
            analyzer_id="basic",
            ok=True,
            summary="; ".join(summary_parts) or "basic meta ok",
            extras=extras,
        )
