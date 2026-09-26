"""Named prompt sections for LLM review envelopes.

Plugins reference :class:`ReviewSection` and :data:`DEFAULT_SECTIONS` — not
angle-bracket tag names. Tags are an internal render detail.
"""

from __future__ import annotations

from enum import StrEnum


class ReviewSection(StrEnum):
    """Sections composed into the default review prompt."""

    INSTRUCTIONS = "instructions"
    META = "meta"
    RUNTIME = "runtime"
    CONSTRAINTS = "constraints"
    OPERATOR = "operator_instructions"
    PRIOR_FINDINGS = "prior_findings"
    TIMELINE = "timeline"
    EPILOGUE = "epilogue"


DEFAULT_SECTIONS: tuple[ReviewSection, ...] = (
    ReviewSection.INSTRUCTIONS,
    ReviewSection.META,
    ReviewSection.RUNTIME,
    ReviewSection.CONSTRAINTS,
    ReviewSection.OPERATOR,
    ReviewSection.PRIOR_FINDINGS,
    ReviewSection.TIMELINE,
    ReviewSection.EPILOGUE,
)
