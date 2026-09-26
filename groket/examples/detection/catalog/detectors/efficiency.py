"""Efficiency detectors — detect inefficient tool usage patterns such as
fragmented reads, blind edits, and batchable git operations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, Severity, ToolCall

from patterns import (
    GIT_CMD_RE,
    TEMP_PREFIXES,
)


@dataclass(slots=True)
class _ReadMeta:
    """Last successful read_file position for a path (re-read detection)."""

    offset: int
    tc: ToolCall

def _detect_re_reads(
    tool_calls: list[ToolCall],
) -> tuple[list[Match], dict[str, list[tuple[int, int, ToolCall]]]]:
    """Detect files read from offset 0 multiple times without edits in between.

    Also returns reads_per_file for use by _detect_fragmented_reads.
    """
    results: list[Match] = []
    reads_per_file: dict[str, list[tuple[int, int, ToolCall]]] = defaultdict(list)
    read_files_meta: dict[str, _ReadMeta] = {}
    edited_since_read: set[str] = set()

    for tc in tool_calls:
        if tc.tool_name == "search_replace" and not tc.is_error:
            f = tc.input_str("file_path")
            if f:
                edited_since_read.add(f)
        elif tc.tool_name == "run_terminal_command" and not tc.is_error:
            cmd = tc.input_str("command")
            if any(kw in cmd for kw in ["sed -i", "patch ", ">> ", "> ", "tee "]):
                for f in list(read_files_meta.keys()):
                    if f.split("/")[-1] in cmd:
                        edited_since_read.add(f)

        if tc.tool_name != "read_file":
            continue
        f = tc.input_str("target_file")
        if not f:
            continue
        offset = tc.inputs().as_int("offset", 0)
        limit = tc.inputs().as_int("limit", 1000)

        prev = read_files_meta.get(f)
        if prev is not None and offset == 0 and prev.offset == 0 and f not in edited_since_read:
            results.append(
                Match(
                    tool_calls=[tc, prev.tc],
                    variables={"file": f, "sub_type": "re_read", "count": 2},
                    summary_override=f"Re-read same file from start: {f.split('/')[-1]}",
                    detail_override=f"File '{f}' was read from offset 0 multiple times.",
                )
            )
        read_files_meta[f] = _ReadMeta(offset=offset, tc=tc)
        edited_since_read.discard(f)
        reads_per_file[f].append((offset, limit, tc))

    return results, reads_per_file

def _detect_fragmented_reads(
    reads_per_file: dict[str, list[tuple[int, int, ToolCall]]],
    small_read_limit: int,
    min_small_reads: int,
) -> list[Match]:
    """Detect many small sequential reads on the same file."""
    results: list[Match] = []
    for f, reads in reads_per_file.items():
        if len(reads) < 5:
            continue
        small_reads = [(off, lim, tc) for off, lim, tc in reads if lim <= small_read_limit]
        if len(small_reads) < min_small_reads:
            continue
        offsets = sorted(off for off, _, _ in small_reads)
        sequential = sum(1 for i in range(len(offsets) - 1) if offsets[i + 1] - offsets[i] <= 60)
        if sequential >= 3:
            tcs = [tc for _, _, tc in small_reads[:8]]
            results.append(
                Match(
                    tool_calls=tcs,
                    variables={"file": f, "sub_type": "fragmented", "count": len(small_reads)},
                    summary_override=(
                        f"Fragmented reads on {f.split('/')[-1]}: {len(small_reads)} small reads"
                    ),
                    detail_override=(
                        f"File '{f}' was read in {len(small_reads)} small chunks "
                        f"(limit <= {small_read_limit} lines each) with sequential offsets.\n"
                        f"  Offsets: {offsets[:8]}\n"
                        f"A single larger read would be more efficient."
                    ),
                )
            )
    return results

def _detect_blind_edits(tool_calls: list[ToolCall]) -> list[Match]:
    """Detect search_replace edits on files never read with read_file."""
    read_files_set: set[str] = set()
    created_files: set[str] = set()

    for tc in tool_calls:
        if tc.tool_name == "read_file":
            read_files_set.add(tc.input_str("target_file"))
        elif tc.tool_name == "grep" and tc.result_content:
            for line in tc.result_content.splitlines()[:50]:
                if ":" in line:
                    fpath = line.split(":")[0].strip()
                    if fpath and "/" in fpath and not fpath.startswith(" "):
                        read_files_set.add(fpath)
        elif tc.tool_name == "search_replace" and not tc.is_error:
            if tc.input_str("old_string") == "":
                f = tc.input_str("file_path")
                if f:
                    created_files.add(f)

    blind_edits: dict[str, list[ToolCall]] = defaultdict(list)
    for tc in tool_calls:
        if tc.tool_name != "search_replace":
            continue
        f = tc.input_str("file_path")
        if not f or f in read_files_set or f in created_files:
            continue
        if any(f.startswith(p) for p in TEMP_PREFIXES):
            continue
        if tc.input_str("old_string") == "":
            continue
        blind_edits[f].append(tc)

    results: list[Match] = []
    for f, edits in blind_edits.items():
        count_str = f" ({len(edits)}x)" if len(edits) > 1 else ""
        results.append(
            Match(
                tool_calls=edits[:5],
                variables={"file": f, "sub_type": "blind_edit", "count": len(edits)},
                severity_override=Severity.MEDIUM,
                summary_override=(
                    f"Edited file without reading it first{count_str}: {f.split('/')[-1]}"
                ),
                detail_override=(
                    f"search_replace was called on '{f}' {len(edits)} time(s) but "
                    f"the file was never read with read_file first."
                ),
            )
        )
    return results

@detector("file_ops")
def file_ops(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect file operation anti-patterns: re-reads from offset 0,
    fragmented reads (many small sequential reads), and edits without
    reading the file first."""
    re_reads, reads_per_file = _detect_re_reads(tool_calls)
    fragmented = _detect_fragmented_reads(
        reads_per_file,
        small_read_limit=params.as_int("small_read_limit", 50),
        min_small_reads=params.as_int("min_small_reads", 4),
    )
    blind = _detect_blind_edits(tool_calls)
    return re_reads + fragmented + blind

@detector("git_check")
def git_check(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect parallel git commands (lock contention risk) and sequential
    batchable git commands that could have been a single call."""
    batchable_subcmds: list[str] = params.as_str_list(
        "batchable_subcmds", ["add", "commit", "status", "diff", "log"]
    )
    batchable_set: set[str] = set(batchable_subcmds)

    results: list[Match] = []

    def _get_cmd(tc: ToolCall) -> str:
        cmd = tc.input_str("command")
        return cmd if isinstance(cmd, str) else ""

    def _git_subcmds(cmd: str) -> list[tuple[str, str]]:
        return [(m.group(1), m.group(0).strip()) for m in GIT_CMD_RE.finditer(cmd)]

    git_tool_calls: list[ToolCall] = []
    for tc in tool_calls:
        if tc.tool_name != "run_terminal_command":
            continue
        cmd = _get_cmd(tc)
        if "git " in cmd:
            git_tool_calls.append(tc)


    bash_calls_by_time: dict[int, list[ToolCall]] = defaultdict(list)
    for tc in git_tool_calls:
        if tc.timestamp:
            bash_calls_by_time[tc.timestamp].append(tc)

    for ts, calls in bash_calls_by_time.items():
        if len(calls) < 2:
            continue
        git_cmds = [_get_cmd(c)[:80] for c in calls]
        any_error = any(c.is_error for c in calls)
        results.append(
            Match(
                tool_calls=calls,
                variables={
                    "sub_type": "parallel",
                    "count": len(calls),
                    "subcmd": "",
                    "commands": "; ".join(git_cmds),
                },
                severity_override=Severity.HIGH if any_error else Severity.MEDIUM,
                summary_override=(
                    f"Parallel git commands ({len(calls)}){' with errors' if any_error else ''}"
                ),
                detail_override=(
                    "Multiple git commands were run in parallel:\n"
                    + "\n".join(f"  - {cmd}" for cmd in git_cmds)
                    + ("\nSome of these commands failed." if any_error else "")
                ),
            )
        )


    runs: list[list[tuple[int, ToolCall, str, str]]] = []
    current_run: list[tuple[int, ToolCall, str, str]] = []

    for i, tc in enumerate(tool_calls):
        if tc.tool_name != "run_terminal_command":
            if tc.tool_name in (
                "search_replace",
                "todo_write",
                "update_goal",
                "read_file",
                "grep",
                "list_dir",
            ):
                if len(current_run) >= 2:
                    runs.append(current_run)
                current_run = []
            continue

        cmd = _get_cmd(tc)
        subcmds = _git_subcmds(cmd)

        if len(subcmds) == 1:
            sub, _ = subcmds[0]
            if sub in batchable_set:
                current_run.append((i, tc, sub, cmd))
                continue


        if len(current_run) >= 2:
            runs.append(current_run)
        current_run = []

    if len(current_run) >= 2:
        runs.append(current_run)

    for run in runs:
        by_sub: dict[str, list[tuple[int, ToolCall, str]]] = defaultdict(list)
        for pos, tc, sub, cmd in run:
            by_sub[sub].append((pos, tc, cmd))

        for sub, entries in by_sub.items():
            if len(entries) < 2:
                continue
            cmds = [cmd[:80] for _, _, cmd in entries]
            tcs = [tc for _, tc, _ in entries]
            any_error = any(tc.is_error for tc in tcs)
            results.append(
                Match(
                    tool_calls=tcs,
                    variables={
                        "sub_type": "batchable",
                        "count": len(entries),
                        "subcmd": sub,
                        "commands": "; ".join(cmds),
                    },
                    severity_override=(Severity.HIGH if any_error else Severity.MEDIUM),
                    summary_override=(
                        f"Sequential `git {sub}` calls ({len(entries)}x) could be batched"
                    ),
                    detail_override=(
                        f"The model ran {len(entries)} separate `git {sub}` commands "
                        f"that could have been a single call:\n"
                        + "\n".join(f"  - {cmd}" for cmd in cmds)
                    ),
                )
            )

    return results
