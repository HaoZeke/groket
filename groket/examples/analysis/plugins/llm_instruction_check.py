"""Minimal LLM review example — instruction adherence only.

Enable::

    "analysis": { "plugins": ["llm_instruction_check:InstructionCheckAnalyzer"] }

Uses the same plumbing as feedback review but a shorter rubric. Good starting
point for custom reviews: subclass :class:`~groket.analysis.llm.LlmReviewAnalyzer`
and implement :meth:`build_instructions` only.

Findings tab + ``artifacts["report"]`` on the Report tab.
"""

from __future__ import annotations

from groket.analysis.llm import LlmReviewAnalyzer, SessionContextPack


class InstructionCheckAnalyzer(LlmReviewAnalyzer):
    """Light instruction-adherence review (example)."""

    review_id = "instruction-check"
    review_name = "Instruction Check"
    review_version = "2"
    review_description = "Minimal LLM check: did the agent follow operator turns?"
    report_title_prefix = "Instruction check"
    effort = "low"

    def build_instructions(self, pack: SessionContextPack) -> str:
        note_n = len(pack.operator_notes.notes)
        notes_bit = (
            f" Also consider {note_n} operator note(s) when present."
            if note_n
            else ""
        )
        return (
            f"This session has {pack.turn_count} operator turn(s). "
            "Only report clear failures to follow the *active* operator "
            "instruction for each turn (see operator instructions block)."
            f"{notes_bit} "
            "Ignore style nits. Respect runtime fairness constraints "
            "(e.g. always-approve). Prefer zero findings when the agent "
            "followed the asks."
        )
