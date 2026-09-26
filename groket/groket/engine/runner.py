"""Run enabled rules and produce :class:`~groket.analysis.base.Finding` values."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from ..analysis.base import Finding
from ..models import ChatMessage, MatchVariables, ParamBag, Severity, TemplateValue, ToolCall
from .loader import RuleConfig, get_config
from .models import Match, match_to_finding

logger = logging.getLogger(__name__)


def run_rules(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    rule_ids: list[str] | None = None,
) -> list[Finding]:
    """Run enabled rules (or *rule_ids*) and return findings."""
    config = get_config()
    findings: list[Finding] = []
    rules_to_run = config.rules
    if rule_ids is not None:
        rules_to_run = {k: v for k, v in rules_to_run.items() if k in rule_ids}

    for rule_id, rule_cfg in rules_to_run.items():
        if not rule_cfg.enabled and rule_ids is None:
            continue
        if rule_cfg.detector_func is None:
            logger.warning(
                "Rule '%s' has no resolved detector '%s'", rule_id, rule_cfg.detector_name
            )
            continue
        try:
            matches = rule_cfg.detector_func(tool_calls, messages, ParamBag(rule_cfg.params))
            for match in matches:
                findings.append(_match_to_finding(rule_cfg, match))
        except Exception as e:
            findings.append(
                Finding(
                    id=rule_id,
                    plugin_id="rules",
                    category="Rule Error",
                    severity=Severity.LOW,
                    title=f"Rule '{rule_id}' failed: {e}",
                    detail=str(e),
                )
            )
    return findings


def _match_to_finding(rule_cfg: RuleConfig, match: Match) -> Finding:
    variables = dict(match.variables)
    if match.tool_calls:
        variables.setdefault("tool", match.tool_calls[0].tool_name)
        variables.setdefault("tools", ", ".join(tc.tool_name for tc in match.tool_calls[:5]))
        cmds: list[str] = []
        for tc in match.tool_calls[:5]:
            if tc.tool_name == "run_terminal_command":
                cmd = tc.input_str("command")
                cmds.append(str(cmd)[:60] if cmd is not None else "")
        if cmds:
            variables.setdefault("matched_commands", "; ".join(cmds))
    severity = match.severity_override or rule_cfg.severity
    title = match.summary_override or _safe_format(rule_cfg.summary_template, variables)
    detail = match.detail_override or _safe_format(rule_cfg.detail_template, variables)
    return match_to_finding(
        rule_id=rule_cfg.rule_id,
        category=rule_cfg.category,
        severity=severity,
        title=title,
        detail=detail,
        match=match,
    )


def _safe_format(template: str, variables: MatchVariables) -> str:
    if not template:
        return ""
    formatted = {k: _format_value(v) for k, v in variables.items()}
    try:
        return template.format(**formatted)
    except (KeyError, IndexError, ValueError):
        result = template
        for key, value in formatted.items():
            result = result.replace("{" + key + "}", value)
        return re.sub(r"\{[^}]*\}", "?", result)


def _format_value(value: TemplateValue) -> str:
    if isinstance(value, list):
        if len(value) <= 5:
            return ", ".join(str(v) for v in value)
        return ", ".join(str(v) for v in value[:5]) + f" (+{len(value) - 5} more)"
    if value is None:
        return ""
    return str(value)
