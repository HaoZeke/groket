"""Rules/detectors analyzer — emits :class:`Finding` only."""

from __future__ import annotations

from pathlib import Path

from ...base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding

PLUGIN_ID = "engine"


class EngineDetectorAnalyzer:
    """User-configured rules under ``~/.groket`` (no built-in catalog)."""

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id=PLUGIN_ID,
            name="Rules",
            description="YAML rules + user detectors → findings.",
            optional=True,
        )

    def analyze(self, session_dir: Path, context: AnalyzeContext | None = None) -> AnalysisResult:
        _ = context
        try:
            from ....engine.analysis import analyze_session_full
        except Exception as exc:
            return AnalysisResult(
                session_id=session_dir.name,
                session_dir=str(session_dir),
                analyzer_id=PLUGIN_ID,
                ok=False,
                error=f"engine unavailable: {exc}",
            )
        try:
            full = analyze_session_full(session_dir)
            findings: list[Finding] = list(full.composites or []) + list(full.findings or [])
            # Re-tag plugin_id for this analyzer surface
            for f in findings:
                if f.plugin_id == "rules":
                    f.plugin_id = PLUGIN_ID
            meta = full.meta
            sid = session_dir.name
            if meta is not None and getattr(meta, "session_id", None):
                sid = meta.session_id
            return AnalysisResult(
                session_id=sid,
                session_dir=str(session_dir),
                analyzer_id=PLUGIN_ID,
                ok=True,
                findings=findings,
                summary=f"{len(findings)} finding(s)",
            )
        except Exception as exc:
            return AnalysisResult(
                session_id=session_dir.name,
                session_dir=str(session_dir),
                analyzer_id=PLUGIN_ID,
                ok=False,
                error=str(exc),
            )
