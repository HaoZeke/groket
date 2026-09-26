"""Order findings and report sections for Findings / Report surfaces."""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..models import Severity, TraceEvent
from ..session.turns import (
    event_display_turn_map,
    segment_timeline_turns,
)
from .base import Finding

# Sort key sentinel: unlinked findings after every turn-linked row.
_UNLINKED = 10**9
_TURN_NUMS = re.compile(r"\d+")
_TURN_LINE = re.compile(r"(?m)^Turn:\s*(.+)$")
_WHERE_LINE = re.compile(r"(?m)^Where(?:\s+do you see it)?:\s*(.+)$", re.I)
_H2_SPLIT = re.compile(r"(?=^## )", re.MULTILINE)
_ISSUE_H2 = re.compile(r"^##\s+Issue\b", re.IGNORECASE | re.MULTILINE)
_ISSUE_H2_NUM = re.compile(r"^##\s+Issue\s+\d+\s*:", re.IGNORECASE)
_TRAILING_TIP = re.compile(
    r"\n+---\s*\n+(?:_Tip:|\*Tip:|Tip:).*\Z",
    re.IGNORECASE | re.DOTALL,
)


def parse_turn_hint(text: str | None) -> int | None:
    """First / minimum integer from free-text turn labels.

    Accepts ``Turn 3``, ``Turns 2–4``, ``turn 0``, bare numbers, etc.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    nums = [int(x) for x in _TURN_NUMS.findall(raw)]
    if not nums:
        return None
    return min(nums)


def _tool_call_event_index(timeline: Sequence[TraceEvent]) -> dict[str, int]:
    by_tcid: dict[str, int] = {}
    for ev in timeline:
        tcid = (ev.tool_call_id or "").strip()
        if tcid and tcid not in by_tcid:
            by_tcid[tcid] = int(ev.index)
    return by_tcid


def _anchor_event_indices(
    finding: Finding,
    tool_call_to_index: dict[str, int],
) -> list[int]:
    """Event indices that locate *finding* on the timeline (earliest first)."""
    indices = list(finding.all_event_indices)
    if indices:
        return indices
    for tcid in finding.all_tool_call_ids:
        idx = tool_call_to_index.get(tcid)
        if idx is not None:
            indices.append(idx)
    return indices


def _finding_declared_turn(finding: Finding) -> int | None:
    """Turn from analyzer form extras (MF ``turn`` / ``where``) when present."""
    extras = finding.extras or {}
    for key in ("turn", "where"):
        val = extras.get(key)
        if val is None:
            continue
        n = parse_turn_hint(str(val))
        if n is not None:
            return n
    return None


def sort_findings_by_turn(
    findings: Sequence[Finding],
    timeline: Sequence[TraceEvent],
) -> list[Finding]:
    """Chronological order: turn → earliest evidence → severity → title.

    Prefers free-text turn labels on ``Finding.extras`` (Model Feedback
    ``turn`` / ``where``), then sequential display turn index from timeline
    segmentation (segment order after renumber — not raw harness turn_number).
    Findings with no turn anchors sort last.

    :param findings: Findings from one or more plugins.
    :param timeline: Session timeline used to map evidence → turns.
    :returns: New list; input order is not mutated.
    """
    if not findings:
        return []
    segments = segment_timeline_turns(list(timeline))
    turn_by_event = event_display_turn_map(segments)
    tool_call_to_index = _tool_call_event_index(timeline)

    def sort_key(f: Finding) -> tuple[int, int, Severity, str, str]:
        anchors = _anchor_event_indices(f, tool_call_to_index)
        event_turns = [turn_by_event[i] for i in anchors if i in turn_by_event]
        declared = _finding_declared_turn(f)
        if declared is not None:
            turn = declared
        elif event_turns:
            turn = min(event_turns)
        else:
            turn = _UNLINKED
        earliest = min(anchors) if anchors else _UNLINKED
        return (turn, earliest, f.severity, f.title or "", f.id)

    return sorted(findings, key=sort_key)


def _section_turn_key(section: str) -> tuple[int, str]:
    """Sort key for one ``## Issue`` markdown block."""
    m = _TURN_LINE.search(section)
    if m:
        n = parse_turn_hint(m.group(1))
        if n is not None:
            return (n, section[:60])
    m = _WHERE_LINE.search(section)
    if m:
        n = parse_turn_hint(m.group(1))
        if n is not None:
            return (n, section[:60])
    return (_UNLINKED, section[:60])


def order_report_markdown_by_turn(text: str) -> str:
    """Reorder ``## Issue …`` sections by ``Turn:`` / Where labels.

    Leaves the title, session summary, and trailing tip in place. Renumbers
    issue headings to ``## Issue 1:`` … after sorting. No-op when fewer than
    two issue sections exist.

    :param text: Full plugin report markdown (artifact).
    :returns: Markdown with issues in turn order (always ends with newline).
    """
    body = (text or "").strip()
    if not body:
        return ""

    tip = ""
    tip_m = _TRAILING_TIP.search(body)
    if tip_m:
        tip = tip_m.group(0).strip()
        body = body[: tip_m.start()].rstrip()

    parts = _H2_SPLIT.split(body)
    head: list[str] = []
    issues: list[str] = []
    tail: list[str] = []
    seen_issue = False
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        if _ISSUE_H2.match(stripped):
            seen_issue = True
            issues.append(stripped)
        elif not seen_issue:
            head.append(stripped)
        else:
            tail.append(stripped)

    if len(issues) > 1:
        issues = sorted(issues, key=_section_turn_key)
        renumbered: list[str] = []
        for n, sec in enumerate(issues, start=1):
            if _ISSUE_H2_NUM.match(sec):
                sec = _ISSUE_H2_NUM.sub(f"## Issue {n}:", sec, count=1)
            renumbered.append(sec)
        issues = renumbered

    chunks = head + issues + tail
    out = "\n\n".join(chunks).strip()
    if tip:
        out = f"{out}\n\n{tip}".strip()
    return out + "\n"
