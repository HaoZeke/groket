"""Detectors for behavioral anti-patterns."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, Severity, ToolCall, as_json_object

from patterns import (
    BUILD_OR_TEST_RE,
    ENV_FAILURE_RE,
    HEREDOC_WRITE_RE,
    SERVER_CMD_RE,
    TERMINAL_ERROR_RE,
    had_successful_verification_before,
    is_build_progress_only,
    is_infra_error,
    is_truncated_only_output,
)

_EXPECTED_SIGNAL_KILL = SERVER_CMD_RE


_DEFAULT_COMPLETION_ERROR_PATTERNS = TERMINAL_ERROR_RE.pattern
_DEFAULT_COMPLETION_ERROR_RE = re.compile(_DEFAULT_COMPLETION_ERROR_PATTERNS, re.IGNORECASE)

@detector("premature_completion")
def detect_premature_completion(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect sessions where the model marks task complete (via
    `completion_tools`) while last terminal command had errors.
    Also detects sessions ending with unresolved errors in last 5 calls.
    """
    matches: list[Match] = []

    completion_tools: set[str] = set(
        params.as_str_list("completion_tools", ["todo_write", "update_goal"])
    )
    _ep_override = params.as_str_opt("error_patterns")
    error_patterns: re.Pattern[str] = (
        re.compile(_ep_override, re.IGNORECASE) if _ep_override else _DEFAULT_COMPLETION_ERROR_RE
    )

    if not tool_calls:
        return matches

    def _is_real_error(tc: ToolCall) -> bool:
        """Check if a terminal command has a real *task* error.

        Infra noise (git author not set), env failures, and non-build/test
        failures are excluded — they don't mean the task was incomplete.
        """
        cmd = tc.input_str("command")
        output = tc.result_content or ""


        if "signal" in output[:100].lower() or "killed" in output[:100].lower():
            if _EXPECTED_SIGNAL_KILL.search(cmd):
                return False


        if "automatically moved to background" in output:
            return False
        if "Background task" in output and "started" in output[:200]:
            return False
        if tc.signal and tc.signal.lower() in ("killed", "sigkill", "sigterm"):
            return False




        m = HEREDOC_WRITE_RE.search(cmd)
        if m and m.group(1).startswith("/tmp/"):
            return False


        if ENV_FAILURE_RE.search(output[:1000]) or is_infra_error(output):
            return False
        if is_build_progress_only(output):
            return False
        if is_truncated_only_output(output):
            return False



        is_build_or_test = bool(BUILD_OR_TEST_RE.search(cmd))
        has_test_fail = bool(
            re.search(
                r"\bFAILED\b|\d+\s+failed|tests?\s+failed|Build FAILED|error\[E\d+\]",
                output[:2000],
                re.IGNORECASE,
            )
        )

        if not is_build_or_test and not has_test_fail:
            return False

        if tc.is_error:
            if _EXPECTED_SIGNAL_KILL.search(cmd):
                return False
            if ENV_FAILURE_RE.search(output[:1000]):
                return False

            if not has_test_fail and not error_patterns.search(output):
                return False
            return True


        if output.startswith("exit: 0"):
            if re.search(r"\d+\s+(?:failed|errors?)\s*[,\n]", output, re.IGNORECASE):
                return True
            if re.search(r"FAILED.*\d+\s+passed", output, re.IGNORECASE):
                return True
            return False

        return bool(error_patterns.search(output))


    last_terminal_result: tuple[ToolCall, bool] | None = None

    for i, tc in enumerate(tool_calls):
        if tc.tool_name == "run_terminal_command":
            had_errors = _is_real_error(tc)
            last_terminal_result = (tc, had_errors)

        elif tc.tool_name in completion_tools and last_terminal_result:
            prev_tc, had_errors = last_terminal_result
            if not had_errors:
                continue



            if had_successful_verification_before(tool_calls, i):
                continue

            is_completion = False
            if tc.tool_name == "update_goal":
                is_completion = tc.inputs().as_bool("completed") is True
            elif tc.tool_name == "todo_write":
                todos = tc.inputs().get("todos") or []
                if isinstance(todos, list):
                    is_completion = any(
                        t.get("status") == "completed" for t in todos if isinstance(t, dict)
                    )

            if is_completion:
                prev_cmd = prev_tc.input_str("command")[:80]
                error_match = error_patterns.search(prev_tc.result_content or "")
                error_snippet = error_match.group(0).strip()[:60] if error_match else "(error)"
                matches.append(
                    Match(
                        tool_calls=[tc, prev_tc],
                        variables={
                            "command": prev_cmd,
                            "error_snippet": error_snippet,
                            "completion_tool": tc.tool_name,
                        },
                        severity_override=Severity.HIGH,
                        summary_override=("Marked task complete while last command had errors"),
                        detail_override=(
                            f"Recent command `{prev_cmd}` had errors "
                            f"({error_snippet}) but completion was declared "
                            f"via {tc.tool_name} without resolving them."
                        ),
                    )
                )


    if len(tool_calls) >= 5:
        last_terminal: ToolCall | None = None
        last_terminal_idx = -1
        for idx in range(len(tool_calls) - 1, -1, -1):
            if tool_calls[idx].tool_name == "run_terminal_command":
                last_terminal = tool_calls[idx]
                last_terminal_idx = idx
                break

        if last_terminal and _is_real_error(last_terminal):

            if not had_successful_verification_before(tool_calls, len(tool_calls)):
                remaining_after = tool_calls[last_terminal_idx + 1 :]
                substantive_after = [
                    tc
                    for tc in remaining_after
                    if tc.tool_name in ("search_replace", "run_terminal_command")
                ]
                if not substantive_after:
                    cmd = last_terminal.input_str("command")[:80]
                    error_match = error_patterns.search(last_terminal.result_content or "")
                    error_snippet = error_match.group(0).strip()[:60] if error_match else "(error)"
                    matches.append(
                        Match(
                            tool_calls=[last_terminal],
                            variables={
                                "command": cmd,
                                "error_snippet": error_snippet,
                                "completion_tool": "",
                            },
                            severity_override=Severity.HIGH,
                            summary_override="Session ended with unresolved errors",
                            detail_override=(
                                f"Recent command `{cmd}` had errors "
                                f"({error_snippet}) but the session ended "
                                f"without resolving them."
                            ),
                        )
                    )

    return matches

_DEFAULT_FLAG_IN_PATH_PATTERN = (
    r'^["\s]*-?[iBAC]["\s]'
    r'|^"[iBAC]"\s*(true|false|\d+)'
    r"|^\s+$"
)
_DEFAULT_FLAG_IN_PATH_RE = re.compile(_DEFAULT_FLAG_IN_PATH_PATTERN)

@detector("malformed_tool_arguments")
def detect_malformed_tool_arguments(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Check grep tool calls for malformed arguments: flag-like values in
    path field, embedded quotes in path, spaced-out paths, null-prefixed
    paths/globs, bare glob extensions.
    """
    matches: list[Match] = []

    _fip_override = params.as_str_opt("flag_in_path_pattern")
    flag_in_path_pattern: re.Pattern[str] = (
        re.compile(_fip_override) if _fip_override else _DEFAULT_FLAG_IN_PATH_RE
    )

    if not tool_calls:
        return matches

    def _check_path(tc: ToolCall, path: str, field_name: str) -> bool:
        """Check a path-like field for malformation.  Returns True if bad."""
        if not path:
            return False

        reason = ""
        if flag_in_path_pattern.search(path):
            reason = "contains flag-like value"
        elif '"' in path and not path.startswith("/"):
            reason = "contains embedded quotes"
        elif len(path) > 5 and path[1] == " " and path[3] == " ":
            reason = "has spaced-out characters"

        if not reason:
            return False

        val_repr = repr(path[:40])
        matches.append(
            Match(
                tool_calls=[tc],
                variables={"field_name": field_name, "value": val_repr, "count": 1},
                severity_override=Severity.HIGH,
                summary_override=f"Grep {field_name} {reason}: {val_repr}",
                detail_override=(f"Malformed `{field_name}` in 1 tool call. Value: {val_repr}"),
            )
        )
        return True


    null_path_calls: list[ToolCall] = []
    null_glob_calls: list[ToolCall] = []
    bare_glob_calls: dict[str, list[ToolCall]] = defaultdict(list)

    for tc in tool_calls:
        if tc.tool_name != "grep":
            continue

        path = tc.input_str("path") or ""
        glob_val = tc.input_str("glob") or ""


        if _check_path(tc, path, "path"):
            continue


        if glob_val:

            if glob_val.startswith("null"):
                null_glob_calls.append(tc)
                continue


            if (
                len(glob_val) <= 4
                and not glob_val.startswith(("*", "!", "."))
                and "*" not in glob_val
                and "/" not in glob_val
                and "." not in glob_val
            ):
                bare_glob_calls[glob_val].append(tc)
                continue


        if path and path.startswith("null"):
            null_path_calls.append(tc)


    def _emit_aggregated(
        field: str,
        tcs: list[ToolCall],
        value: str,
        summary: str,
    ) -> None:
        matches.append(
            Match(
                tool_calls=tcs[:10],
                variables={"field_name": field, "value": value, "count": len(tcs)},
                severity_override=Severity.HIGH,
                summary_override=summary,
                detail_override=f"Malformed `{field}` in {len(tcs)} tool call(s). Value: {value}",
            )
        )

    if null_path_calls:
        val = repr(null_path_calls[0].input_str("path")[:60])
        _emit_aggregated(
            "path",
            null_path_calls,
            val,
            f"Grep path has null prefix in {len(null_path_calls)} call(s)",
        )

    if null_glob_calls:
        val = repr(null_glob_calls[0].input_str("glob")[:60])
        _emit_aggregated(
            "glob",
            null_glob_calls,
            val,
            f"Grep glob has null prefix in {len(null_glob_calls)} call(s)",
        )

    for ext_val, tcs in bare_glob_calls.items():
        _emit_aggregated(
            "glob",
            tcs,
            ext_val,
            f"Grep glob is bare extension '{ext_val}' in "
            f"{len(tcs)} call(s) (should be *.{ext_val})",
        )

    return matches

_BG_TASK_RE = re.compile(r"Background task (\S+) started")

@detector("background_check")
def detect_background_task_issues(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Track background tasks started (run_terminal_command with
    background=True, extract task_id from result) and
    get_command_or_subagent_output calls checking task IDs.
    Report unchecked background tasks.
    """
    matches: list[Match] = []

    if not tool_calls:
        return matches

    bg_tasks: dict[str, ToolCall] = {}
    bg_task_checked: set[str] = set()

    for tc in tool_calls:
        if tc.tool_name == "run_terminal_command":
            if tc.raw_input.get("background", False):
                m = _BG_TASK_RE.search(tc.result_content or "")
                if m:
                    bg_tasks[m.group(1)] = tc
                else:

                    bg_tasks[tc.call_id] = tc

        if tc.tool_name == "get_command_or_subagent_output":
            task_id = tc.input_str("task_id")
            if task_id:
                bg_task_checked.add(task_id)

    unchecked_ids = set(bg_tasks.keys()) - bg_task_checked
    if unchecked_ids:
        unchecked_tcs = [bg_tasks[tid] for tid in unchecked_ids]
        matches.append(
            Match(
                tool_calls=unchecked_tcs,
                variables=as_json_object(
                    {
                        "count": len(unchecked_ids),
                        "unchecked_ids": sorted(unchecked_ids),
                    }
                ),
                severity_override=Severity.MEDIUM,
                summary_override=(
                    f"Background task(s) started but never checked ({len(unchecked_ids)})"
                ),
            )
        )

    return matches

_DEFAULT_BAD_PATH_PATTERN = (
    r"^(null|undefined|None|NaN)"
    r"|(//)"
    r"|([\x00-\x1f])"
)

@detector("malformed_paths")
def detect_malformed_paths(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Check path arguments in read_file, search_replace, grep, list_dir for
    null/undefined/None prefix, double slashes, control chars.
    Group findings by prefix pattern.
    """
    matches: list[Match] = []

    bad_path_pattern: re.Pattern[str] = re.compile(
        params.as_str("bad_path_pattern", _DEFAULT_BAD_PATH_PATTERN)
    )

    if not tool_calls:
        return matches

    bad_calls: dict[str, list[tuple[ToolCall, str]]] = defaultdict(list)

    for tc in tool_calls:
        paths_to_check: list[str] = []
        if tc.tool_name == "read_file":
            paths_to_check.append(tc.input_str("target_file"))
        elif tc.tool_name == "search_replace":
            paths_to_check.append(tc.input_str("file_path"))
        elif tc.tool_name == "grep":
            paths_to_check.append(tc.input_str("path"))
        elif tc.tool_name == "list_dir":
            paths_to_check.append(tc.input_str("target_directory"))

        for path in paths_to_check:
            if not path or path.startswith(("http://", "https://")):
                continue
            m = bad_path_pattern.search(path)
            if m:
                prefix = m.group(0)
                bad_calls[prefix].append((tc, path))

    for prefix, entries in bad_calls.items():
        unique_paths = sorted({path for _, path in entries})
        tcs = [tc for tc, _ in entries]
        matches.append(
            Match(
                tool_calls=tcs[:10],
                variables=as_json_object(
                    {
                        "prefix": prefix,
                        "count": len(entries),
                        "paths": unique_paths[:8],
                    }
                ),
                severity_override=Severity.HIGH,
                summary_override=(
                    f"Malformed `{prefix}` path prefix in {len(entries)} tool call(s)"
                    if len(entries) > 1
                    else f"Malformed path: `{entries[0][1][:60]}`"
                ),
            )
        )

    return matches

@detector("failed_edit_patterns")
def detect_failed_edit_patterns(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Classify search_replace failures: no-op edits (old==new), ambiguous
    matches (found multiple times), blind retries (same failing edit without
    re-reading file).  Returns one Match per failure type found.
    """
    matches: list[Match] = []

    if not tool_calls:
        return matches

    noop_edits: list[tuple[int, ToolCall]] = []
    ambiguous: list[tuple[int, ToolCall]] = []
    blind_retries: list[tuple[int, ToolCall, int]] = []

    for i, tc in enumerate(tool_calls):
        if tc.tool_name != "search_replace" or not tc.is_error:
            continue

        result = (tc.result_content or "").lower()
        old_str = tc.input_str("old_string")
        file_path = tc.input_str("file_path")

        if "old string and new string are the same" in result:
            noop_edits.append((i, tc))
        elif "found multiple times" in result or "not unique" in result:
            ambiguous.append((i, tc))
        elif "not found" in result:

            key = (file_path, old_str[:100])
            for j in range(i - 1, max(i - 6, -1), -1):
                prev = tool_calls[j]
                if prev.tool_name == "search_replace" and prev.is_error:
                    prev_key = (
                        prev.input_str("file_path"),
                        prev.input_str("old_string")[:100],
                    )
                    if prev_key == key:
                        between = tool_calls[j + 1 : i]
                        read_it = any(
                            t.tool_name == "read_file" and t.input_str("target_file") == file_path
                            for t in between
                        )
                        if not read_it:
                            blind_retries.append((i, tc, j))
                        break


    if noop_edits:
        files = sorted({tc.input_str("file_path") for _, tc in noop_edits})
        matches.append(
            Match(
                tool_calls=[tc for _, tc in noop_edits],
                variables=as_json_object(
                    {
                        "type": "noop",
                        "count": len(noop_edits),
                        "files": files,
                    }
                ),
                severity_override=Severity.MEDIUM,
                summary_override=(f"No-op edits: old_string == new_string ({len(noop_edits)}x)"),
            )
        )

    if ambiguous:
        files = sorted({tc.input_str("file_path") for _, tc in ambiguous})
        matches.append(
            Match(
                tool_calls=[tc for _, tc in ambiguous],
                variables=as_json_object(
                    {
                        "type": "ambiguous",
                        "count": len(ambiguous),
                        "files": files,
                    }
                ),
                severity_override=Severity.MEDIUM,
                summary_override=(f"Ambiguous edits: old_string not unique ({len(ambiguous)}x)"),
            )
        )

    if blind_retries:
        by_file: dict[str, list[tuple[int, ToolCall]]] = defaultdict(list)
        for pos, tc, _prev_pos in blind_retries:
            f = tc.input_str("file_path")
            by_file[f].append((pos, tc))

        for f, entries in by_file.items():
            matches.append(
                Match(
                    tool_calls=[tc for _, tc in entries],
                    variables=as_json_object(
                        {
                            "type": "blind_retry",
                            "count": len(entries),
                            "files": [f],
                        }
                    ),
                    severity_override=Severity.HIGH,
                    summary_override=(
                        f"Retried same failing edit without re-reading "
                        f"`{f.split('/')[-1]}` ({len(entries)}x)"
                    ),
                )
            )

    return matches
