"""Host environment checks (Docker, Grok auth, paths) for self-test UI/CLI."""

from __future__ import annotations

from .self_test import CheckResult, SelfTestReport, run_self_test

__all__ = ["CheckResult", "SelfTestReport", "run_self_test"]
