"""Analyzer protocol, findings, and analysis results.

UI-agnostic contracts for pluggable session analysis. Implementations are
registered via :mod:`groket.analysis.registry` and run by
:class:`~groket.analysis.service.AnalysisService`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

from ..flags import Flag
from ..models import JsonObject, SessionMeta, Severity


@dataclass
class AnalyzerInfo:
    """Metadata for discovery / UI listings."""

    id: str
    name: str
    description: str = ""
    optional: bool = False
    version: str = "0"
    # When True, AnalysisService runs this analyzer in a second pass with
    # ``prior_findings`` (and flags) from non-deferred plugins. External
    # pipelines that need detector output should set this (e.g. LLM review).
    defer: bool = False


@dataclass
class Finding:
    """Universal unit of derived analysis produced by any plugin."""

    id: str
    plugin_id: str
    severity: Severity
    title: str
    detail: str = ""
    category: str = ""
    tool_call_ids: list[str] = field(default_factory=list)
    update_indices: list[int] = field(default_factory=list)
    event_indices: list[int] = field(default_factory=list)
    children: list[Finding] = field(default_factory=list)
    extras: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        """Serialise a Finding (including children) for JSON storage."""
        d: JsonObject = {
            "id": self.id,
            "plugin_id": self.plugin_id,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "category": self.category,
            "tool_call_ids": list(self.tool_call_ids),
            "update_indices": list(self.update_indices),
            "event_indices": list(self.event_indices),
        }
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        if self.extras:
            d["extras"] = dict(self.extras)
        return d

    @classmethod
    def from_dict(cls, d: JsonObject) -> Finding:
        """Reconstruct a Finding from a serialised dict."""
        children_raw = d.get("children") or []
        children: list[Finding] = []
        if isinstance(children_raw, list):
            for cd in children_raw:
                if isinstance(cd, dict):
                    children.append(cls.from_dict(cd))
        tool_ids = d.get("tool_call_ids") or []
        upd = d.get("update_indices") or []
        ev_idx = d.get("event_indices") or []
        extras_raw = d.get("extras") or {}
        extras: JsonObject = {}
        if isinstance(extras_raw, dict):
            extras = {str(k): v for k, v in extras_raw.items()}
        return cls(
            id=str(d.get("id", "")),
            plugin_id=str(d.get("plugin_id", "")),
            severity=Severity(str(d.get("severity", "low"))),
            title=str(d.get("title", "")),
            detail=str(d.get("detail", "")),
            category=str(d.get("category", "")),
            tool_call_ids=[str(x) for x in tool_ids] if isinstance(tool_ids, list) else [],
            update_indices=(
                [int(x) for x in upd if isinstance(x, (int, float, str))]
                if isinstance(upd, list)
                else []
            ),
            event_indices=(
                [int(x) for x in ev_idx if isinstance(x, (int, float, str))]
                if isinstance(ev_idx, list)
                else []
            ),
            children=children,
            extras=extras,
        )

    @property
    def all_tool_call_ids(self) -> list[str]:
        """All tool call IDs including from children."""
        seen: set[str] = set()
        result: list[str] = []
        for cid in self.tool_call_ids:
            if cid not in seen:
                seen.add(cid)
                result.append(cid)
        for child in self.children:
            for cid in child.all_tool_call_ids:
                if cid not in seen:
                    seen.add(cid)
                    result.append(cid)
        return result

    @property
    def all_update_indices(self) -> list[int]:
        """All update indices including from children."""
        seen: set[int] = set()
        result: list[int] = []
        for idx in self.update_indices:
            if idx not in seen:
                seen.add(idx)
                result.append(idx)
        for child in self.children:
            for idx in child.all_update_indices:
                if idx not in seen:
                    seen.add(idx)
                    result.append(idx)
        return sorted(result)

    @property
    def all_event_indices(self) -> list[int]:
        """All timeline event indices including from children."""
        seen: set[int] = set()
        result: list[int] = []
        for idx in self.event_indices:
            if idx not in seen:
                seen.add(idx)
                result.append(idx)
        for child in self.children:
            for idx in child.all_event_indices:
                if idx not in seen:
                    seen.add(idx)
                    result.append(idx)
        return sorted(result)


@dataclass
class AnalysisResult:
    """UI-agnostic outcome of running an analyzer on one session."""

    session_id: str = ""
    session_dir: str = ""
    analyzer_id: str = ""
    ok: bool = True
    error: str = ""
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    extras: JsonObject = field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.MEDIUM)

    def to_dict(self) -> JsonObject:
        return {
            "session_id": self.session_id,
            "session_dir": self.session_dir,
            "analyzer_id": self.analyzer_id,
            "ok": self.ok,
            "error": self.error,
            "finding_count": self.finding_count,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "artifacts": dict(self.artifacts),
            "extras": dict(self.extras),
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> AnalysisResult:
        """Reconstruct from JSON-serialisable dict (cache round-trip)."""
        findings_raw = data.get("findings") or []
        findings: list[Finding] = []
        if isinstance(findings_raw, list):
            for fd in findings_raw:
                if isinstance(fd, dict):
                    findings.append(Finding.from_dict(fd))
        artifacts_raw = data.get("artifacts") or {}
        artifacts: dict[str, str] = {}
        if isinstance(artifacts_raw, dict):
            artifacts = {str(k): str(v) for k, v in artifacts_raw.items()}
        extras_raw = data.get("extras") or {}
        extras: JsonObject = {}
        if isinstance(extras_raw, dict):
            extras = {str(k): v for k, v in extras_raw.items()}
        return cls(
            session_id=str(data.get("session_id", "")),
            session_dir=str(data.get("session_dir", "")),
            analyzer_id=str(data.get("analyzer_id", "")),
            ok=bool(data.get("ok", True)),
            error=str(data.get("error", "")),
            findings=findings,
            summary=str(data.get("summary", "")),
            artifacts=artifacts,
            extras=extras,
        )


class AnalyzeContext(TypedDict, total=False):
    """Optional context passed into :meth:`Analyzer.analyze`."""

    prior_findings: list[Finding]
    prior_results: dict[str, AnalysisResult]
    flags: list[Flag]
    session_meta: SessionMeta


@runtime_checkable
class Analyzer(Protocol):
    """Pluggable session analyzer — the only protocol plugins need to implement."""

    @property
    def info(self) -> AnalyzerInfo: ...

    def analyze(
        self,
        session_dir: Path,
        context: AnalyzeContext | None = None,
    ) -> AnalysisResult: ...


class NoopAnalyzer:
    """Ship-with-app analyzer that records nothing (always ok)."""

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="noop",
            name="None",
            description="No analysis — app works without plugins.",
            optional=False,
        )

    def analyze(
        self,
        session_dir: Path,
        context: AnalyzeContext | None = None,
    ) -> AnalysisResult:
        _ = context
        sid = session_dir.name if session_dir else ""
        return AnalysisResult(
            session_id=sid,
            session_dir=str(session_dir),
            analyzer_id="noop",
            ok=True,
            summary="No analyzer configured.",
        )
