"""Optional composite grouping of findings (user YAML under ~/.groket/rules)."""

from __future__ import annotations

from collections import defaultdict

from ..analysis.base import Finding
from .loader import CompositeConfig, get_config


def find_composites(findings: list[Finding]) -> list[Finding]:
    """Return composite parent findings (with children) when YAML composites match."""
    config = get_config()
    out: list[Finding] = []
    by_rule: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_rule[f.id].append(f)

    for comp in config.composites:
        parent = _try_match(comp, by_rule)
        if parent is not None:
            out.append(parent)
    return out


def suppress_children(findings: list[Finding], composites: list[Finding]) -> list[Finding]:
    """Drop findings absorbed as children of a composite."""
    suppressed: set[int] = set()
    for parent in composites:
        for child in parent.children:
            suppressed.add(id(child))
    return [f for f in findings if id(f) not in suppressed]


def _try_match(comp: CompositeConfig, by_rule: dict[str, list[Finding]]) -> Finding | None:
    children: list[Finding] = []
    for rid in comp.child_rules:
        children.extend(by_rule.get(rid, []))
    if not children:
        return None
    if len(children) < max(1, comp.min_repeat):
        return None
    return Finding(
        id=comp.composite_id,
        plugin_id="rules",
        severity=comp.severity,
        title=comp.name,
        detail=comp.root_cause or "",
        category="Composite",
        tool_call_ids=[cid for ch in children for cid in ch.tool_call_ids],
        update_indices=sorted({i for ch in children for i in ch.update_indices}),
        children=list(children),
        extras={"should_have": comp.should_have},
    )
