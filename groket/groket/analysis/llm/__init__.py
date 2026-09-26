"""LLM-backed session review plumbing for analysis plugins.

Plugins subclass :class:`LlmReviewAnalyzer` and implement
:meth:`~LlmReviewAnalyzer.build_instructions`. Core builds the session
context pack (operator turns, operator notes, timeline digest, runtime
fairness), calls headless ``grok`` with a structured schema, maps results to
:class:`~groket.analysis.base.Finding` rows (timeline-linked), and sets
``artifacts["report"]`` for the Report tab.
"""

from __future__ import annotations

from .base import LlmReviewAnalyzer
from .client import GrokCliClient
from .context import RuntimePolicy, SessionContextPack, build_session_context_pack
from .review import (
    is_incomplete_review,
    map_review_findings,
    render_review_report,
)
from .schema import REVIEW_FINDINGS_SCHEMA
from .sections import DEFAULT_SECTIONS, ReviewSection

__all__ = [
    "DEFAULT_SECTIONS",
    "GrokCliClient",
    "LlmReviewAnalyzer",
    "REVIEW_FINDINGS_SCHEMA",
    "ReviewSection",
    "RuntimePolicy",
    "SessionContextPack",
    "build_session_context_pack",
    "is_incomplete_review",
    "map_review_findings",
    "render_review_report",
]
