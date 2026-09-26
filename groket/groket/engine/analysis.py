"""Session analysis entry points — always produce :class:`Finding` lists."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis.base import Finding
from ..models import ChatMessage, SessionMeta, Severity, ToolCall
from ..parser import find_sessions, load_session_meta, parse_chat_history, parse_tool_calls
from .composites import find_composites, suppress_children
from .runner import run_rules


@dataclass
class SessionAnalysis:
    """Detector pass result for one session (findings only)."""

    meta: SessionMeta
    findings: list[Finding] = field(default_factory=list)
    composites: list[Finding] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    all_findings: list[Finding] = field(default_factory=list)

    @property
    def finding_count(self) -> int:
        return len(self.findings) + len(self.composites)

    def as_tuple(self) -> tuple[SessionMeta, list[Finding], list[Finding]]:
        return self.meta, self.findings, self.composites


def _turn_failure_finding(meta: SessionMeta) -> Finding | None:
    if not meta.turn_failed:
        return None
    return Finding(
        id="turn_outcome_error",
        plugin_id="rules",
        category="Session / Runtime",
        severity=Severity.HIGH,
        title=f"Turn ended with outcome={meta.turn_outcome}",
        detail=(
            "events.jsonl recorded turn_ended with a non-success outcome "
            f"({meta.turn_outcome})." + (f" Loops: {meta.loop_count}." if meta.loop_count else "")
        ),
    )


def analyze_parsed(
    meta: SessionMeta,
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    rule_ids: list[str] | None = None,
    *,
    include_turn_failure: bool = True,
) -> SessionAnalysis:
    all_findings = run_rules(tool_calls, messages, rule_ids)
    if include_turn_failure:
        tfi = _turn_failure_finding(meta)
        if tfi is not None:
            all_findings.append(tfi)
    composites = find_composites(all_findings)
    independent = suppress_children(all_findings, composites)
    return SessionAnalysis(
        meta=meta,
        findings=independent,
        composites=composites,
        tool_calls=tool_calls,
        messages=list(messages),
        all_findings=all_findings,
    )


def analyze_session(
    session_dir: Path,
    rule_ids: list[str] | None = None,
    *,
    include_turn_failure: bool = True,
) -> tuple[SessionMeta, list[Finding], list[Finding]]:
    return analyze_session_full(
        session_dir, rule_ids, include_turn_failure=include_turn_failure
    ).as_tuple()


def analyze_session_full(
    session_dir: Path,
    rule_ids: list[str] | None = None,
    *,
    include_turn_failure: bool = True,
) -> SessionAnalysis:
    session_dir = Path(session_dir)
    meta = load_session_meta(session_dir)
    tool_calls = parse_tool_calls(session_dir)
    messages = parse_chat_history(session_dir)
    return analyze_parsed(
        meta,
        tool_calls,
        messages,
        rule_ids,
        include_turn_failure=include_turn_failure,
    )


def analyze_directory(
    root: Path,
    rule_ids: list[str] | None = None,
) -> list[tuple[SessionMeta, list[Finding], list[Finding]]]:
    return [analyze_session(sd, rule_ids) for sd in find_sessions(root)]


def analyze_directory_full(root: Path, rule_ids: list[str] | None = None) -> list[SessionAnalysis]:
    return [analyze_session_full(sd, rule_ids) for sd in find_sessions(root)]


__all__ = [
    "SessionAnalysis",
    "analyze_directory",
    "analyze_directory_full",
    "analyze_parsed",
    "analyze_session",
    "analyze_session_full",
]
