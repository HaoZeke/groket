"""Workflow detectors — patterns discovered from low-level C library traces.

These detectors capture anti-patterns specific to how models navigate
unfamiliar codebases, manage build systems, and interact with large files.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, Severity, ToolCall

from patterns import (
    BUILD_CMD_RE,
    EXPLORATORY_CMD_RE,
    HEREDOC_SCRIPT_RE,
    HEREDOC_WRITE_RE,
    INLINE_SCRIPT_RE,
    INSTALL_CMD_RE,
)


@detector("large_file_full_read")
def large_file_full_read(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect read_file calls on large files without offset/limit.

    When a file returns 500+ lines, reading it in full wastes context
    window. The model should use offset/limit or grep to find the
    relevant section.
    """
    min_lines: int = params.as_int("min_lines", 500)
    extensions: list[str] = params.as_str_list("extensions", [])

    ext_set = set(extensions) if extensions else None

    results: list[Match] = []

    for tc in tool_calls:
        if tc.tool_name != "read_file":
            continue


        if tc.inputs().has("offset"):
            continue
        if tc.inputs().has("limit"):
            continue

        target = tc.input_str("target_file")
        if not target:
            continue


        if ext_set:
            suffix = target.rsplit(".", 1)[-1] if "." in target else ""
            if suffix not in ext_set:
                continue


        result = tc.result_content or ""
        line_count = result.count("\n")
        if line_count < min_lines:
            continue

        filename = target.rsplit("/", 1)[-1] if "/" in target else target

        results.append(
            Match(
                tool_calls=[tc],
                variables={
                    "file": filename,
                    "path": target,
                    "line_count": line_count,
                },
            )
        )

    return results

@detector("late_dependency_install")
def late_dependency_install(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect build commands that fail, followed by package installs.

    The model should check for required dependencies before attempting
    to build. This pattern wastes tool calls and session time.
    """
    max_gap: int = params.as_int("max_gap", 5)

    results: list[Match] = []
    seen_install_after: set[int] = set()

    for i, tc in enumerate(tool_calls):
        if tc.tool_name != "run_terminal_command":
            continue

        cmd = tc.input_str("command")
        if not isinstance(cmd, str):
            continue


        if not BUILD_CMD_RE.search(cmd):
            continue

        result = tc.result_content or ""
        is_fail = tc.is_error or "error:" in result.lower() or "Error" in result

        if not is_fail:
            continue


        end = min(i + 1 + max_gap, len(tool_calls))
        for j in range(i + 1, end):
            candidate = tool_calls[j]
            if candidate.tool_name != "run_terminal_command":
                continue

            cand_cmd = candidate.input_str("command")
            if not isinstance(cand_cmd, str):
                continue

            if INSTALL_CMD_RE.search(cand_cmd):
                if j not in seen_install_after:
                    seen_install_after.add(j)


                    pkg_match = re.search(
                        r"install\s+(?:-[yq]+\s+)*(.+?)(?:\s*&&|\s*\|\||$)",
                        cand_cmd,
                    )
                    packages = pkg_match.group(1)[:80] if pkg_match else cand_cmd[:80]

                    build_snippet = cmd[:80]

                    results.append(
                        Match(
                            tool_calls=[tc, candidate],
                            variables={
                                "build_command": build_snippet,
                                "install_command": cand_cmd[:120],
                                "packages": packages,
                            },
                        )
                    )
                break

    return results

def _is_structurally_complex(cmd: str, min_pipes: int, min_chains: int) -> bool:
    """Require real shell pipeline complexity, not just a long string."""
    pipes = cmd.count("|")
    chains = cmd.count("&&") + cmd.count(";")
    newlines = cmd.count("\n")

    if newlines >= 12 and (pipes >= 2 or chains >= 3):
        return True
    return pipes >= min_pipes or chains >= min_chains

def _should_skip_long_cmd(cmd: str) -> bool:
    """Skip long commands that aren't overcomplex *shell* — just long payloads."""
    if INLINE_SCRIPT_RE.search(cmd):
        return True
    if HEREDOC_WRITE_RE.search(cmd):
        return True
    if HEREDOC_SCRIPT_RE.search(cmd):
        return True

    if EXPLORATORY_CMD_RE.search(cmd):
        non_explore = re.sub(
            r"\b(?:cd|git|ls|find|which|type|uname|pwd|echo|head|tail|wc|cat)\b"
            r"|&&|\||;|\s+-\S+|\s+/\S+|\s+\S+\.\w+",
            " ",
            cmd,
            flags=re.IGNORECASE,
        )

        if len(non_explore.strip()) < 40:
            return True
    return False

@detector("overcomplex_shell_commands")
def overcomplex_shell_commands(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect run_terminal_command calls with excessively long *and* complex commands.

    Models sometimes pack entire scripts into a single shell command
    with long pipe chains and multi-line logic. Length alone is not
    enough — orientation chains (git status && ls) and heredoc/inline
    scripts are excluded.
    """
    min_length: int = params.as_int("min_length", 300)
    min_count: int = params.as_int("min_count", 5)
    high_threshold: int = params.as_int("high_threshold", 25)
    min_pipes: int = params.as_int("min_pipes", 3)
    min_chains: int = params.as_int("min_chains", 5)

    long_cmds: list[ToolCall] = []

    for tc in tool_calls:
        if tc.tool_name != "run_terminal_command":
            continue

        cmd = tc.input_str("command")
        if not isinstance(cmd, str):
            continue

        if len(cmd) < min_length:
            continue
        if _should_skip_long_cmd(cmd):
            continue
        if not _is_structurally_complex(cmd, min_pipes, min_chains):
            continue
        long_cmds.append(tc)

    if len(long_cmds) < min_count:
        return []


    worst = max(long_cmds, key=lambda t: len(t.input_str("command")))
    worst_cmd = worst.input_str("command")
    pipe_count = worst_cmd.count("|")
    chain_count = worst_cmd.count("&&") + worst_cmd.count(";")

    severity = Severity.HIGH if len(long_cmds) >= high_threshold else None

    return [
        Match(
            tool_calls=long_cmds[:10],
            variables={
                "count": len(long_cmds),
                "avg_length": sum(len(t.input_str("command")) for t in long_cmds) // len(long_cmds),
                "max_length": len(worst_cmd),
                "max_pipes": pipe_count,
                "max_chains": chain_count,
            },
            severity_override=severity,
        )
    ]
