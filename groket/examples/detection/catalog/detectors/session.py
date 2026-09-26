"""Session-pattern detectors — detect higher-level anti-patterns that span
multiple tool calls: edit-break-fix cycles, build/test failures, and loops."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, Severity, ToolCall

from patterns import (
    BUILD_CMD_RE,
    BUILD_OR_TEST_RE,
    BUILD_PROGRESS_RE,
    COMPILE_ERROR_RE,
    CRASH_RE,
    ERROR_IN_OUTPUT_RE,
    HEREDOC_WRITE_RE,
    INLINE_SCRIPT_RE,
    TEMP_PREFIXES,
    TEST_CMD_RE,
    TEST_FAIL_RE,
    is_build_progress_only,
    is_env_failure,
    is_truncated_only_output,
)

_CONFIGCRASH_RE = re.compile(
    r"_pytest/config|pytest_cmdline_parse|PytestConfigWarning"
    r"|pluggy.*_hooks|Failed to import filter module"
    r"|ModuleNotFoundError:.*(?:coverage|pluggy|pytest_)",
    re.IGNORECASE,
)
_TEST_RESULTS_RE = re.compile(r"\d+\s+passed", re.IGNORECASE)

def _is_config_crash(output: str) -> bool:
    """Check if test failure is a framework config crash, not a test failure."""
    return bool(_CONFIGCRASH_RE.search(output)) and not bool(_TEST_RESULTS_RE.search(output))


_PYTEST_FAIL_RE = re.compile(r"FAILED\s+(\S+::\S+)", re.MULTILINE)
_CARGO_FAIL_RE = re.compile(r"(\S+)\s+\.\.\.\s+FAILED", re.MULTILINE)
_GENERIC_FAIL_RE = re.compile(r"FAIL:\s+(\S+)", re.MULTILINE)

def _extract_failing_tests(output: str) -> set[str]:
    """Extract individual test names from test output."""
    names: set[str] = set()
    for m in _PYTEST_FAIL_RE.finditer(output):
        names.add(m.group(1))
    for m in _CARGO_FAIL_RE.finditer(output):
        names.add(m.group(1))
    for m in _GENERIC_FAIL_RE.finditer(output):
        names.add(m.group(1))
    return names

def _extract_error_line(output: str, tc: ToolCall) -> str:
    """Extract the first error/crash line from build output."""
    for line in output.splitlines():
        if re.search(r"error|fault|panic|signal", line, re.IGNORECASE):
            return line.strip()[:120]
    if tc.signal:
        return f"Process killed by {tc.signal}"
    return ""

def _get_edited_file(tc: ToolCall) -> str:
    """Get edited file from search_replace or cat heredoc."""
    if tc.tool_name == "search_replace":
        return tc.input_str("file_path")
    if tc.tool_name == "run_terminal_command":
        m = HEREDOC_WRITE_RE.search(tc.input_str("command"))
        return m.group(1) if m else ""
    return ""

def _is_build_failure(tc: ToolCall) -> bool:
    """Check if a terminal command represents a build/test failure."""
    output = tc.result_content or ""
    return (
        tc.is_error
        or bool(COMPILE_ERROR_RE.search(output))
        or bool(CRASH_RE.search(output))
        or bool(tc.signal)
    )

def _build_failure_match(
    involved_tcs: list[ToolCall],
    edit_file: str,
    edit_path: str,
    cmd: str,
    error_line: str,
    fail_count: int,
    context_label: str,
) -> Match:
    """Create a Match for a build failure caused by an edit."""
    sev = Severity.HIGH if fail_count <= 2 else Severity.MEDIUM
    count_note = f"\n  (Build failure #{fail_count} on {edit_file})" if fail_count > 1 else ""
    return Match(
        tool_calls=involved_tcs,
        variables={
            "edit_file": edit_file,
            "command": cmd[:100],
            "error_line": error_line,
            "fail_count": fail_count,
        },
        severity_override=sev,
        summary_override=f"Edit to {edit_file} caused build failure/crash",
        detail_override=(
            f"{context_label}\n"
            f"  Edited file: {edit_path}\n"
            f"  Command: {cmd[:100]}\n"
            f"  Error: {error_line}\n"
            f"The model should reason through the edit more carefully." + count_note
        ),
    )

@detector("edit_build_failure")
def edit_build_failure(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect search_replace edits (or cat/tee file writes) followed by
    build commands that fail with compile/syntax errors, or build-and-run
    commands that crash at runtime.  Also catches combined heredoc+build
    commands like `cat > file << 'EOF' ... && gcc ...`."""
    max_per_file: int = params.as_int("max_per_file", 4)
    build_cmd_pattern: str = params.as_str("build_cmd_pattern", "")
    compile_error_patterns: str = params.as_str("compile_error_patterns", "")

    build_re = re.compile(build_cmd_pattern, re.IGNORECASE) if build_cmd_pattern else BUILD_CMD_RE
    compile_re = (
        re.compile(compile_error_patterns, re.IGNORECASE)
        if compile_error_patterns
        else COMPILE_ERROR_RE
    )

    results: list[Match] = []
    build_fail_count: dict[str, int] = defaultdict(int)
    last_edit: ToolCall | None = None
    last_edit_file: str = ""

    def _is_compile_or_crash(output: str, tc: ToolCall) -> bool:
        has_compile = bool(compile_re.search(output))
        has_crash = bool(CRASH_RE.search(output)) or bool(tc.signal)



        if has_compile and not has_crash:
            cmd = tc.input_str("command")
            if INLINE_SCRIPT_RE.search(cmd):
                if 'File "<string>"' in output or 'File "<stdin>"' in output:
                    return False


        if is_build_progress_only(output):
            return False
        if is_truncated_only_output(output):
            return False
        if not has_compile and not has_crash:
            if BUILD_PROGRESS_RE.search(output) and not re.search(
                r"error(?:\[E\d+\])?:\s", output, re.IGNORECASE
            ):
                return False


        if has_compile or has_crash:
            return True
        return False

    for tc in tool_calls:
        if tc.tool_name == "search_replace" and not tc.is_error:
            last_edit = tc
            last_edit_file = tc.input_str("file_path")
            continue

        if tc.tool_name != "run_terminal_command":
            continue

        cmd = tc.input_str("command")
        output = tc.result_content
        m = HEREDOC_WRITE_RE.search(cmd)
        is_heredoc = bool(m)


        if is_heredoc and build_re.search(cmd):
            edit_path = m.group(1) if m else ""


            if any(edit_path.startswith(p) for p in TEMP_PREFIXES):
                last_edit = None
                continue
            if _is_compile_or_crash(output, tc) and not is_env_failure(output):
                edit_file = edit_path.split("/")[-1] if edit_path else "unknown"
                build_fail_count[edit_file] += 1
                fc = build_fail_count[edit_file]
                if fc <= max_per_file:
                    results.append(
                        _build_failure_match(
                            [tc],
                            edit_file,
                            m.group(1) if m else "unknown",
                            cmd,
                            _extract_error_line(output, tc),
                            fc,
                            "A combined write+build command failed.",
                        )
                    )
            last_edit = None
            continue


        if is_heredoc and not tc.is_error:
            last_edit = tc
            last_edit_file = m.group(1) if m else ""
            continue


        if last_edit is not None and build_re.search(cmd):

            if any(last_edit_file.startswith(p) for p in TEMP_PREFIXES):
                last_edit = None
                continue
            if _is_compile_or_crash(output, tc) and not is_env_failure(output):
                edit_file = last_edit_file.split("/")[-1] if last_edit_file else "unknown"
                build_fail_count[edit_file] += 1
                fc = build_fail_count[edit_file]
                if fc <= max_per_file:
                    results.append(
                        _build_failure_match(
                            [last_edit, tc],
                            edit_file,
                            last_edit_file,
                            cmd,
                            _extract_error_line(output, tc),
                            fc,
                            "An edit was followed by a build command that failed or crashed.",
                        )
                    )
                last_edit = None
                continue


        if not tc.is_error and build_re.search(cmd):
            last_edit = None

    return results

@detector("edit_test_failure")
def edit_test_failure(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect search_replace or heredoc edits followed by test runs that
    crash or fail, indicating the edit introduced a bug."""
    max_per_file: int = params.as_int("max_per_file", 4)
    test_cmd_pattern: str = params.as_str("test_cmd_pattern", "")

    test_re = re.compile(test_cmd_pattern, re.IGNORECASE) if test_cmd_pattern else TEST_CMD_RE

    results: list[Match] = []


    recent_edits: list[tuple[ToolCall, str]] = []

    file_fail_count: dict[str, int] = defaultdict(int)

    known_failures: set[str] = set()

    for tc in tool_calls:
        if tc.tool_name == "search_replace" and not tc.is_error:
            f = tc.input_str("file_path")
            recent_edits.append((tc, f))
            continue

        if tc.tool_name == "run_terminal_command":
            cmd = tc.input_str("command")

            m = HEREDOC_WRITE_RE.search(cmd)
            if m and not tc.is_error and not test_re.search(cmd):
                recent_edits.append((tc, m.group(1)))
                continue

            output = tc.result_content or ""
            has_test = test_re.search(cmd)


            if has_test and not recent_edits:
                known_failures.update(_extract_failing_tests(output))
                continue

            if recent_edits:


                project_edits = [
                    (e, f)
                    for e, f in recent_edits
                    if not any(f.startswith(p) for p in TEMP_PREFIXES)
                ]
                if not project_edits:
                    recent_edits.clear()
                    continue
                has_fail = TEST_FAIL_RE.search(output) or tc.signal

                if has_test and (has_fail or tc.is_error):

                    if is_env_failure(output):
                        continue


                    if _is_config_crash(output):
                        continue



                    current_failures = _extract_failing_tests(output)
                    if current_failures and current_failures.issubset(known_failures):
                        known_failures.update(current_failures)
                        continue

                    known_failures.update(current_failures)



                    code_edits = [
                        (e, f)
                        for e, f in project_edits
                        if not (
                            "." in f
                            and f.rsplit(".", 1)[-1].lower() in ("rst", "md", "txt", "adoc")
                        )
                    ]
                    if not code_edits:
                        continue
                    crash_line = ""
                    for line in output.splitlines():
                        if TEST_FAIL_RE.search(line):
                            crash_line = line.strip()[:120]
                            break
                    if not crash_line and tc.signal:
                        crash_line = f"Process killed by {tc.signal}"




                    seen_files: list[str] = []
                    seen_set: set[str] = set()
                    for _, f in reversed(code_edits):
                        base = f.split("/")[-1]
                        if base not in seen_set:
                            seen_set.add(base)
                            seen_files.append(base)
                    edit_files = ", ".join(seen_files[:5])
                    edit_tcs = [e for e, _ in code_edits[-5:]]



                    primary_file = seen_files[0] if seen_files else ""
                    file_fail_count[primary_file] += 1
                    count = file_fail_count[primary_file]


                    if count <= 2:
                        sev = Severity.HIGH
                    elif count <= max_per_file:
                        sev = Severity.MEDIUM
                    else:

                        recent_edits.clear()
                        continue

                    results.append(
                        Match(
                            tool_calls=edit_tcs + [tc],
                            variables={
                                "edit_files": edit_files,
                                "command": cmd[:100],
                                "crash_line": crash_line,
                                "fail_count": count,
                            },
                            severity_override=sev,
                            summary_override=(f"Edit to {edit_files} caused test crash/failure"),
                            detail_override=(
                                f"Code edits were followed by a test that crashed "
                                f"or failed.\n"
                                f"  Edited: {edit_files}\n"
                                f"  Test command: {cmd[:100]}\n"
                                f"  Failure: {crash_line}\n"
                                f"The model introduced a bug in its own fix."
                                + (f"\n  (Failure #{count} on {primary_file})" if count > 1 else "")
                            ),
                        )
                    )
                    recent_edits.clear()


        if (
            tc.tool_name == "run_terminal_command"
            and not tc.is_error
            and test_re.search(tc.input_str("command"))
            and not TEST_FAIL_RE.search(tc.result_content or "")
        ):
            recent_edits.clear()

    return results

@detector("fix_cycle")
def fix_cycle(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect files that required 3+ rounds of edit -> build/test -> failure
    before the build/test succeeded, indicating trial-and-error fixing.
    Also detects combined heredoc+build commands (cat > f << EOF && gcc)."""
    min_cycles: int = params.as_int("min_cycles", 3)

    results: list[Match] = []

    file_cycles: dict[str, list[tuple[ToolCall, ToolCall]]] = defaultdict(list)
    file_last_edit: dict[str, ToolCall] = {}

    for tc in tool_calls:
        if tc.tool_name == "search_replace" and not tc.is_error:
            f = tc.input_str("file_path")
            if f:
                file_last_edit[f] = tc
            continue

        if tc.tool_name == "run_terminal_command":
            cmd = tc.input_str("command")


            m = HEREDOC_WRITE_RE.search(cmd)
            has_heredoc = bool(m)
            has_build = bool(BUILD_OR_TEST_RE.search(cmd))


            if has_heredoc and has_build and m is not None:
                f = m.group(1)
                if _is_build_failure(tc):
                    file_cycles[f].append((tc, tc))
                else:

                    if f in file_cycles and len(file_cycles[f]) >= min_cycles:
                        _emit_cycle_match(results, f, file_cycles[f], resolved=True)
                    file_cycles.pop(f, None)
                    file_last_edit.pop(f, None)
                continue


            if has_heredoc and not tc.is_error and m is not None:
                f = m.group(1)
                if f:
                    file_last_edit[f] = tc

            if not has_build:
                continue

            if _is_build_failure(tc) and not is_env_failure(tc.result_content):

                error_files = set()
                if tc.result_content:
                    for ef in re.findall(r"[\w/.-]+\.\w{1,5}", tc.result_content[:1000]):
                        ef_base = ef.split("/")[-1]
                        for f in file_last_edit:
                            if f.endswith(ef) or f.split("/")[-1] == ef_base:
                                error_files.add(f)

                blame_files = error_files if error_files else set(file_last_edit.keys())
                for f in blame_files:
                    if f in file_last_edit:
                        file_cycles[f].append((file_last_edit[f], tc))
                file_last_edit.clear()
            else:
                for f, cycles in list(file_cycles.items()):
                    if len(cycles) >= min_cycles:
                        _emit_cycle_match(results, f, cycles, resolved=True)
                file_cycles.clear()
                file_last_edit.clear()


    for f, cycles in file_cycles.items():
        if len(cycles) >= min_cycles:
            _emit_cycle_match(results, f, cycles, resolved=False)

    return results

def _emit_cycle_match(
    results: list[Match],
    f: str,
    cycles: list[tuple[ToolCall, ToolCall]],
    resolved: bool = True,
) -> None:
    """Add a Match for a series of edit-fail cycles on a file."""
    all_tcs: list[ToolCall] = []
    seen: set[str] = set()
    for edit_tc, build_tc in cycles[:5]:
        if edit_tc.call_id not in seen:
            seen.add(edit_tc.call_id)
            all_tcs.append(edit_tc)
        if build_tc.call_id not in seen:
            seen.add(build_tc.call_id)
            all_tcs.append(build_tc)

    suffix = "" if resolved else " (never resolved)"
    results.append(
        Match(
            tool_calls=all_tcs[:10],
            variables={
                "file": f,
                "cycle_count": len(cycles),
                "resolved": resolved,
            },
            summary_override=(f"{len(cycles)} edit-fail cycles on {f.split('/')[-1]}{suffix}"),
            detail_override=(
                f"File '{f}' had {len(cycles)} rounds of edit -> build/test -> "
                f"failure"
                f"{'  before success' if resolved else ' and the session ended without success'}.\n"
                f"This indicates trial-and-error fixing rather than reasoning "
                f"through the code before writing."
            ),
        )
    )

@detector("edit_build_loops")
def edit_build_loops(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect excessive edit-build-fail-edit cycles on the same file by
    looking ahead from each edit for a failing build, then another edit."""
    min_cycles: int = params.as_int("min_cycles", 3)
    lookahead: int = params.as_int("lookahead", 6)

    build_cmds_re = re.compile(
        params.as_str("build_cmd_pattern", BUILD_OR_TEST_RE.pattern),
        re.IGNORECASE,
    )
    error_re = re.compile(
        params.as_str("error_pattern", ERROR_IN_OUTPUT_RE.pattern),
        re.IGNORECASE,
    )

    results: list[Match] = []
    cycles_by_file: dict[str, list[int]] = defaultdict(list)

    for i, tc in enumerate(tool_calls):
        edited_file = _get_edited_file(tc)
        if not edited_file:
            continue
        if tc.tool_name == "search_replace" and tc.is_error:
            continue

        for j in range(i + 1, min(i + lookahead, len(tool_calls))):
            build_tc = tool_calls[j]
            if build_tc.tool_name != "run_terminal_command":
                continue
            cmd = build_tc.input_str("command")
            if not build_cmds_re.search(cmd):
                continue

            has_error = (
                build_tc.is_error
                or error_re.search(build_tc.result_content)
                or bool(build_tc.signal)
            )
            if not has_error:
                break

            for k in range(j + 1, min(j + lookahead, len(tool_calls))):
                next_edited = _get_edited_file(tool_calls[k])
                if next_edited == edited_file:
                    cycles_by_file[edited_file].append(i)
                    break
            break

    for f, positions in cycles_by_file.items():
        if len(positions) < min_cycles:
            continue
        involved_tcs = [tool_calls[p] for p in positions[:10]]
        results.append(
            Match(
                tool_calls=involved_tcs,
                variables={
                    "file": f,
                    "cycle_count": len(positions),
                },
                summary_override=(
                    f"Excessive edit-build-fail loop on {f.split('/')[-1]} "
                    f"({len(positions)} cycles)"
                ),
                detail_override=(
                    f"The model edited `{f}`, ran a build, got an error, and edited "
                    f"the same file again {len(positions)} times."
                ),
            )
        )

    return results
