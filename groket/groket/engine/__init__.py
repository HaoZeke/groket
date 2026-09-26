"""Thin rule/detector engine (user YAML + detectors only).

Output artifact is always :class:`~groket.analysis.base.Finding`.
Detectors return :class:`Match`; the runner applies rule templates.
No built-in rules or detectors ship in the package — see
``examples/detection/`` and ``~/.groket/{rules,detectors}/``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..analysis.base import Finding
from ..models import ChatMessage, JsonObject, ToolCall
from .analysis import (
    SessionAnalysis,
    analyze_directory,
    analyze_directory_full,
    analyze_parsed,
    analyze_session,
    analyze_session_full,
)
from .composites import find_composites, suppress_children
from .detectors import detector
from .loader import RuleConfig, get_config, reload_config
from .models import Match
from .runner import run_rules


@dataclass
class RuleInfo:
    rule_id: str
    category: str
    description: str
    enabled: bool
    func: Callable[[list[ToolCall], Sequence[ChatMessage]], list[Finding]]
    detector_name: str = ""
    params: JsonObject = field(default_factory=dict)
    recommendation: str = ""


def _rule_config_to_info(rc: RuleConfig) -> RuleInfo:
    def wrapped_func(
        tool_calls: list[ToolCall],
        messages: Sequence[ChatMessage],
    ) -> list[Finding]:
        return run_rules(tool_calls, messages, rule_ids=[rc.rule_id])

    return RuleInfo(
        rule_id=rc.rule_id,
        category=rc.category,
        description=rc.description,
        enabled=rc.enabled,
        func=wrapped_func,
        detector_name=rc.detector_name,
        params=dict(rc.params),
        recommendation=rc.recommendation,
    )


def run_all_rules(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    rule_ids: list[str] | None = None,
) -> list[Finding]:
    return run_rules(tool_calls, messages, rule_ids)


def get_all_rules() -> dict[str, RuleInfo]:
    config = get_config()
    return {rule_id: _rule_config_to_info(rc) for rule_id, rc in config.rules.items()}


def set_rule_enabled(rule_id: str, enabled: bool) -> None:
    config = get_config()
    if rule_id in config.rules:
        config.rules[rule_id].enabled = enabled


__all__ = [
    "Finding",
    "Match",
    "RuleInfo",
    "SessionAnalysis",
    "analyze_directory",
    "analyze_directory_full",
    "analyze_parsed",
    "analyze_session",
    "analyze_session_full",
    "detector",
    "find_composites",
    "get_all_rules",
    "reload_config",
    "run_all_rules",
    "set_rule_enabled",
    "suppress_children",
]
