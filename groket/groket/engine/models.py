"""Detection matches and findings.

**One concept for analysis output:** :class:`~groket.analysis.base.Finding`.

Detectors return lightweight :class:`Match` values; the runner turns each match
plus rule YAML into a :class:`Finding` (``plugin_id="rules"``, ``id`` = rule id).
Composites (optional user YAML) attach as parent findings with children.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.base import Finding
from ..models import MatchVariables, Severity, ToolCall

__all__ = ["Finding", "Match", "match_to_finding"]


@dataclass
class Match:
    """Detector hit before rule templates are applied."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    variables: MatchVariables = field(default_factory=dict)
    severity_override: Severity | None = None
    summary_override: str | None = None
    detail_override: str | None = None


def match_to_finding(
    *,
    rule_id: str,
    category: str,
    severity: Severity,
    title: str,
    detail: str,
    match: Match,
) -> Finding:
    """Build the single analysis artifact from a detector match + rule metadata."""
    return Finding(
        id=rule_id,
        plugin_id="rules",
        severity=severity,
        title=title,
        detail=detail,
        category=category,
        tool_call_ids=[tc.call_id for tc in match.tool_calls],
        update_indices=[tc.update_index for tc in match.tool_calls],
        extras={},
    )
