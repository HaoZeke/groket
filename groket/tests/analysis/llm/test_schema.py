"""Tests for review JSON schema."""

from __future__ import annotations

from groket.analysis.llm.schema import REVIEW_FINDINGS_SCHEMA


def test_schema_requires_findings_summary() -> None:
    assert REVIEW_FINDINGS_SCHEMA["type"] == "object"
    req = REVIEW_FINDINGS_SCHEMA["required"]
    assert "findings" in req
    assert "summary" in req
    assert "all_clear" in req
