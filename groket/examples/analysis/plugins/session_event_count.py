"""Example analysis plugin — simple timeline event counts.

Install::

    mkdir -p ~/.groket/plugins
    cp examples/analysis/plugins/session_event_count.py ~/.groket/plugins/

Enable in ~/.groket/config.json::

    {
      "analysis": {
        "plugins": ["session_event_count:SessionEventCountAnalyzer"]
      }
    }

Or scaffold + register::

    uv run groket gen plugin session_event_count --register

Then open the TUI and run analysis (``a`` on the sessions list). Findings
appear on the Report / Findings views for the plugin id ``session-event-count``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import Severity
from groket.parser import load_session_meta, parse_timeline


class SessionEventCountAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="session-event-count",
            name="Session event count",
            description="Counts timeline event types (example user analysis plugin)",
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        _ = kwargs
        meta = load_session_meta(session_dir)
        timeline = parse_timeline(session_dir)
        counts = Counter(ev.event_type for ev in timeline)

        findings: list[Finding] = []
        total = sum(counts.values())
        if total == 0:
            findings.append(
                Finding(
                    id="session-event-count-empty",
                    plugin_id=self.info.id,
                    title="No timeline events",
                    detail=f"Session {meta.session_id} has an empty timeline.",
                    severity=Severity.LOW,
                )
            )
        else:
            top = ", ".join(f"{k}={v}" for k, v in counts.most_common(5))
            findings.append(
                Finding(
                    id="session-event-count-summary",
                    plugin_id=self.info.id,
                    title=f"{total} timeline events",
                    detail=f"Top types: {top}",
                    severity=Severity.LOW,
                )
            )
            tool_calls = counts.get("tool_call", 0)
            if tool_calls >= 50:
                findings.append(
                    Finding(
                        id="session-event-count-busy",
                        plugin_id=self.info.id,
                        title="High tool_call volume",
                        detail=f"{tool_calls} tool_call events in this session.",
                        severity=Severity.MEDIUM,
                    )
                )

        return AnalysisResult(
            analyzer_id=self.info.id,
            findings=findings,
            summary=f"{len(findings)} finding(s), {total} events",
            ok=True,
        )
