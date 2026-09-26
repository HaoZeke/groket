"""Fluent policy: composed messages exist; check_fluent hard rules pass."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from groket.ui.i18n import setup_i18n, t

ROOT = Path(__file__).resolve().parents[2]


def test_composed_tool_messages_format() -> None:
    setup_i18n("en")
    assert "grep" in t("tool-detail-heading", index=5, name="grep")
    assert "Output (12 chars)" == t("tool-output-rule", n=12)
    assert "cleaned from" in t("tool-output-rule-cleaned", n=3, raw=10)
    assert "context7" in t("tool-mcp-label", name="context7__x")


def test_notify_messages() -> None:
    setup_i18n("en")
    assert "/tmp" in t("notify-scanning", path="/tmp")
    assert "3" in t("notify-loaded-sessions", n=3)
    assert "2" in t("notify-analyzing", n=2, plugins=1)


def test_check_fluent_script_exits_zero() -> None:
    script = ROOT / "scripts" / "check_fluent.py"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
