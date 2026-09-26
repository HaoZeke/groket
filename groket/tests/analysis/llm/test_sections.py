"""Tests for review section constants."""

from __future__ import annotations

from groket.analysis.llm.sections import DEFAULT_SECTIONS, ReviewSection


def test_default_sections_include_core_blocks() -> None:
    assert ReviewSection.INSTRUCTIONS in DEFAULT_SECTIONS
    assert ReviewSection.TIMELINE in DEFAULT_SECTIONS
    assert ReviewSection.OPERATOR in DEFAULT_SECTIONS
    assert ReviewSection.RUNTIME in DEFAULT_SECTIONS
