"""Shared compiled regex patterns used by multiple detectors.

Consolidates build/test command patterns, file-write detection,
error patterns, and environment failure detection into one place
to avoid drift between detector modules.
"""

from __future__ import annotations

import re

HEREDOC_WRITE_RE = re.compile(r"(?:cat|tee)\s+>?\s*(\S+)\s*<<")

BUILD_CMD_RE = re.compile(
    r"(?<!\.)(?<!\w)\b(?:make|gcc|g\+\+|cc\s|cmake|configure|meson|cargo\s+build"
    r"|go\s+build|npm\s+run\s+build|tsc|python.*setup\.py)\b",
    re.IGNORECASE,
)

TEST_CMD_RE = re.compile(
    r"\b(?:runtest|pytest|cargo\s+test|go\s+test"
    r"|npm\s+test|make\s+test|jest|mocha"
    r"|phpunit|rspec|python.*-m\s+pytest"
    r"|python.*test|\./test|make\s+check)\b",
    re.IGNORECASE,
)

BUILD_OR_TEST_RE = re.compile(
    f"(?:{BUILD_CMD_RE.pattern})|(?:{TEST_CMD_RE.pattern})",
    re.IGNORECASE,
)

COMPILE_ERROR_RE = re.compile(
    r"|".join(
        [
            r"error:.*undeclared",
            r"error:.*implicit declaration",
            r"error:.*redefinition of",
            r"error:.*conflicting types",
            r"error:.*expected.*before",
            r"error:.*unknown type",
            r"error:.*no member named",
            r"error:.*static declaration.*follows non-static",
            r"error\[E\d+\]",
            r"SyntaxError:",
            r"IndentationError:",
            r"ModuleNotFoundError:",
            r"ImportError:",
            r"NameError:",
            r"TypeError:.*argument",
            r"cannot find module",
            r"is not a function",
            r"compilation failed",
            r"Build FAILED",
        ]
    ),
    re.IGNORECASE,
)

CRASH_RE = re.compile(
    r"|".join(
        [
            r"Segmentation fault",
            r"SIGSEGV",
            r"SIGABRT",
            r"SIGBUS",
            r"core dumped",
            r"Accessing address: 0x",
            r"double free",
            r"heap-use-after-free",
            r"stack-buffer-overflow",
            r"AddressSanitizer",
            r"UndefinedBehaviorSanitizer",
            r"MemorySanitizer",
            r"panic:",
            r"FAILED.*\d+ passed",
            r"FAILURES",
        ]
    ),
    re.IGNORECASE,
)

TEST_FAIL_RE = re.compile(
    CRASH_RE.pattern + r"|"
    r"(?<![0O] )FAIL(?:ED|URE)|AssertionError|assert.*fail|"
    r"expected.*but\s+(?:got|was)|"
    r"test.*[1-9]\d*\s+failed|tests?\s+passed.*[1-9]\d*\s+failed",
    re.IGNORECASE,
)

ENV_FAILURE_RE = re.compile(
    r"command not found|"
    r"No such file or directory.*gcc|"
    r"fatal error:.*No such file or directory|"
    r"gcc.*not found|"
    r"-race requires cgo|"
    r"enable cgo by setting CGO_ENABLED|"
    r"C compiler.*not found|"
    r"ModuleNotFoundError|"
    r"externally-managed-environment|"
    r"pip.*not found|"
    r"node:.*MODULE_NOT_FOUND|"
    r"Cannot find module|"
    r"error: linker .* not found|"
    r"pkg-config.*not found|"
    r"python3?: command not found|"
    r"/usr/bin/bash: line \d+: \S+: command not found",
    re.IGNORECASE,
)


INFRA_ERROR_RE = re.compile(
    r"Author identity unknown|"
    r"tell me who you are|"
    r"git config --global user\.(email|name)|"
    r"Please tell me who you are|"
    r"unable to auto-detect email address|"
    r"Permission denied \(publickey\)|"
    r"Could not resolve host|"
    r"Connection timed out|"
    r"index\.lock.*File exists",
    re.IGNORECASE,
)


VERIFY_SUCCESS_ERROR_RE = re.compile(
    r"call stack too deep|"
    r"maximum recursion|"
    r"RecursionError|"
    r"maximum call stack|"
    r"stack overflow",
    re.IGNORECASE,
)

ERROR_IN_OUTPUT_RE = re.compile(
    r"error(?:\[E\d+\])?:|fatal:|FAILED|panic[!:\s]|Traceback",
    re.IGNORECASE,
)

INSTALL_CMD_RE = re.compile(
    r"\b(?:apt-get\s+install|apt\s+install|yum\s+install"
    r"|dnf\s+install|apk\s+add|pacman\s+-S)\b",
    re.IGNORECASE,
)

TERMINAL_ERROR_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"error(?:\[E\d+\])?:\s"
    r"|fatal(?:\s+error)?:\s"
    r"|FAILED"
    r"|command not found"
    r"|No such file or directory"
    r"|panic[!:\s]"
    r"|Traceback \(most recent"
    r"|SyntaxError:"
    r"|ImportError:"
    r"|ModuleNotFoundError:"
    r"|Segmentation fault"
    r"|core dumped"
    r"|externally-managed-environment"
    r"|tests?\s+failed|test.*FAIL"
    r")",
    re.IGNORECASE,
)

SIGNAL_KILL_RE = re.compile(
    r"exit:\s*killed\s*\(signal\s*\d+\)|signal:\s*\d+|Killed$",
    re.IGNORECASE,
)

SERVER_CMD_RE = re.compile(
    r"(?:redis-server|nginx|node\s+server|flask\s+run|uvicorn|gunicorn"
    r"|python.*serve|java\s+-jar|pkill|kill\s+"
    r"|redis-benchmark|--daemonize|--daemon)",
    re.IGNORECASE,
)

GIT_CMD_RE = re.compile(r"(?:^|&&|;|\|)\s*git\s+(\S+)")

INLINE_SCRIPT_RE = re.compile(
    r"(?:python3?)\s+(?:\S+\s+)*-c\s"
    r"|(?:node|perl|ruby)\s+(?:\S+\s+)*-[eE]\s",
    re.IGNORECASE,
)


HEREDOC_SCRIPT_RE = re.compile(
    r"(?:^|&&|;|\|)\s*(?:python3?|node|bash|sh|perl|ruby|php)\s*<<",
    re.IGNORECASE | re.MULTILINE,
)


EXPLORATORY_CMD_RE = re.compile(
    r"\bgit\s+(?:log|status|branch|stash|show|diff|remote|tag)\b"
    r"|\b(?:ls|find|which|type|uname|pwd)\b",
    re.IGNORECASE,
)

BUILD_PROGRESS_RE = re.compile(
    r"^\s*(?:Building|Compiling|Downloading|Installing|Fetching)\b"
    r"|Building\s+\[=*>",
    re.IGNORECASE | re.MULTILINE,
)

def is_build_progress_only(output: str) -> bool:
    """True when output looks like truncated mid-build progress without diagnostics."""
    if not output:
        return False
    has_progress = bool(BUILD_PROGRESS_RE.search(output))
    has_diag = bool(
        re.search(
            r"error(?:\[E\d+\])?:\s|FAILED|panic!|Traceback \(most recent",
            output,
            re.IGNORECASE,
        )
    )
    return has_progress and not has_diag

def is_truncated_only_output(output: str) -> bool:
    """True when the stored result is only a truncation/metadata wrapper.

    Grok traces sometimes store just:
      exit: 101 [truncated: showing first/last 20.6KB of 32.9KB - full output at: ...]
    with no actual stderr/stdout body.  That is not actionable evidence of
    a compile/test failure for our rules.
    """
    if not output:
        return False

    body = re.sub(r"^exit:\s*\S+\s*", "", output.strip(), count=1)
    body = body.strip()
    if not body:
        return True
    if re.match(
        r"^\[truncated:[^\]]*\]\s*(?:full output at:)?\s*\S*\s*$",
        body,
        re.IGNORECASE | re.DOTALL,
    ):
        return True

    if (
        "[truncated:" in body
        and len(body) < 250
        and not re.search(r"error(?:\[E\d+\])?:\s|FAILED|panic!", body, re.IGNORECASE)
    ):
        return True
    return False

def is_successful_verification(
    cmd: str, output: str, *, is_error: bool = False, exit_code: int | None = None
) -> bool:
    """True when a terminal command looks like a successful test/build verify step.

    Used to suppress false_completion / premature_completion when the model
    *did* get a green verify somewhere before declaring done — even if an
    earlier or later full-suite run still shows unrelated failures.
    """
    if not cmd or not BUILD_OR_TEST_RE.search(cmd):
        return False
    if is_error:
        return False
    if exit_code is not None and exit_code != 0:
        return False
    if not output or is_truncated_only_output(output):
        return False

    tail = output[-2500:]


    if re.search(
        r"(?:^|\n)\s*[1-9]\d*\s+failed(?:,|\s|$)"
        r"|(?:^|\n)\s*FAILED\s+\S+",
        tail,
        re.IGNORECASE,
    ):
        return False
    if re.search(
        r"error(?:\[E\d+\])?:\s|Build FAILED|could not compile|panicked at"
        r"|Interrupted: \d+ error",
        tail,
        re.IGNORECASE,
    ):
        return False


    if re.search(
        r"\d+\s+passed(?:,|\s|$)"
        r"|\ball\s+tests?\s+passed\b"
        r"|\b\d+\s+tests?\s+ok\b"
        r"|Build succeeded|Finished.*release|Finished.*dev",
        tail,
        re.IGNORECASE,
    ):
        return True


    if re.search(r"\bmake\b|\bcargo\s+build\b", cmd, re.IGNORECASE):
        if output.startswith("exit: 0") or exit_code == 0:
            return True
    return False

def tool_call_is_successful_verification(tc) -> bool:
    """Wrapper over is_successful_verification for ToolCall objects."""
    if getattr(tc, "tool_name", None) != "run_terminal_command":
        return False
    cmd = (tc.raw_input or {}).get("command", "")
    output = tc.result_content or ""
    return is_successful_verification(
        cmd,
        output,
        is_error=bool(tc.is_error),
        exit_code=tc.exit_code,
    )

def had_successful_verification_before(tool_calls: list, end_idx: int) -> bool:
    """True if any run_terminal_command before end_idx is a green verify."""
    for j in range(max(0, end_idx)):
        if tool_call_is_successful_verification(tool_calls[j]):
            return True
    return False

TEMP_PREFIXES = ("/tmp/", "/var/tmp/")

def is_env_failure(output: str) -> bool:
    """Check if test/build output indicates an environment issue, not a code bug."""
    return bool(ENV_FAILURE_RE.search(output[:1000]))

def is_infra_error(output: str) -> bool:
    """Check if output is infra/tooling noise (git author, locks), not task failure."""
    return bool(INFRA_ERROR_RE.search(output[:1500]))

def is_verify_expected_error(cmd: str, output: str) -> bool:
    """True when the model is verifying a depth/recursion fix and the error is expected."""
    if not VERIFY_SUCCESS_ERROR_RE.search(output[:2000]):
        return False
    return bool(
        re.search(
            r"\bjq\b|python3?\s+-c|node\s+-e|recursion|nested|depth",
            cmd,
            re.IGNORECASE,
        )
    )
