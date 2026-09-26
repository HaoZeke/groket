"""Security scanner — flags potential secrets and risky operations in traces.

Scans tool call arguments and results for patterns that indicate leaked
credentials, force-pushes, destructive git operations, external downloads,
and unpinned dependency installs.

Config:
    "plugins": ["security_scanner:SecurityScannerAnalyzer"]
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import Severity
from groket.parser import parse_timeline, parse_tool_calls

logger = logging.getLogger(__name__)

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S{20,}")),
    ("GitHub Token", re.compile(r"gh[ps]_[A-Za-z0-9_]{36,}")),
    ("Generic API Key", re.compile(r"(?i)(api[_-]?key|secret[_-]?key)\s*[=:]\s*['\"]?\S{16,}")),
    ("Private Key Block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}")),
]


RISKY_COMMAND_PATTERNS: list[tuple[str, Severity, re.Pattern[str]]] = [
    ("Force push", Severity.HIGH, re.compile(r"git\s+push\s+.*--force")),
    ("Hard reset", Severity.MEDIUM, re.compile(r"git\s+reset\s+--hard")),
    ("Recursive delete", Severity.MEDIUM, re.compile(r"rm\s+-r")),
    ("curl / wget download", Severity.LOW, re.compile(r"(curl|wget)\s+")),
    ("pip install (unpinned)", Severity.LOW, re.compile(r"pip\s+install\s+(?!.*==)")),
    ("chmod 777", Severity.MEDIUM, re.compile(r"chmod\s+777")),
    ("curl pipe to shell", Severity.HIGH, re.compile(r"curl\s+.*\|\s*(bash|sh|zsh)")),
    ("Identity override", Severity.LOW, re.compile(r"git\s+config\s+user\.(name|email)\s+")),
]

class SecurityScannerAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="security-scanner",
            name="Security Scanner",
            description=(
                "Detects leaked secrets, force-pushes, destructive "
                "commands, and risky operations in traces"
            ),
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        tool_calls = parse_tool_calls(session_dir)
        timeline = parse_timeline(session_dir)

        if not tool_calls and not timeline:
            return AnalysisResult(
                session_id=session_dir.name,
                analyzer_id="security-scanner",
                ok=True,
                summary="No trace data to scan",
            )

        findings: list[Finding] = []

        for tc in tool_calls:
            if tc.tool_name == "run_terminal_command":
                cmd = tc.raw_input.get("command", "")
                for label, severity, pattern in RISKY_COMMAND_PATTERNS:
                    if pattern.search(cmd):
                        findings.append(Finding(
                            id=f"risky-{tc.call_id[:12]}",
                            plugin_id="security-scanner",
                            severity=severity,
                            title=f"{label}",
                            detail=f"Command: {cmd[:200]}",
                            category="Risky Command",
                            tool_call_ids=[tc.call_id],
                        ))

            chunks = [str(v) for v in tc.raw_input.values()]
            if tc.result_content:
                chunks.append(tc.result_content)
            for chunk in chunks:
                for label, pattern in SECRET_PATTERNS:
                    for match in pattern.finditer(chunk):
                        findings.append(Finding(
                            id=f"secret-{tc.call_id[:12]}",
                            plugin_id="security-scanner",
                            severity=Severity.HIGH,
                            title=f"Potential {label} detected",
                            detail=f"In {tc.tool_name}: {_redact(match.group())}",
                            category="Leaked Secret",
                            tool_call_ids=[tc.call_id],
                        ))

        for ev in timeline:
            if ev.event_type not in ("assistant", "user", "thought"):
                continue
            text = ev.content if isinstance(ev.content, str) else str(ev.content)
            for label, pattern in SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    findings.append(Finding(
                        id=f"secret-msg-{ev.index}",
                        plugin_id="security-scanner",
                        severity=Severity.HIGH,
                        title=f"Potential {label} in {ev.event_type} message",
                        detail=_redact(match.group()),
                        category="Leaked Secret",
                    ))

        high = sum(1 for f in findings if f.severity == Severity.HIGH)
        medium = sum(1 for f in findings if f.severity == Severity.MEDIUM)
        low = sum(1 for f in findings if f.severity == Severity.LOW)
        if findings:
            summary = f"{len(findings)} security finding(s): {high} high, {medium} medium, {low} low"
        else:
            summary = "Clean — no secrets or risky commands detected"

        return AnalysisResult(
            session_id=session_dir.name,
            analyzer_id="security-scanner",
            ok=True,
            findings=findings,
            summary=summary,
        )

def _redact(value: str, keep: int = 6) -> str:
    if len(value) <= keep:
        return value
    return value[:keep] + "***REDACTED***"

