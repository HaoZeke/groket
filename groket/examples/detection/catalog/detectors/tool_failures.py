"""Detectors for tool call failures and ignored errors."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, Severity, ToolCall, as_json_object

from patterns import (
    BUILD_OR_TEST_RE,
    ENV_FAILURE_RE,
    SERVER_CMD_RE,
    SIGNAL_KILL_RE,
    TERMINAL_ERROR_RE,
    is_env_failure,
    is_infra_error,
    is_verify_expected_error,
)

_SKIP_TOKENS = {"cd", "&&", ";", "sudo", "timeout", "nohup", "env", "nice", "exec"}

_DIAGNOSTIC_CMDS = {"which", "whereis", "type", "command", "hash"}

def _core_tokens(tokens: list[str]) -> list[str]:
    """Extract the core command (binary + subcommand) from a tokenized shell
    command, stripping env vars, cd prefixes, sudo/timeout/nohup wrappers."""
    out: list[str] = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if "=" in t and not t.startswith("-"):
            continue
        if t in _SKIP_TOKENS:
            if t in ("cd", "timeout", "nice"):
                skip_next = True
            continue
        if t.startswith("-") and not out:
            continue
        out.append(t)
        if len(out) >= 2:
            break
    return out

@detector("tool_call_failures")
def detect_tool_call_failures(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Group failed tool calls.  Parallel failures (same timestamp
    ±parallel_window_s) get HIGH severity (or LOW if all read-only).
    Single failures get LOW (recoverable) or MEDIUM.
    Skip build/test failures that the model addressed (next 5 calls
    include search_replace/read_file/grep).
    """
    matches: list[Match] = []

    parallel_window_s: int = params.as_int("parallel_window_s", 5)
    recoverable_tools: set[str] = set(
        params.as_str_list("recoverable_tools", ["search_replace", "read_file", "list_dir"])
    )
    build_test_pattern: re.Pattern[str] = re.compile(
        params.as_str("build_test_pattern", BUILD_OR_TEST_RE.pattern),
        re.IGNORECASE,
    )

    if not tool_calls:
        return matches

    def _is_noise_error(tc: ToolCall) -> bool:
        """Infra/meta/env errors shouldn't contribute to failure groupings."""
        if tc.tool_name in ("update_goal", "todo_write", "web_search"):
            return True
        if tc.tool_name == "run_terminal_command":
            out = tc.result_content or ""
            if is_env_failure(out) or is_infra_error(out):
                return True
            from patterns import is_build_progress_only

            if is_build_progress_only(out):
                return True
        return False

    error_calls = [tc for tc in tool_calls if tc.is_error and not _is_noise_error(tc)]
    if not error_calls:
        return matches


    substantive = [tc for tc in error_calls if tc.tool_name not in ("update_goal", "todo_write")]
    if not substantive:
        return matches


    tc_by_idx: dict[int, ToolCall] = {tc.update_index: tc for tc in tool_calls}
    idx_list = sorted(tc_by_idx.keys())

    _INSTALL_KEYWORDS = {
        "install",
        "apt-get",
        "pip",
        "npm",
        "cargo",
        "go get",
        "go mod",
        "brew",
        "yum",
        "dnf",
        "apk add",
    }

    def _is_addressed(tc: ToolCall) -> bool:
        """Check if the model followed up on this build/test error."""
        if tc.tool_name != "run_terminal_command":
            return False
        cmd = tc.input_str("command")
        if not build_test_pattern.search(cmd):
            return False

        error_files = set()
        if tc.result_content:
            error_files = set(re.findall(r"[\w/.-]+\.\w{1,5}", tc.result_content[:500]))
        try:
            pos = idx_list.index(tc.update_index)
        except ValueError:
            return False
        for j in range(pos + 1, min(pos + 6, len(idx_list))):
            next_tc = tc_by_idx[idx_list[j]]
            if next_tc.tool_name in ("search_replace", "read_file", "grep"):
                return True
            if next_tc.tool_name == "run_terminal_command":
                next_cmd = next_tc.input_str("command")

                if any(kw in next_cmd.lower() for kw in _INSTALL_KEYWORDS):
                    return True

                cmd_tokens = cmd.split()
                next_tokens = next_cmd.split()
                if cmd_tokens and next_tokens:
                    cmd_core = _core_tokens(cmd_tokens)
                    next_core = _core_tokens(next_tokens)
                    if cmd_core and cmd_core == next_core:
                        return True

                if error_files and any(f in next_cmd for f in error_files):
                    return True
        return False

    _READ_ONLY_TOOLS = {"read_file", "list_dir", "grep"}
    _META_TOOLS = {"update_goal", "todo_write"}
    _INFRA_ERROR_TOOLS = {"web_search", "update_goal", "todo_write"}
    _INFRA_ERROR_RE = re.compile(
        r"HTTP request failed|error sending request|connection.*refused"
        r"|timeout|503 Service|502 Bad Gateway"
        r"|Author identity unknown|tell me who you are",
        re.IGNORECASE,
    )
    reported_ids: set[str] = set()

    for tc in error_calls:
        if tc.call_id in reported_ids:
            continue


        parallel_errors = [
            ec
            for ec in error_calls
            if ec.call_id != tc.call_id
            and ec.call_id not in reported_ids
            and ec.timestamp is not None
            and tc.timestamp is not None
            and abs(ec.timestamp - tc.timestamp) < parallel_window_s
        ]

        if parallel_errors:
            all_tcs = [tc] + parallel_errors
            reported_ids.update(t.call_id for t in all_tcs)
            tools = [t.tool_name for t in all_tcs]

            if all(t.tool_name in _READ_ONLY_TOOLS for t in all_tcs):
                sev = Severity.LOW
            elif all(
                t.tool_name in _INFRA_ERROR_TOOLS and _INFRA_ERROR_RE.search(t.result_content or "")
                for t in all_tcs
            ):
                sev = Severity.LOW
            else:
                sev = Severity.HIGH

            snippet = tc.result_content[:300] if tc.result_content else "(no content)"
            matches.append(
                Match(
                    tool_calls=all_tcs,
                    variables=as_json_object(
                        {
                            "count": len(all_tcs),
                            "tools": tools,
                            "error_snippet": snippet,
                            "is_parallel": True,
                        }
                    ),
                    severity_override=sev,
                    summary_override=(
                        f"Parallel tool calls failed ({len(all_tcs)} calls: {', '.join(tools)})"
                    ),
                )
            )
        else:

            if _is_addressed(tc):
                continue

            reported_ids.add(tc.call_id)
            error_snippet = tc.result_content[:200] if tc.result_content else "(no content)"
            sev = Severity.LOW if tc.tool_name in recoverable_tools else Severity.MEDIUM

            matches.append(
                Match(
                    tool_calls=[tc],
                    variables=as_json_object(
                        {
                            "count": 1,
                            "tools": [tc.tool_name],
                            "error_snippet": error_snippet,
                            "is_parallel": False,
                        }
                    ),
                    severity_override=sev,
                    summary_override=f"Tool call failed: {tc.tool_name}",
                )
            )

    return matches

def _should_skip_error(tc: ToolCall, skip_tools: set[str]) -> bool:
    """Return True if this error should be excluded from ignored-error checks."""

    if tc.tool_name in skip_tools:
        content_lower = (tc.result_content or "").lower()
        if "harness" in content_lower or "channel" in content_lower:
            return True

    if tc.tool_name == "search_replace":
        result_lower = (tc.result_content or "").lower()
        if "old string and new string are the same" in result_lower:
            return True

    if tc.tool_name == "run_terminal_command":
        output = tc.result_content or ""
        cmd = tc.input_str("command")
        if SIGNAL_KILL_RE.search(output) and SERVER_CMD_RE.search(cmd):
            return True


    if tc.tool_name == "list_dir":
        return True


    if tc.tool_name == "read_file":
        result = (tc.result_content or "").lower()
        if "does not exist" in result or "no such file" in result:
            return True

    if tc.tool_name == "run_terminal_command":
        output = tc.result_content or ""
        cmd = tc.input_str("command")
        if is_env_failure(output) or ENV_FAILURE_RE.search(output[:1000]):
            return True
        if is_infra_error(output):
            return True
        if is_verify_expected_error(cmd, output):
            return True


        if re.search(r"^commit [0-9a-f]{7,}", output, re.MULTILINE) and not re.search(
            r"fatal:|error:", output[:500], re.IGNORECASE
        ):
            return True

        from patterns import is_build_progress_only

        if is_build_progress_only(output):
            return True


        if "[truncated:" in output and not re.search(
            r"error(?:\[E\d+\])?:\s", output, re.IGNORECASE
        ):
            return True
        from patterns import is_truncated_only_output

        if is_truncated_only_output(output):
            return True
    if tc.tool_name in ("update_goal", "todo_write"):
        return True
    return False

def _cmd_addresses_error(
    tc: ToolCall,
    nxt: ToolCall,
    cmd_not_found: bool,
    missing_binary: str,
) -> bool:
    """Check if *nxt* terminal command addresses a failed terminal command *tc*."""
    failed_cmd = tc.input_str("command")
    next_cmd = nxt.input_str("command")


    if cmd_not_found:
        next_lower = next_cmd.lower()
        if any(d in next_lower for d in _DIAGNOSTIC_CMDS):
            return True
        if "ls /usr" in next_lower or "ls -" in next_lower:
            return True
        if missing_binary:
            variants = {missing_binary + "3", missing_binary + "2"}
            if missing_binary.endswith("3"):
                variants.add(missing_binary.rstrip("3"))
            variants.discard("")
            if any(v in next_cmd for v in variants):
                return True


    failed_core = _core_tokens(failed_cmd.split())
    next_core = _core_tokens(next_cmd.split())
    if failed_core and failed_core == next_core:
        return True


    files_in_cmd = re.findall(r"[\w/.-]+\.\w{1,5}", failed_cmd)
    if files_in_cmd and any(f in next_cmd for f in files_in_cmd):
        return True


    if any(d in next_cmd for d in ["gdb", "strace", "valgrind", "debug"]):
        return True

    return False

def _is_error_addressed(
    tc: ToolCall,
    window: list[ToolCall],
    failed_file: str,
    cmd_not_found: bool,
    missing_binary: str,
) -> bool:
    """Check if any call in *window* addresses the error from *tc*."""
    for nxt in window:

        if tc.tool_name == "run_terminal_command" and nxt.tool_name == "run_terminal_command":
            if _cmd_addresses_error(tc, nxt, cmd_not_found, missing_binary):
                return True
            continue


        if nxt.tool_name == tc.tool_name:
            return True


        if nxt.tool_name == "read_file" and failed_file:
            nxt_file = nxt.input_str("target_file")
            if nxt_file and (nxt_file == failed_file or failed_file in nxt_file):
                return True


        if nxt.tool_name in ("grep", "list_dir"):
            return True


        if nxt.tool_name == "search_replace" and failed_file:
            if nxt.input_str("file_path") == failed_file:
                return True



        if tc.tool_name == "run_terminal_command" and nxt.tool_name == "search_replace":
            return True


        if tc.tool_name == "run_terminal_command" and nxt.tool_name == "read_file":
            cmd = tc.input_str("command")
            if BUILD_OR_TEST_RE.search(cmd):
                return True

    return False

@detector("ignored_errors")
def detect_ignored_errors(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """For each error tool call, check if the next `lookahead_window` calls
    address it.  'Addressed' means: same tool retried, read_file on failed
    file, grep, search_replace on failed file, or (for terminal commands)
    the next command references the same files.
    """
    matches: list[Match] = []
    lookahead_window: int = params.as_int("lookahead_window", 3)
    skip_tools: set[str] = set(params.as_str_list("skip_tools", ["update_goal"]))

    if not tool_calls:
        return matches

    for i, tc in enumerate(tool_calls):
        if not tc.is_error:
            continue
        if _should_skip_error(tc, skip_tools):
            continue

        remaining = tool_calls[i + 1 :]


        if not remaining:
            error_snippet = tc.result_content[:200] if tc.result_content else "(no content)"
            matches.append(
                Match(
                    tool_calls=[tc],
                    variables={
                        "tool": tc.tool_name,
                        "error_snippet": error_snippet,
                        "next_actions": "",
                    },
                    severity_override=Severity.MEDIUM,
                    summary_override=f"Tool error on final call never addressed: {tc.tool_name}",
                )
            )
            continue

        window = remaining[:lookahead_window]
        failed_file = (
            tc.input_str("target_file") or tc.input_str("file_path") or tc.input_str("path")
        )


        cmd_not_found = False
        missing_binary = ""
        if tc.tool_name == "run_terminal_command":
            output = tc.result_content or ""
            if "not found" in output.lower():
                cmd_not_found = True
                m = re.search(r"(\S+):\s*(?:command\s+)?not found", output, re.IGNORECASE)
                if m:
                    missing_binary = m.group(1).split("/")[-1]

        if not _is_error_addressed(tc, window, failed_file, cmd_not_found, missing_binary):
            next_actions = ", ".join(n.tool_name for n in window)
            error_snippet = tc.result_content[:200] if tc.result_content else "(no content)"
            matches.append(
                Match(
                    tool_calls=[tc],
                    variables={
                        "tool": tc.tool_name,
                        "error_snippet": error_snippet,
                        "next_actions": next_actions,
                    },
                    severity_override=Severity.HIGH,
                    summary_override=f"Tool error ignored: {tc.tool_name}",
                )
            )

    return matches


_DEFAULT_ERROR_PATTERNS = TERMINAL_ERROR_RE.pattern

@detector("terminal_errors_ignored")
def detect_terminal_errors_ignored(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Check terminal command output for error patterns (configurable via
    `error_patterns` param).  Check next `lookahead_window` calls for
    meaningful follow-up.
    """
    matches: list[Match] = []

    lookahead_window: int = params.as_int("lookahead_window", 3)
    error_patterns: re.Pattern[str] = re.compile(
        params.as_str("error_patterns", _DEFAULT_ERROR_PATTERNS),
        re.IGNORECASE,
    )

    if not tool_calls:
        return matches

    def _extract_error_context(output: str, cmd: str) -> tuple[str, list[str]]:
        """Extract the error snippet and relevant file/topic keywords."""
        found = error_patterns.findall(output)
        snippet = found[0].strip()[:80] if found else ""
        keywords: set[str] = set()
        for f in re.findall(r"[\w/.-]+\.\w{1,5}", cmd):
            keywords.add(f.split("/")[-1])
        m = re.search(r"(\S+):\s*(?:command\s+)?not found", output, re.IGNORECASE)
        if m:
            keywords.add(m.group(1).split("/")[-1])
        m = re.search(r"No module named '(\S+)'", output)
        if m:
            keywords.add(m.group(1))
        return snippet, list(keywords)

    def _is_addressed(tc: ToolCall, keywords: list[str], window: list[ToolCall]) -> bool:
        """Check if the next few tool calls meaningfully address the error."""
        for nxt in window:
            ri = nxt.raw_input
            ri_map = ri.raw() if hasattr(ri, "raw") else (ri if isinstance(ri, dict) else {})
            nxt_input_str = json.dumps(ri_map)[:300].lower()
            if keywords and any(kw.lower() in nxt_input_str for kw in keywords):
                return True
            if nxt.tool_name == "search_replace":
                return True
            if nxt.tool_name == "grep":
                return True
            if nxt.tool_name == "read_file":
                return True
            if nxt.tool_name == "run_terminal_command":
                next_cmd = nxt.input_str("command")
                if any(inst in next_cmd for inst in ["install", "apt-get", "pip", "npm"]):
                    return True
                if keywords and any(kw.lower() in next_cmd.lower() for kw in keywords):
                    return True
        return False

    for i, tc in enumerate(tool_calls):
        if tc.tool_name != "run_terminal_command":
            continue
        if not tc.result_content:
            continue

        found = error_patterns.findall(tc.result_content)
        if not found:
            continue

        remaining = tool_calls[i + 1 :]
        if not remaining:
            continue

        cmd = tc.input_str("command")
        snippet, keywords = _extract_error_context(tc.result_content, cmd)
        window = remaining[:lookahead_window]

        if not _is_addressed(tc, keywords, window):
            next_actions = ", ".join(n.tool_name for n in window)
            matches.append(
                Match(
                    tool_calls=[tc],
                    variables=as_json_object(
                        {
                            "command": cmd[:120],
                            "error_snippet": snippet,
                            "keywords": keywords[:5],
                            "next_actions": next_actions,
                        }
                    ),
                    severity_override=Severity.MEDIUM,
                    summary_override=f"Terminal error not addressed: `{cmd[:50]}`",
                )
            )

    return matches
