"""Context-awareness detectors — detect failures to adapt to the codebase,
environment, or task at hand."""

from __future__ import annotations

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
    json_as_bool,
    json_as_str,
)

_DEFAULT_LANG_PATTERNS: dict[str, list[str]] = {
    "typescript": [
        r"Promise<",
        r"export\s+type\s",
        r"export\s+interface\s",
        r"import\s+\{",
        r"readonly\s+\w+:",
        r"keyof\s+",
        r"as\s+const\b",
        r"Partial<\w",
        r"Record<\w",
    ],
    "javascript": [
        r"require\(['\"]",
        r"module\.exports",
        r"\.then\(\s*(?:function|=>)",
        r"document\.\w+",
        r"window\.\w+",
        r"console\.log\(",
        r"export\s+default\s",
    ],
    "python": [
        r"__import__\(",
        r"subprocess\.\w+\(",
        r"def\s+\w+\(self[,\)]",
        r"__init__\.\w+",
        r"__name__\s*==",
    ],
    "sql": [
        r"\bSELECT\s+\w+\s+FROM\b",
        r"\bINSERT\s+INTO\b",
        r"\bDROP\s+TABLE\b",
        r"\bCREATE\s+TABLE\b",
    ],
}

_DEFAULT_LANG_GLOBS: dict[str, list[str]] = {
    "typescript": [".ts", ".tsx"],
    "javascript": [".js", ".jsx", ".mjs"],
    "python": [".py"],
    "ruby": [".rb"],
    "php": [".php"],
    "java": [".java"],
    "go": [".go"],
    "c": [".c", ".h"],
    "cpp": [".cpp", ".cc", ".cxx", ".hpp"],
    "rust": [".rs"],
}


_TYPE_LANG_MAP: dict[str, str] = {
    "ts": "typescript",
    "js": "javascript",
    "py": "python",
    "rb": "ruby",
    "java": "java",
    "go": "go",
    "rust": "rust",
    "c": "c",
    "cpp": "cpp",
}


_DEFAULT_SHELL_PATTERNS: list[JsonObject] = [
    {"pattern": r"\bgrep\s+", "alternative": "grep tool", "primary_only": True},
    {"pattern": r"\brg\s+", "alternative": "grep tool", "primary_only": True},
    {"pattern": r"\bfind\s+\.\s", "alternative": "list_dir or grep --glob", "primary_only": True},
    {"pattern": r"\bfind\s+/", "alternative": "list_dir or grep --glob", "primary_only": True},
    {"pattern": r"\bcat\s+", "alternative": "read_file", "primary_only": True},
    {"pattern": r"\bhead\s+", "alternative": "read_file with limit", "primary_only": True},
    {"pattern": r"\btail\s+", "alternative": "read_file with offset", "primary_only": True},
    {"pattern": r"\bls\s+", "alternative": "list_dir", "primary_only": True},
    {
        "pattern": r"\bsed\s+-n\s",
        "alternative": "read_file with offset/limit",
        "primary_only": True,
    },
    {"pattern": r"\bawk\s+", "alternative": "grep or read_file", "primary_only": True},
    {"pattern": r"\btree\s+", "alternative": "list_dir", "primary_only": True},
]


_DEFAULT_ENV_ERROR_PATTERNS: list[dict[str, str]] = [
    {"pattern": r"unrecognized option", "description": "Unrecognized flag (likely BusyBox/Alpine)"},
    {"pattern": r"invalid option", "description": "Invalid option (environment mismatch)"},
    {"pattern": r"unknown option", "description": "Unknown option (environment mismatch)"},
    {"pattern": r"command not found", "description": "Missing binary"},
    {"pattern": r"No such file or directory.*bin/", "description": "Missing binary"},
    {"pattern": r"not found$", "description": "Missing command"},
    {"pattern": r"applet not found", "description": "BusyBox applet not available"},
]


_DEFAULT_GENERIC_PATTERNS = (
    r"(?:eval|exec|system|sql|inject|xss|csrf|deserializ|pickle|yaml\.load|shell_exec"
    r"|TODO|FIXME|HACK|XXX|BUG|DEPRECATED)"
)
_DEFAULT_SHOTGUN_PATTERNS = r"^(?:error|bug|fix|issue|problem|crash|fail|broken)$"
_DEFAULT_GENERIC_RE = re.compile(_DEFAULT_GENERIC_PATTERNS, re.IGNORECASE)
_DEFAULT_SHOTGUN_RE = re.compile(_DEFAULT_SHOTGUN_PATTERNS, re.IGNORECASE)
_MULTI_LANG_GLOB_RE = re.compile(r"\*\.\{.*,.*\}")



def _is_primary_command(cmd: str, pat: str) -> bool:
    """Check if the matched pattern is the primary command,
    not something piped from another command or a file write."""

    stripped = re.sub(r'^(?:cd\s+\S+\s*&&\s*|bash\s+-c\s+["\']?)', "", cmd.strip())


    m = re.search(pat, stripped)
    if not m:
        return False


    before = stripped[: m.start()]
    if "|" in before:
        return False


    after = stripped[m.end() :]

    next_chain = len(after)
    for sep in ["&&", ";"]:
        pos = after.find(sep)
        if pos != -1 and pos < next_chain:
            next_chain = pos
    segment_after = after[:next_chain]
    pipe_m = re.search(r"\|\s*(\w+)", segment_after)
    if pipe_m:
        pipe_target = pipe_m.group(1)
        if pipe_target in ("cat", "less", "more", "tee", "wc", "sort", "uniq", "tr", "column"):
            return False
        if "find" in pat:
            return False


    for sep in ["&&", ";"]:
        if sep in before:
            pre_chain = before.split(sep)[0].strip()
            if pre_chain and not pre_chain.startswith("cd "):
                return False


    if "cat" in pat:
        if re.search(r">\s*\S+", after[:40]) or "<<" in after[:40]:
            return False


    if "find" in pat:
        full_cmd = before + stripped[m.start() :]
        if any(kw in full_cmd for kw in ["-exec", "xargs", " -o ", "-newer", "-mtime"]):
            return False

    return True

@detector("wrong_language_search")
def wrong_language_search(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect grep patterns that use language-specific constructs irrelevant
    to the actual codebase (e.g., searching for TypeScript in a C repo)."""
    lang_globs: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_LANG_GLOBS.items()}
    if params.has("lang_globs"):
        raw_g = params.get("lang_globs")
        if isinstance(raw_g, dict):
            lang_globs = {
                str(k): [str(x) for x in (v if isinstance(v, list) else [])]
                for k, v in raw_g.items()
            }
    lang_patterns: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_LANG_PATTERNS.items()}
    if params.has("lang_patterns"):
        raw_p = params.get("lang_patterns")
        if isinstance(raw_p, dict):
            lang_patterns = {
                str(k): [str(x) for x in (v if isinstance(v, list) else [])]
                for k, v in raw_p.items()
            }

    results: list[Match] = []


    extensions: dict[str, int] = defaultdict(int)
    for tc in tool_calls:
        if tc.tool_name == "list_dir" and tc.result_content:
            for ext in re.findall(r"\.([a-zA-Z0-9]+)", tc.result_content):
                extensions[f".{ext.lower()}"] += 1
        elif tc.tool_name == "read_file":
            f = tc.input_str("target_file")
            if "." in f:
                ext = "." + f.rsplit(".", 1)[-1].lower()
                extensions[ext] += 1
        elif tc.tool_name in ("search_replace", "grep"):
            f = tc.input_str("file_path") or tc.input_str("path")
            if f and "." in f:
                ext = "." + f.rsplit(".", 1)[-1].lower()
                extensions[ext] += 1

    if not extensions:
        return results


    present_langs: set[str] = set()
    for lang, exts in lang_globs.items():
        if any(extensions.get(e, 0) > 0 for e in exts):
            present_langs.add(lang)

    for tc in tool_calls:
        if tc.tool_name != "grep":
            continue

        pattern = tc.input_str("pattern")
        glob = tc.input_str("glob") or ""
        type_filter = tc.input_str("type") or ""


        if type_filter and type_filter in _TYPE_LANG_MAP:
            target_lang = _TYPE_LANG_MAP[type_filter]
            if target_lang not in present_langs:
                results.append(
                    Match(
                        tool_calls=[tc],
                        variables={
                            "target_lang": target_lang,
                            "present_langs": ", ".join(sorted(present_langs)),
                            "pattern": pattern[:80],
                        },
                        summary_override=(
                            f"Grep --type={type_filter} but no {target_lang} files exist"
                        ),
                        detail_override=(
                            f"Grep used --type={type_filter} ({target_lang}) but the codebase "
                            f"has no {target_lang} files.\n"
                            f"  Pattern: {pattern[:80]}\n"
                            f"  Present languages: {sorted(present_langs)}\n"
                            f"  Present extensions: {dict(list(extensions.items())[:10])}"
                        ),
                    )
                )
                continue


        absent_in_glob: list[str] = []
        for lang, exts in lang_globs.items():
            if lang in present_langs:
                continue
            for ext in exts:
                if ext in glob:
                    absent_in_glob.append(f"{ext} ({lang})")

        if absent_in_glob:
            results.append(
                Match(
                    tool_calls=[tc],
                    variables={
                        "target_lang": absent_in_glob[0].split("(")[-1].rstrip(")"),
                        "present_langs": ", ".join(sorted(present_langs)),
                        "pattern": pattern[:80],
                    },
                    summary_override=(
                        f"Grep glob targets absent language: {', '.join(absent_in_glob[:4])}"
                    ),
                    detail_override=(
                        f"Grep with glob '{glob}' targets file types not present in this codebase.\n"
                        f"  Pattern: {pattern[:80]}\n"
                        f"  Absent: {', '.join(absent_in_glob)}\n"
                        f"  Present extensions: {dict(list(extensions.items())[:10])}"
                    ),
                )
            )
            continue


        for lang, lp_list in lang_patterns.items():
            if lang in present_langs:
                continue
            matches_found: list[str] = []
            for p in lp_list:
                try:
                    if re.search(p, pattern):
                        matches_found.append(p)
                except re.error:
                    if p in pattern:
                        matches_found.append(p)
            if len(matches_found) >= 2:
                results.append(
                    Match(
                        tool_calls=[tc],
                        variables={
                            "target_lang": lang,
                            "present_langs": ", ".join(sorted(present_langs)),
                            "pattern": pattern[:80],
                        },
                        summary_override=(f"Grep uses {lang} pattern on non-{lang} codebase"),
                        detail_override=(
                            f"Grep pattern contains {lang}-specific construct(s) but no "
                            f"{lang} files exist in the workspace.\n"
                            f"  Pattern: {pattern[:80]}\n"
                            f"  Matched constructs: {matches_found[:5]}\n"
                            f"  Present languages: {sorted(present_langs)}"
                        ),
                    )
                )
                break

    return results

@detector("terminal_native_check")
def terminal_native_check(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect shell commands that duplicate what grep, read_file, or list_dir do."""
    shell_patterns: list[JsonObject] = list(_DEFAULT_SHELL_PATTERNS)
    if params.has("shell_patterns"):
        raw_sp = params.get("shell_patterns")
        if isinstance(raw_sp, list):
            shell_patterns = [dict(x) for x in raw_sp if isinstance(x, dict)]

    results: list[Match] = []

    for tc in tool_calls:
        if tc.tool_name != "run_terminal_command":
            continue
        cmd = tc.input_str("command")


        if re.match(r"^\s*(?:perl|python[23]?|ruby|node)\s+(?:-[ec]|-m\s)", cmd):
            continue

        if re.match(r"^\s*echo\s", cmd) and ">" not in cmd:
            continue

        if "<<" in cmd and re.search(r"(?:cat|tee)\s+>?\s*\S+\s*<<", cmd):
            continue

        if re.match(r"^\s*(?:cd\s+\S+\s*&&\s*)?git\s+", cmd):
            continue

        chain_count = cmd.count("&&") + cmd.count(";") + 1
        if chain_count >= 3:
            continue

        if re.search(r"\|\s*cat\s*$", cmd.strip()):
            continue

        for sp in shell_patterns:
            pat = json_as_str(sp.get("pattern"), "")
            alternative = json_as_str(sp.get("alternative"), "native tool")
            primary_only = json_as_bool(sp.get("primary_only"), True)

            if not pat:
                continue
            if not re.search(pat, cmd):
                continue
            if primary_only and not _is_primary_command(cmd, pat):
                continue

            results.append(
                Match(
                    tool_calls=[tc],
                    variables={
                        "command": cmd[:120],
                        "alternative": alternative,
                    },
                    summary_override=(f"Shell command duplicates native tool: use {alternative}"),
                    detail_override=(
                        f"run_terminal_command used for an operation the native {alternative} "
                        f"handles better.\n"
                        f"  Command: {cmd[:120]}\n"
                        f"Native tools are faster, don't depend on the container environment, "
                        f"and avoid shell compatibility issues."
                    ),
                )
            )
            break

    return results

_CMD_NOT_FOUND_RE = re.compile(r"(\S+):\s*(?:command\s+)?not found", re.IGNORECASE)
_NO_SUCH_FILE_RE = re.compile(r"(\S+):\s*No such file", re.IGNORECASE)
_CHAIN_SPLIT_RE = re.compile(r"\s*(?:&&|;)\s*")

def _extract_missing_binary(output: str, cmd: str) -> str | None:
    """Extract the missing binary name from 'command not found' output.
    Returns None if no actual not-found error, or if the binary is only
    in a later segment of a compound command."""
    m = _CMD_NOT_FOUND_RE.search(output) or _NO_SUCH_FILE_RE.search(output)
    if not m:
        return None
    binary = m.group(1).split("/")[-1]

    segments = _CHAIN_SPLIT_RE.split(cmd)
    if len(segments) > 1:
        first_seg_bins = set(re.findall(r"(?:^|\s)(\S+)", segments[0]))
        if binary not in first_seg_bins:
            return None
    return binary

def _missing_binary_match(
    tc: ToolCall,
    binary: str,
    cmd: str,
    output: str,
    desc: str,
) -> Match:
    return Match(
        tool_calls=[tc],
        variables={"command": cmd[:120], "error_desc": desc, "binary": binary},
        severity_override=Severity.HIGH,
        summary_override=f"Retried missing binary: {binary}",
        detail_override=(
            f"The model tried `{binary}` again after it was already not found.\n"
            f"  Command: {cmd[:120]}\n"
            f"  Error: {output[:200]}\n"
            f"After discovering a binary is missing, the model should "
            f"adapt (install it, or use an alternative)."
        ),
    )

def _option_error_match(
    tc: ToolCall,
    cmd: str,
    output: str,
    desc: str,
) -> Match:
    return Match(
        tool_calls=[tc],
        variables={"command": cmd[:120], "error_desc": desc, "binary": ""},
        severity_override=Severity.HIGH,
        summary_override=f"Command failed — {desc}",
        detail_override=(
            f"A terminal command failed due to an environment incompatibility.\n"
            f"  Command: {cmd[:120]}\n"
            f"  Error: {output[:200]}\n"
            f"This typically happens when GNU-specific flags are used in a "
            f"BusyBox/Alpine container."
        ),
    )

@detector("terminal_env_check")
def terminal_env_check(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect shell commands that failed because of environment differences
    (e.g., GNU-only flags on BusyBox/Alpine).  Only flag 'command not found'
    if the model retries the same missing binary."""
    env_error_patterns: list[dict[str, str]] = list(_DEFAULT_ENV_ERROR_PATTERNS)
    if params.has("env_error_patterns"):
        raw_ep = params.get("env_error_patterns")
        if isinstance(raw_ep, list):
            env_error_patterns = [
                {str(k): str(v) for k, v in item.items()}
                for item in raw_ep
                if isinstance(item, dict)
            ]
    results: list[Match] = []
    seen_missing: dict[str, int] = {}
    grace_window: int = params.as_int("grace_window", 2)

    for i, tc in enumerate(tool_calls):
        if tc.tool_name != "run_terminal_command" or not tc.result_content:
            continue

        output = tc.result_content
        cmd = tc.input_str("command")

        for ep in env_error_patterns:
            pat = ep.get("pattern", "")
            desc = ep.get("description", "")
            if not pat or not re.search(pat, output, re.IGNORECASE | re.MULTILINE):
                continue

            if "not found" in pat.lower() or "Missing" in desc:
                binary = _extract_missing_binary(output, cmd)
                if not binary:
                    break
                if binary in seen_missing:
                    if i - seen_missing[binary] <= grace_window:
                        break
                    results.append(_missing_binary_match(tc, binary, cmd, output, desc))
                else:
                    seen_missing[binary] = i
                break
            else:
                if not tc.is_error:
                    break
                results.append(_option_error_match(tc, cmd, output, desc))
                break

    return results

@detector("overlapping_reads")
def overlapping_reads(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect multiple read_file calls on the same file with overlapping offset
    ranges, excluding re-reads that are legitimate (after editing the file)."""
    min_reads: int = params.as_int("min_reads", 3)
    overlap_threshold: float = params.as_float("overlap_threshold", 0.3)

    results: list[Match] = []


    reads_by_file: dict[str, list[tuple[int, int, ToolCall, int]]] = defaultdict(list)

    edit_indices_by_file: dict[str, set[int]] = defaultdict(set)

    for idx, tc in enumerate(tool_calls):
        if tc.tool_name == "read_file":
            f = tc.input_str("target_file")
            if not f:
                continue
            offset = tc.inputs().as_int("offset", 0)

            limit = tc.inputs().as_int_opt("limit")
            if limit is None:
                limit = 200
            reads_by_file[f].append((offset, limit, tc, idx))
        elif tc.tool_name == "search_replace" and not tc.is_error:
            f = tc.input_str("file_path")
            if f:
                edit_indices_by_file[f].add(idx)

    for f, reads in reads_by_file.items():
        if len(reads) < min_reads:
            continue

        edit_indices = edit_indices_by_file.get(f, set())

        overlaps: list[tuple[ToolCall, ToolCall, int]] = []
        for i in range(len(reads)):
            for j in range(i + 1, len(reads)):
                start_a, limit_a, tc_a, idx_a = reads[i]
                start_b, limit_b, tc_b, idx_b = reads[j]


                if any(idx_a < eidx < idx_b or idx_b < eidx < idx_a for eidx in edit_indices):
                    continue

                end_a = start_a + limit_a
                end_b = start_b + limit_b

                overlap_start = max(start_a, start_b)
                overlap_end = min(end_a, end_b)
                overlap = max(0, overlap_end - overlap_start)

                smaller = min(limit_a, limit_b)
                if smaller > 0 and overlap / smaller > overlap_threshold:
                    overlaps.append((tc_a, tc_b, overlap))

        if len(overlaps) >= 2:
            seen_tcs: list[ToolCall] = []
            seen_ids: set[str] = set()
            for tc_a, tc_b, _ in overlaps:
                if tc_a.call_id not in seen_ids:
                    seen_ids.add(tc_a.call_id)
                    seen_tcs.append(tc_a)
                if tc_b.call_id not in seen_ids:
                    seen_ids.add(tc_b.call_id)
                    seen_tcs.append(tc_b)

            ranges = ", ".join(f"{r[0]}-{r[0] + r[1]}" for r in reads[:5])
            results.append(
                Match(
                    tool_calls=seen_tcs[:10],
                    variables={
                        "file": f,
                        "read_count": len(reads),
                        "overlap_count": len(overlaps),
                    },
                    summary_override=(
                        f"Overlapping reads on {f.split('/')[-1]} ({len(overlaps)} overlaps)"
                    ),
                    detail_override=(
                        f"File '{f}' was read {len(reads)} times with overlapping ranges.\n"
                        f"  Ranges: {ranges}\n"
                        f"  {len(overlaps)} pairs overlap by >{overlap_threshold:.0%} "
                        f"(excluding post-edit re-reads).\n"
                        f"A single wider read or targeted grep would be more efficient."
                    ),
                )
            )

    return results

@detector("premature_generic_search")
def premature_generic_search(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Detect grep calls with generic or multi-language patterns issued
    before the model has meaningfully explored the project structure."""
    orientation_reads: int = params.as_int("orientation_reads", 2)
    generic_patterns_str: str = params.as_str("generic_patterns", _DEFAULT_GENERIC_PATTERNS)
    shotgun_patterns_str: str = params.as_str("shotgun_patterns", _DEFAULT_SHOTGUN_PATTERNS)

    results: list[Match] = []


    generic_re = (
        re.compile(generic_patterns_str, re.IGNORECASE)
        if generic_patterns_str != _DEFAULT_GENERIC_PATTERNS
        else _DEFAULT_GENERIC_RE
    )
    shotgun_re = (
        re.compile(shotgun_patterns_str, re.IGNORECASE)
        if shotgun_patterns_str != _DEFAULT_SHOTGUN_PATTERNS
        else _DEFAULT_SHOTGUN_RE
    )


    files_read = 0
    oriented_idx: int | None = None
    for i, tc in enumerate(tool_calls):
        if tc.tool_name == "read_file":
            files_read += 1
        if files_read >= orientation_reads:
            oriented_idx = i
            break

    for i, tc in enumerate(tool_calls):
        if tc.tool_name != "grep":
            continue


        if oriented_idx is not None and i > oriented_idx:
            continue

        glob = tc.input_str("glob") or ""
        pattern = tc.input_str("pattern")
        path = tc.input_str("path") or ""

        is_multi_lang_glob = bool(_MULTI_LANG_GLOB_RE.search(glob))
        is_generic_pattern = bool(generic_re.search(pattern))
        is_shotgun = bool(shotgun_re.match(pattern.strip()))
        is_broad_path = path in ("", ".", "./", "/")

        if (
            is_multi_lang_glob
            or (is_generic_pattern and is_broad_path)
            or (is_shotgun and is_broad_path)
        ):
            reason = "multi-language glob" if is_multi_lang_glob else "generic pattern"
            results.append(
                Match(
                    tool_calls=[tc],
                    variables={
                        "reason": reason,
                        "pattern": pattern[:80],
                        "glob": glob or "(none)",
                    },
                    summary_override=(f"Premature {reason} before exploring project structure"),
                    detail_override=(
                        f"A grep with a {reason} was issued before the model explored "
                        f"the project structure (read at least {orientation_reads} files).\n"
                        f"  Pattern: {pattern[:80]}\n"
                        f"  Glob: {glob or '(none)'}\n"
                        f"  Path: {path or '(root)'}\n"
                        f"The model should first understand what the project is before "
                        f"doing broad searches."
                    ),
                )
            )

    return results
