"""Generic detectors — reusable functions parameterized via YAML rules."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import (
    ChatMessage,
    JsonObject,
    RuleParams,
    Severity,
    ToolCall,
    ToolInput,
    ToolInputBag,
    as_json_object,
    json_as_str_list,
)

_regex_cache: dict[str, re.Pattern[str]] = {}

def _compile(pattern: str) -> re.Pattern[str]:
    """Compile *pattern* with caching."""
    compiled = _regex_cache.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern, re.DOTALL)
        _regex_cache[pattern] = compiled
    return compiled

def _raw_input_str(raw_input: ToolInput, field: str) -> str:
    """Return a single tool-input field as text."""
    return ToolInputBag.ensure(raw_input).as_str(field)

def _command_snippet(tc: ToolCall, max_len: int = 80) -> str:
    """Extract a short command snippet from a ToolCall."""
    cmd = tc.input_str("command")
    if not isinstance(cmd, str):
        cmd = str(cmd)
    if len(cmd) > max_len:
        return cmd[:max_len] + "…"
    return cmd

@detector("count_matching_calls")
def count_matching_calls(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Filter tool calls matching criteria and fire if count >= threshold."""
    tools: list[str] = params.as_str_list("tools", [])
    input_patterns: dict[str, str] = params.as_str_dict("input_patterns")
    result_patterns: str = params.as_str("result_patterns", "")
    error_only: bool = params.as_bool("error_only", False)
    success_only: bool = params.as_bool("success_only", False)
    min_count: int = params.as_int("min_count", 1)
    group_by: str | None = params.as_str_opt("group_by")
    group_min: int = params.as_int("group_min", min_count)
    high_threshold: int | None = params.as_int_opt("high_threshold")


    compiled_input: dict[str, re.Pattern[str]] = {k: _compile(v) for k, v in input_patterns.items()}
    compiled_result: re.Pattern[str] | None = _compile(result_patterns) if result_patterns else None

    tools_set: set[str] = set(tools) if tools else set()

    matched: list[ToolCall] = []
    for tc in tool_calls:

        if tools_set and tc.tool_name not in tools_set:
            continue


        if error_only and not tc.is_error:
            continue
        if success_only and tc.is_error:
            continue


        skip = False
        for field_name, pat in compiled_input.items():
            value = _raw_input_str(tc.raw_input, field_name)
            if not pat.search(value):
                skip = True
                break
        if skip:
            continue


        if compiled_result and not compiled_result.search(tc.result_content or ""):
            continue

        matched.append(tc)

    if not matched:
        return []


    def _build_match(group_calls: list[ToolCall], group_key: str | None = None) -> Match:
        tool_names = sorted({tc.tool_name for tc in group_calls})
        error_count = sum(1 for tc in group_calls if tc.is_error)
        commands: list[str] = []
        for tc in group_calls:
            if tc.tool_name == "run_terminal_command":
                cmd = tc.input_str("command")
                if isinstance(cmd, str) and cmd:
                    commands.append(cmd[:120])
                if len(commands) >= 5:
                    break

        variables: JsonObject = {
            "count": len(group_calls),
            "error_count": error_count,
            "matched_tools": ", ".join(tool_names),
            "matched_commands": "; ".join(commands),
        }


        first = group_calls[0]
        for field_name in (
            "query",
            "pattern",
            "command",
            "target_file",
            "file_path",
            "target_directory",
        ):
            val = _raw_input_str(first.raw_input, field_name)
            if val:
                variables.setdefault(field_name, val[:120])

        severity_override: Severity | None = None
        if high_threshold is not None and len(group_calls) >= high_threshold:
            severity_override = Severity.HIGH

        if group_key is not None:
            variables["group_key"] = group_key

            if group_by and any(kw in group_by for kw in ("file", "path", "target")):
                variables["file"] = group_key

        return Match(
            tool_calls=group_calls,
            variables=variables,
            severity_override=severity_override,
        )

    results: list[Match] = []

    if group_by:
        groups: dict[str, list[ToolCall]] = defaultdict(list)
        for tc in matched:
            key = _raw_input_str(tc.raw_input, group_by)
            if not key:
                key = "<unknown>"
            groups[key].append(tc)

        for key, group_calls in groups.items():
            if len(group_calls) >= group_min:
                results.append(_build_match(group_calls, group_key=key))
    elif len(matched) >= min_count:
        results.append(_build_match(matched))

    return results

_DEFAULT_MUTATING_TOOLS = frozenset({"search_replace", "todo_write", "update_goal"})

def _signature_key(tc: ToolCall, key_fields: RuleParams | None) -> tuple[str, ...]:
    """Build a hashable signature for a tool call."""
    name = tc.tool_name


    if key_fields is not None and name in key_fields:
        fields = json_as_str_list(key_fields.get(name))
        parts: list[str] = [name]
        for f in fields:
            parts.append(_raw_input_str(tc.raw_input, f))
        return tuple(parts)


    ri = tc.inputs()
    if name == "read_file":
        return (
            name,
            ri.as_str("target_file"),
            str(ri.get("offset", "")),
            str(ri.get("limit", "")),
        )
    if name == "grep":
        return (name, ri.as_str("pattern"), ri.as_str("path"))
    if name == "run_terminal_command":
        cmd = ri.as_str("command")

        if "<<" in cmd or len(cmd) <= 200:
            return (name, cmd)
        return (name, cmd[:200])
    if name == "list_dir":
        return (name, ri.as_str("target_directory"))
    if name == "search_replace":
        return (
            name,
            ri.as_str("file_path"),
            ri.as_str("old_string"),
            ri.as_str("new_string"),
        )

    try:
        serialized = json.dumps(ri.raw(), sort_keys=True, default=str)
    except (TypeError, ValueError):
        serialized = str(ri.raw())
    return (name, serialized)

@detector("repeated_identical_calls")
def repeated_identical_calls(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect the same tool call repeated with no state-changing ops between."""
    min_repeat: int = params.as_int("min_repeat", 2)
    mutating_tools_list: list[str] = params.as_str_list("mutating_tools", [])
    mutating_tools: frozenset[str] = (
        frozenset(mutating_tools_list) if mutating_tools_list else _DEFAULT_MUTATING_TOOLS
    )
    high_threshold: int = params.as_int("high_threshold", 5)
    key_fields: RuleParams | None = (
        params.mapping("key_fields") if params.has("key_fields") else None
    )
    error_only: bool = params.as_bool("error_only", False)

    if len(tool_calls) < min_repeat:
        return []

    results: list[Match] = []

    streak_key: tuple[str, ...] | None = None
    streak_calls: list[ToolCall] = []

    def _flush_streak() -> None:
        if len(streak_calls) >= min_repeat:
            first = streak_calls[0]

            ri = first.inputs()
            if first.tool_name == "read_file":
                summary = ri.as_str("target_file") or str(ri.raw())
            elif first.tool_name == "grep":
                summary = f"/{ri.as_str('pattern')}/ in {ri.as_str('path') or '.'}"
            elif first.tool_name == "run_terminal_command":
                summary = ri.as_str("command")[:100]
            else:
                try:
                    summary = json.dumps(ri.raw(), default=str)[:120]
                except (TypeError, ValueError):
                    summary = str(ri.raw())[:120]

            severity = Severity.HIGH if len(streak_calls) >= high_threshold else None

            results.append(
                Match(
                    tool_calls=list(streak_calls),
                    variables={
                        "count": len(streak_calls),
                        "tool": first.tool_name,
                        "input_summary": summary,
                    },
                    severity_override=severity,
                )
            )

    for tc in tool_calls:

        if tc.tool_name in mutating_tools:
            _flush_streak()
            streak_key = None
            streak_calls = []
            continue


        if error_only and not tc.is_error:
            _flush_streak()
            streak_key = None
            streak_calls = []
            continue

        key = _signature_key(tc, key_fields)
        if key == streak_key:
            streak_calls.append(tc)
        else:
            _flush_streak()
            streak_key = key
            streak_calls = [tc]

    _flush_streak()
    return results

def _metric_vars(
    value: float,
    numerator: int,
    denominator: int,
    total: int,
    duration: int,
) -> JsonObject:
    """Build the standard variables dict for a session_metric match."""
    return {
        "total": total,
        "duration": duration,
        "metric_value": round(value, 4),
        "numerator": numerator,
        "denominator_value": denominator,
        "ratio": round(value, 4),
    }

def _check_threshold(value: float, threshold: float, comparison: str) -> bool:
    """Return True if *value* passes the *comparison* against *threshold*."""
    if comparison == "gt":
        return value > threshold
    if comparison == "lt":
        return value < threshold
    return value >= threshold

def _metric_tool_ratio(
    tool_calls: list[ToolCall],
    params: RuleParams,
    total: int,
    duration: int,
    threshold: float,
    comparison: str,
) -> list[Match]:
    num_set = set(params.as_str_list("numerator_tools"))
    numerator = sum(1 for tc in tool_calls if tc.tool_name in num_set)
    ratio = numerator / total if total else 0.0
    if not _check_threshold(ratio, threshold, comparison):
        return []
    return [
        Match(
            tool_calls=[],
            variables=_metric_vars(ratio, numerator, total, total, duration),
        )
    ]

def _metric_density(
    tool_calls: list[ToolCall],
    params: RuleParams,
    total: int,
    duration: int,
    threshold: float,
    comparison: str,
) -> list[Match]:
    denom_type: str = params.as_str("denominator", "duration")
    denom = duration if denom_type == "duration" else total
    value = total / denom if denom > 0 else 0.0
    if not _check_threshold(value, threshold, comparison):
        return []
    return [
        Match(
            tool_calls=[],
            variables=_metric_vars(value, total, denom, total, duration),
        )
    ]

def _metric_read_before_action(
    tool_calls: list[ToolCall],
    params: RuleParams,
    total: int,
    duration: int,
    threshold: float,
) -> list[Match]:
    action_set = set(params.as_str_list("action_tools"))
    read_set = set(params.as_str_list("read_tools"))
    min_reads: int = params.as_int("min_reads", 0)


    if not action_set:
        if params.as_bool("require_no_text_output", False):
            has_text = any(
                tc.tool_name == "update_goal"
                and tc.inputs().as_str("message")
                and not tc.inputs().as_bool("completed")
                for tc in tool_calls
            )
            if has_text:
                return []

        reads = sum(1 for tc in tool_calls if tc.tool_name in read_set)
        if reads < min_reads:
            return []
        ratio = reads / total if total else 0.0
        if not (ratio >= threshold and reads >= min_reads):
            return []

        variables = _metric_vars(ratio, reads, total, total, duration)
        variables["summary"] = (
            f"Read-only session: {reads}/{total} calls "
            f"({ratio:.0%}) are read-only with no substantive action"
        )
        variables["detail"] = (
            f"The model made {reads} read-only calls out of {total} total "
            f"({ratio:.0%}) with no edits, builds, or other actions.\n"
            f"Duration: {duration:.0f}s. This suggests the model got stuck "
            f"in an investigation loop."
        )
        return [Match(tool_calls=[], variables=variables)]


    reads_before = 0
    first_action_idx: int | None = None
    exclude_investigative = params.as_bool("exclude_investigative_cmds", False)

    for i, tc in enumerate(tool_calls):
        if exclude_investigative and tc.tool_name == "run_terminal_command":
            cmd = tc.input_str("command")
            if isinstance(cmd, str) and re.search(
                r"^\s*(ls|cat|head|tail|wc|file|stat|du|df)\s",
                cmd,
            ):
                continue
        if tc.tool_name in action_set:
            first_action_idx = i
            break
        if tc.tool_name in read_set:
            reads_before += 1

    if first_action_idx is None:
        return []

    denom = first_action_idx if first_action_idx > 0 else 1
    ratio = reads_before / denom
    if not (ratio > threshold and reads_before >= min_reads):
        return []
    return [
        Match(
            tool_calls=[],
            variables=_metric_vars(ratio, reads_before, denom, total, duration),
        )
    ]

@detector("session_metric")
def session_metric(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Compute a session-wide metric and fire if it exceeds/falls below a threshold."""
    metric: str = params.as_str("metric", "tool_ratio")
    threshold: float = params.as_float("threshold", 0.5)
    comparison: str = params.as_str("comparison", "gt")
    min_total: int = params.as_int("min_total", 10)

    total = len(tool_calls)
    if total < min_total:
        return []

    timestamps = [tc.timestamp for tc in tool_calls if tc.timestamp is not None]
    duration = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else 0

    min_duration: int = params.as_int("min_duration", 0)
    if min_duration and duration < min_duration:
        return []

    if metric == "tool_ratio":
        return _metric_tool_ratio(tool_calls, params, total, duration, threshold, comparison)
    if metric == "density":
        return _metric_density(tool_calls, params, total, duration, threshold, comparison)
    if metric == "read_before_action":
        return _metric_read_before_action(tool_calls, params, total, duration, threshold)
    return []

_BG_TASK_TOOLS = frozenset(
    {
        "get_command_or_subagent_output",
        "kill_command_or_subagent",
        "wait_commands_or_subagents",
    }
)

@detector("time_gap")
def time_gap(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect gaps between consecutive timestamps."""
    threshold_seconds: int = params.as_int("threshold_seconds", 120)
    slow_tools: list[str] = params.as_str_list("slow_tools", [])
    slow_tool_threshold: int = params.as_int("slow_tool_threshold_seconds", 180)
    high_severity_seconds: int = params.as_int("high_severity_seconds", 180)
    timeout_range: list[int] = params.as_int_list("timeout_range", [298, 310])

    slow_set: set[str] = set(slow_tools)
    timeout_low = timeout_range[0] if len(timeout_range) >= 1 else 298
    timeout_high = timeout_range[1] if len(timeout_range) >= 2 else 310

    slow_cmd_re: re.Pattern[str] | None = None
    slow_cmd_pattern: str = params.as_str("slow_cmd_pattern", "")
    if slow_cmd_pattern:
        slow_cmd_re = re.compile(slow_cmd_pattern, re.IGNORECASE)


    timed = [tc for tc in tool_calls if tc.timestamp is not None]
    if len(timed) < 2:
        return []

    results: list[Match] = []

    for i in range(1, len(timed)):
        prev = timed[i - 1]
        curr = timed[i]
        assert prev.timestamp is not None and curr.timestamp is not None
        gap = curr.timestamp - prev.timestamp


        effective_threshold = threshold_seconds
        if prev.tool_name in slow_set:
            effective_threshold = slow_tool_threshold

        elif slow_cmd_re and prev.tool_name == "run_terminal_command":
            prev_cmd = prev.input_str("command")
            if isinstance(prev_cmd, str) and slow_cmd_re.search(prev_cmd):
                effective_threshold = slow_tool_threshold

        if gap < effective_threshold:
            continue



        if prev.tool_name in _BG_TASK_TOOLS or curr.tool_name in _BG_TASK_TOOLS:
            continue



        if prev.tool_name == "run_terminal_command":
            bg = prev.inputs().as_bool("background", False)
            if bg is True or bg == "true":
                continue

        is_timeout = timeout_low <= gap <= timeout_high

        severity = Severity.HIGH if gap >= high_severity_seconds else None

        timeout_note = " (likely timeout)" if is_timeout else ""
        results.append(
            Match(
                tool_calls=[prev, curr],
                variables={
                    "gap": gap,
                    "prev_tool": prev.tool_name,
                    "curr_tool": curr.tool_name,
                    "is_timeout": is_timeout,
                    "timeout_note": timeout_note,
                },
                severity_override=severity,
            )
        )

    return results

@detector("consecutive_pattern")
def consecutive_pattern(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect N consecutive tool calls matching criteria without reset."""
    tools: list[str] = params.as_str_list("tools", [])
    match_condition: str = params.as_str("match_condition", "any")
    crash_patterns_str: str = params.as_str("crash_patterns", "")
    min_streak: int = params.as_int("min_streak", 3)
    reset_tools: list[str] = params.as_str_list("reset_tools", ["search_replace", "web_search"])

    tools_set: set[str] = set(tools) if tools else set()
    reset_set: set[str] = set(reset_tools)
    crash_re: re.Pattern[str] | None = _compile(crash_patterns_str) if crash_patterns_str else None

    def _matches_condition(tc: ToolCall) -> bool:
        """Check whether a tool call matches the configured condition."""

        if tools_set and tc.tool_name not in tools_set:
            return False

        if match_condition == "error":
            return tc.is_error
        elif match_condition == "signal":
            return tc.signal is not None and tc.signal != ""
        elif match_condition == "crash":
            if crash_re and crash_re.search(tc.result_content):
                return True




            return tc.signal is not None and tc.signal != ""
        elif match_condition == "any":
            return True
        return False

    if not tool_calls:
        return []

    results: list[Match] = []
    streak: list[ToolCall] = []

    def _flush_streak() -> None:
        if len(streak) >= min_streak:
            commands: list[str] = []
            for tc in streak:
                snippet = _command_snippet(tc)
                if snippet:
                    commands.append(snippet)

            results.append(
                Match(
                    tool_calls=list(streak),
                    variables=as_json_object(
                        {
                            "streak_count": len(streak),
                            "commands": commands,
                        }
                    ),
                )
            )

    for tc in tool_calls:

        if tc.tool_name in reset_set:
            _flush_streak()
            streak = []
            continue

        if _matches_condition(tc):
            streak.append(tc)
        else:
            _flush_streak()
            streak = []

    _flush_streak()
    return results

def _matches_trigger(tc: ToolCall, spec: RuleParams) -> bool:
    """Check if *tc* matches a trigger/follower spec."""
    spec_tools: list[str] = spec.as_str_list("tools", [])
    if spec_tools and tc.tool_name not in spec_tools:
        return False

    input_patterns: dict[str, str] = spec.as_str_dict("input_patterns")
    for field_name, pattern in input_patterns.items():
        value = _raw_input_str(tc.raw_input, field_name)
        if not _compile(pattern).search(value):
            return False

    return True

def _field_value(tc: ToolCall, field_name: str) -> str:
    """Get a field value, handling common aliases."""
    return _raw_input_str(tc.raw_input, field_name)

@detector("sequence_pair")
def sequence_pair(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect A-then-B or A-without-B within a window."""
    trigger_spec: RuleParams = params.mapping("trigger")
    follower_spec: RuleParams = params.mapping("follower")
    window: int = params.as_int("window", 3)
    invert: bool = params.as_bool("invert", False)
    match_fields: list[str] = params.as_str_list("match_fields", [])
    trigger_success_only: bool = params.as_bool("trigger_success_only", False)

    if not tool_calls:
        return []

    results: list[Match] = []
    n = len(tool_calls)

    for i, tc in enumerate(tool_calls):
        if not _matches_trigger(tc, trigger_spec):
            continue


        if trigger_success_only and tc.is_error:
            continue


        found_follower: ToolCall | None = None
        end = min(i + 1 + window, n)
        for j in range(i + 1, end):
            candidate = tool_calls[j]
            if not _matches_trigger(candidate, follower_spec):
                continue



            fields_ok = True
            for mf in match_fields:
                if ":" in mf:
                    trigger_field, follower_field = mf.split(":", 1)
                    trigger_val = _field_value(tc, trigger_field)
                    follower_val = _field_value(candidate, follower_field)
                else:
                    trigger_val = _field_value(tc, mf)
                    follower_val = _field_value(candidate, mf)
                if not trigger_val or trigger_val != follower_val:
                    fields_ok = False
                    break
            if fields_ok:
                found_follower = candidate
                break


        should_fire = False
        if invert and found_follower is None:

            should_fire = True
        elif not invert and found_follower is not None:

            should_fire = True

        if not should_fire:
            continue


        file_val = ""
        for mf in match_fields:
            val = _field_value(tc, mf)
            if val and any(kw in mf for kw in ("file", "path", "target")):
                file_val = val
                break
        if not file_val:

            for fname in ("file_path", "target_file", "path"):
                val = _field_value(tc, fname)
                if val:
                    file_val = val
                    break

        involved = [tc]
        if found_follower is not None:
            involved.append(found_follower)

        results.append(
            Match(
                tool_calls=involved,
                variables={
                    "trigger_tool": tc.tool_name,
                    "follower_tool": (found_follower.tool_name if found_follower else ""),
                    "file": file_val,
                },
            )
        )

    return results
