"""Search quality detectors — detect grep anti-patterns like unscoped searches,
overly broad patterns, and repeated empty searches on the same path."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, ToolCall

_MATCH_LINE_RE = re.compile(r"^[^\s:]+:\d+:", re.MULTILINE)
_AT_LEAST_RE = re.compile(r"at least (\d+)")

def _count_matches(result: str) -> int:
    """Estimate how many matches a grep call returned."""

    m = _AT_LEAST_RE.search(result)
    if m:
        return int(m.group(1))

    return len(_MATCH_LINE_RE.findall(result))

@detector("unscoped_grep")
def unscoped_grep(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect grep calls with no path scope that return excessive matches."""
    min_matches: int = params.as_int("min_matches", 50)

    results: list[Match] = []

    for tc in tool_calls:
        if tc.tool_name != "grep":
            continue

        path = tc.input_str("path") or ""

        if path and path not in (".", "./", "/"):
            continue

        match_count = _count_matches(tc.result_content or "")
        if match_count < min_matches:
            continue

        pattern = tc.input_str("pattern")
        results.append(
            Match(
                tool_calls=[tc],
                variables={
                    "match_count": match_count,
                    "pattern": pattern[:80],
                },
            )
        )

    return results

_BROAD_PATTERN_RE = re.compile(r"^.{1,2}$")
_BROAD_KEYWORDS_RE = re.compile(
    r"^(import|def|class|function|return|const|let|var|if|for|while|error|test|TODO)$",
    re.IGNORECASE,
)

@detector("inefficient_search")
def inefficient_search(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect grep calls with patterns that are too broad or generic,
    resulting in excessive noisy output."""
    min_matches: int = params.as_int("min_matches", 100)

    results: list[Match] = []

    for tc in tool_calls:
        if tc.tool_name != "grep":
            continue

        pattern = tc.input_str("pattern")
        result = tc.result_content or ""
        match_count = _count_matches(result)

        is_broad = False
        reason = ""

        if _BROAD_PATTERN_RE.match(pattern):
            is_broad = True
            reason = f"very short pattern '{pattern}'"
        elif _BROAD_KEYWORDS_RE.match(pattern.strip()):
            is_broad = True
            reason = f"common keyword '{pattern}'"
        elif match_count >= min_matches:
            is_broad = True
            reason = f"{match_count}+ matches"

        if not is_broad:
            continue

        results.append(
            Match(
                tool_calls=[tc],
                variables={
                    "pattern": pattern[:80],
                    "match_count": match_count,
                    "reason": reason,
                },
                summary_override=f"Broad grep pattern: {reason}",
                detail_override=(
                    f"Grep pattern '{pattern[:80]}' is overly broad ({reason}).\n"
                    f"  Matches: {match_count}+\n"
                    f"  Path: {tc.input_str('path', '(root)')}\n"
                    f"Use more specific patterns and scope to relevant directories."
                ),
            )
        )

    return results

@detector("repeated_empty_searches")
def repeated_empty_searches(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect repeated grep calls that return zero results, grouped by
    path prefix. Indicates the model is searching in the wrong place."""
    group_min: int = params.as_int("group_min", 3)

    results: list[Match] = []


    groups: dict[str, list[tuple[ToolCall, str]]] = defaultdict(list)

    for tc in tool_calls:
        if tc.tool_name != "grep":
            continue

        result = tc.result_content or ""

        match_count = _count_matches(result)
        if match_count > 0:
            continue


        stripped = result.strip()
        if stripped and not stripped.startswith("exit:") and len(stripped) > 50:
            continue

        path = tc.input_str("path") or "."
        pattern = tc.input_str("pattern")


        parts = path.strip("/").split("/")
        prefix = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else ".")

        groups[prefix].append((tc, pattern))

    for prefix, entries in groups.items():
        if len(entries) < group_min:
            continue




        raw_patterns = [p.lower().strip() for _, p in entries]
        unique_terms: set[str] = set()
        for p in raw_patterns:

            terms = set(re.split(r"[|\\().*+?\[\]{}^$]+", p))
            terms = {t.strip() for t in terms if len(t.strip()) > 2}
            unique_terms.update(terms)



        if len(unique_terms) >= len(entries) * 2:
            continue

        tcs = [tc for tc, _ in entries]
        patterns = [p[:60] for _, p in entries[:8]]

        results.append(
            Match(
                tool_calls=tcs[:10],
                variables={
                    "group_key": prefix,
                    "count": len(entries),
                    "patterns": ", ".join(patterns),
                },
            )
        )

    return results
