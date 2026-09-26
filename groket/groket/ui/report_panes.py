"""Split plugin report markdown into independently selectable pane bodies.

Report tab mounts one :class:`~groket.ui.selectable_static.SelectableStatic`
per pane so Tab + ``y`` can yank a single H2 section or MF form fence without
the whole plugin card.
"""

from __future__ import annotations

import re

# MF form drafts use fixed ### labels + fenced bodies (see mf_form_feedback).
_FORM_FIELDS_FENCE = re.compile(
    r"^###\s+Form fields[^\n]*\n+"
    r"```[^\n]*\n"
    r"(.*?)"
    r"\n```",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_ISSUE_BOX_FENCE = re.compile(
    r"^###\s+Issue\s*\(copy[^\n]*\n+"
    r"```[^\n]*\n"
    r"(.*?)"
    r"\n```",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_H2_SPLIT = re.compile(r"(?=^## )", re.MULTILINE)


def split_report_markdown_panes(text: str) -> list[str]:
    """Split report markdown into plain pane bodies for separate widgets.

    Top-level ``##`` sections become panes. Within a section, Model Feedback
    **Form fields** and **Issue (copy into the Issue box)** fenced blocks are
    extracted as their own panes (fence body only, paste-ready). Surrounding
    prose keeps the section header so context is visible.

    :param text: Full plugin report markdown (artifact).
    :returns: Non-empty pane strings in document order.
    """
    body = (text or "").strip()
    if not body:
        return []
    parts = _H2_SPLIT.split(body)
    panes: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        panes.extend(_split_section_special_fences(part))
    return panes


def _split_section_special_fences(section: str) -> list[str]:
    """Extract Form fields / Issue box fences from one ``##`` section."""
    specials: list[tuple[int, int, str]] = []
    for pattern in (_FORM_FIELDS_FENCE, _ISSUE_BOX_FENCE):
        for m in pattern.finditer(section):
            fence_body = (m.group(1) or "").strip()
            if fence_body:
                specials.append((m.start(), m.end(), fence_body))
    if not specials:
        return [section]
    specials.sort(key=lambda item: item[0])
    panes: list[str] = []
    cursor = 0
    for start, end, fence_body in specials:
        before = section[cursor:start].strip()
        if before:
            panes.append(before)
        panes.append(fence_body)
        cursor = end
    after = section[cursor:].strip()
    if after:
        panes.append(after)
    return panes
