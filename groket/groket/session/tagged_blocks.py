"""Harness angle-bracket / XML-ish blocks observed in Grok Build traces.

This is **not** an official xAI schema. The coding-agent harness (Claude Code
lineage → Grok Build) documents a few tags in system prompts (notably
``user_query`` and ``system-reminder``) and injects many more as structured
wrappers. Tag names drift across versions; the catalogue below is an
**observed dialect** from real ``~/.grok/sessions`` traces plus public prompt
dumps. Prefer allowlisting over treating every ``<>`` pair as special (Rust
generics, C headers, and HTML in code are noise).

Roles for UI / turn segmentation:

* **operator** — real user intent (unwrap for turn summaries).
* **chrome** — harness inject; not a new operator turn; label as system-ish.
* **tool** — tool/result scaffolding (still not operator speech).
* **preamble** — session/policy sections (usually in first context dump).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Outer message is (optional whitespace) + one paired block + optional trailing ws.
# Use \Z (end of string), not $ — MULTILINE $ would match mid-payload after the first
# closing tag and mis-classify composite user_info+user_query dumps as chrome.
_OUTER_BLOCK = re.compile(
    r"^\s*<([a-zA-Z][a-zA-Z0-9_.-]{0,80})(?:\s[^>]*)?>"
    r"([\s\S]*?)"
    r"</\1>\s*\Z",
)
# First paired block anywhere (for user_query nested in larger payloads).
_ANY_BLOCK = re.compile(
    r"<([a-zA-Z][a-zA-Z0-9_.-]{0,80})(?:\s[^>]*)?>"
    r"([\s\S]*?)"
    r"</\1>",
)

# Tags that mean "this is operator intent" when they wrap (or embed) the ask.
OPERATOR_TAGS: frozenset[str] = frozenset(
    {
        "user_query",
    }
)

# Whole-message harness injects → not operator turns / not "User" heading.
CHROME_TAGS: frozenset[str] = frozenset(
    {
        "system-reminder",
        "system_reminder",  # occasional underscore form in dumps
        "monitor-event",
        "user-prompt-submit-hook",
    }
)

# Tool / background-task scaffolding (often inside tool results or user chrome).
TOOL_TAGS: frozenset[str] = frozenset(
    {
        "workspace_result",
        "task-id",
        "task-type",
        "output-file",
        "summary",  # long-running command status wrapper (not markdown summary)
        "status",
    }
)

# Session / policy preamble sections (system prompt dialect + first-turn dumps).
PREAMBLE_TAGS: frozenset[str] = frozenset(
    {
        "user_info",
        "git_status",
        "tool_calling",
        "formatting",
        "background_tasks",
        "action_safety",
        "output_efficiency",
        "user_guide",
        "inline_line_numbers",
        "project_instructions_spec",
        "making_code_changes",
        "mcp_tools",
        "system_information",
        "tone_and_style",
        "skill_information",
        "runtime_context",
        "operator_instructions",
        "non_negotiables",
        "code_creation",
        "communication",
        "think_before_coding",
        "simplicity",
        "surgical_changes",
        "review_constraints",
    }
)

KNOWN_HARNESS_TAGS: frozenset[str] = OPERATOR_TAGS | CHROME_TAGS | TOOL_TAGS | PREAMBLE_TAGS

# Human headings for timeline / detail (title case).
_HEADINGS: dict[str, str] = {
    "user_query": "User query",
    "system-reminder": "System reminder",
    "system_reminder": "System reminder",
    "monitor-event": "Monitor",
    "user-prompt-submit-hook": "Hook",
    "workspace_result": "Workspace result",
    "task-id": "Task id",
    "task-type": "Task type",
    "output-file": "Output file",
    "summary": "Command status",
    "status": "Status",
    "user_info": "User info",
    "git_status": "Git status",
    "tool_calling": "Tool calling",
    "formatting": "Formatting",
    "background_tasks": "Background tasks",
    "action_safety": "Action safety",
    "output_efficiency": "Output efficiency",
    "user_guide": "User guide",
    "inline_line_numbers": "Line numbers",
    "project_instructions_spec": "Project instructions",
    "making_code_changes": "Code changes",
    "mcp_tools": "MCP tools",
    "system_information": "System information",
    "skill_information": "Skills",
    "runtime_context": "Runtime context",
}


@dataclass(frozen=True, slots=True)
class TaggedBlock:
    """One paired harness-style block."""

    tag: str
    body: str
    #: True when the block is the entire trimmed message body.
    outer: bool

    @property
    def role(self) -> str:
        """operator | chrome | tool | preamble | unknown."""
        t = self.tag.casefold()
        if t in OPERATOR_TAGS:
            return "operator"
        if t in CHROME_TAGS:
            return "chrome"
        if t in TOOL_TAGS:
            return "tool"
        if t in PREAMBLE_TAGS:
            return "preamble"
        return "unknown"

    @property
    def heading(self) -> str:
        """Short UI label for the tag."""
        t = self.tag.casefold()
        if t in _HEADINGS:
            return _HEADINGS[t]
        # kebab/snake → Title words
        words = re.split(r"[-_]+", t)
        return " ".join(w.capitalize() for w in words if w)


def normalize_tag(name: str) -> str:
    """Lowercase tag name for catalogue lookup."""
    return (name or "").strip().casefold()


def parse_outer_tagged_block(text: str) -> TaggedBlock | None:
    """If *text* is essentially one paired ``<tag>…</tag>``, return it.

    :param text: Message body (any role).
    :returns: Block with ``outer=True``, or None.
    """
    raw = text or ""
    m = _OUTER_BLOCK.match(raw)
    if not m:
        return None
    tag = normalize_tag(m.group(1))
    body = (m.group(2) or "").strip()
    return TaggedBlock(tag=tag, body=body, outer=True)


def find_tagged_blocks(text: str, *, limit: int = 32) -> list[TaggedBlock]:
    """Find paired blocks in *text* (non-overlapping, document order).

    Nested same-tag structures are best-effort (non-greedy). Used for
    extracting ``user_query`` from composite user payloads.
    """
    out: list[TaggedBlock] = []
    if not text or "<" not in text:
        return out
    for m in _ANY_BLOCK.finditer(text):
        tag = normalize_tag(m.group(1))
        body = (m.group(2) or "").strip()
        out.append(TaggedBlock(tag=tag, body=body, outer=False))
        if len(out) >= max(1, int(limit)):
            break
    # Mark outer when the whole string is that single block.
    if len(out) == 1:
        outer = parse_outer_tagged_block(text)
        if outer is not None and outer.tag == out[0].tag:
            return [outer]
    return out


def extract_user_query(text: str) -> str | None:
    """Return the first ``<user_query>`` body if present."""
    for block in find_tagged_blocks(text, limit=16):
        if block.tag in OPERATOR_TAGS and block.body:
            return block.body
    return None


def is_harness_user_chrome(content: str) -> bool:
    """True when *content* is harness-injected user chrome, not an operator prompt.

    Matches:

    * Entire message is a known chrome/tool/preamble outer tag.
    * Entire message is ``<system-reminder>…`` (any spelling).
    * Background-task / task-completed body text (with or without tags).
    """
    c = (content or "").strip()
    if not c:
        return False
    outer = parse_outer_tagged_block(c)
    if outer is not None:
        if outer.tag in OPERATOR_TAGS:
            return False
        if outer.tag in CHROME_TAGS or outer.tag in TOOL_TAGS or outer.tag in PREAMBLE_TAGS:
            return True
        # Unknown outer harness-shaped tag (snake/kebab, not HTML): treat as chrome
        # when the name looks like dialect (underscore or hyphen) and not a path.
        if re.fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+", outer.tag):
            return True
    cl = c.casefold()
    if cl.startswith("<system-reminder>") or cl.startswith("<system_reminder>"):
        return True
    if "background task" in cl:
        return True
    if "task-completed-call-" in cl:
        return True
    return False


def harness_user_chrome_heading(content: str) -> str | None:
    """Timeline / detail heading for harness user chrome, or None if operator.

    :returns: Short label (e.g. ``Background task``, ``System reminder``).
    """
    if not is_harness_user_chrome(content):
        return None
    c = content or ""
    cl = c.casefold()
    if "background task" in cl or "task-completed-call-" in cl:
        return "Background task"
    outer = parse_outer_tagged_block(c)
    if outer is not None:
        if outer.tag in {"system-reminder", "system_reminder"}:
            # Prefer specific heading when body is a bg task (already handled).
            return outer.heading
        return outer.heading
    return "System reminder"


def operator_prompt_text(content: str, *, max_chars: int = 0) -> str:
    """Best operator-facing text from a user message body.

    Prefers nested ``<user_query>`` body; otherwise returns *content* when it is
    not harness chrome; empty string when chrome-only.
    """
    raw = (content or "").strip()
    if not raw:
        return ""
    uq = extract_user_query(raw)
    if uq is not None:
        text = uq
    elif is_harness_user_chrome(raw):
        return ""
    else:
        text = raw
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def unwrap_for_display(content: str) -> str:
    """Body text for UI: strip outer harness tags, prefer ``user_query`` body.

    Leaves non-harness text unchanged (including code that merely contains ``<>``).
    """
    raw = content if isinstance(content, str) else str(content or "")
    if not raw.strip():
        return ""
    uq = extract_user_query(raw)
    if uq is not None:
        return uq
    outer = parse_outer_tagged_block(raw)
    if outer is not None and outer.tag not in OPERATOR_TAGS:
        # Known chrome/tool/preamble, or harness-shaped outer tag.
        if (
            outer.tag in CHROME_TAGS
            or outer.tag in TOOL_TAGS
            or outer.tag in PREAMBLE_TAGS
            or re.fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+", outer.tag)
        ):
            return outer.body
    # Leading system-reminder without perfect outer match (trailing junk).
    m = re.match(
        r"^\s*<(system-reminder|system_reminder)(?:\s[^>]*)?>"
        r"([\s\S]*?)"
        r"</\1>\s*",
        raw,
        re.IGNORECASE,
    )
    if m:
        return (m.group(2) or "").strip()
    return raw


__all__ = [
    "CHROME_TAGS",
    "KNOWN_HARNESS_TAGS",
    "OPERATOR_TAGS",
    "PREAMBLE_TAGS",
    "TOOL_TAGS",
    "TaggedBlock",
    "extract_user_query",
    "find_tagged_blocks",
    "harness_user_chrome_heading",
    "is_harness_user_chrome",
    "normalize_tag",
    "operator_prompt_text",
    "parse_outer_tagged_block",
    "unwrap_for_display",
]
