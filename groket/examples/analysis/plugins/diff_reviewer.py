"""Diff reviewer — analyzes code changes made during a session.

Parses tool calls that write files (search_replace, etc.) and checks for
file churn (same file edited many times), large single edits, and source
files changed without corresponding test updates.

Config:
    "plugins": ["diff_reviewer:DiffReviewerAnalyzer"]
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import Severity
from groket.parser import parse_tool_calls

logger = logging.getLogger(__name__)

WRITE_TOOL_NAMES = {
    "search_replace", "write_file", "create_file", "edit_file", "str_replace_editor",
}
TEST_FILE_PATTERN_PARTS = ("test_", "_test.", ".test.", ".spec.", "tests/", "__tests__/")
NON_SOURCE_EXTENSIONS = (".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".csv")

CHURN_THRESHOLD = 5
LARGE_EDIT_CHARS = 2000

def _is_test_path(path: str) -> bool:
    return any(part in path for part in TEST_FILE_PATTERN_PARTS)

class DiffReviewerAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="diff-reviewer",
            name="Diff Reviewer",
            description="Detects file churn, large edits, and missing test updates",
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        tool_calls = parse_tool_calls(session_dir)

        if not tool_calls:
            return AnalysisResult(
                session_id=session_dir.name,
                analyzer_id="diff-reviewer",
                ok=True,
                summary="No tool calls to review",
            )

        file_edit_counts: Counter[str] = Counter()
        changed_files: set[str] = set()
        test_files_touched: set[str] = set()
        findings: list[Finding] = []
        large_edit_count = 0

        for tc in tool_calls:
            if tc.tool_name not in WRITE_TOOL_NAMES:
                continue

            ri = tc.raw_input
            file_path = (
                ri.get("file_path") or ri.get("path")
                or ri.get("target_file") or ""
            )
            new_content = ri.get("new_string") or ri.get("content") or ""

            if file_path:
                changed_files.add(file_path)
                file_edit_counts[file_path] += 1
                if _is_test_path(file_path):
                    test_files_touched.add(file_path)

            if len(new_content) >= LARGE_EDIT_CHARS:
                large_edit_count += 1
                findings.append(Finding(
                    id=f"large-edit-{tc.call_id[:12]}",
                    plugin_id="diff-reviewer",
                    severity=Severity.LOW,
                    title=f"Large edit ({len(new_content):,} chars)",
                    detail=f"{file_path}",
                    category="Large Edit",
                    tool_call_ids=[tc.call_id],
                ))


        for path, count in file_edit_counts.most_common():
            if count < CHURN_THRESHOLD:
                break
            findings.append(Finding(
                id=f"churn-{path.rsplit('/', 1)[-1]}",
                plugin_id="diff-reviewer",
                severity=Severity.MEDIUM,
                title=f"File churn: {path.rsplit('/', 1)[-1]} edited {count}x",
                detail=path,
                category="File Churn",
            ))


        source_files = {
            f for f in changed_files
            if not _is_test_path(f)
            and not f.endswith(NON_SOURCE_EXTENSIONS)
        }
        if source_files and not test_files_touched:
            findings.append(Finding(
                id="missing-tests",
                plugin_id="diff-reviewer",
                severity=Severity.MEDIUM,
                title=f"{len(source_files)} source file(s) changed with no test updates",
                detail="Files: " + ", ".join(
                    f.rsplit("/", 1)[-1] for f in sorted(source_files)[:5]
                ),
                category="Missing Tests",
            ))

        summary = (
            f"{len(changed_files)} file(s) changed, "
            f"{len(test_files_touched)} test file(s), "
            f"{large_edit_count} large edit(s), "
            f"{len(findings)} finding(s)"
        )

        return AnalysisResult(
            session_id=session_dir.name,
            analyzer_id="diff-reviewer",
            ok=True,
            findings=findings,
            summary=summary,
        )

