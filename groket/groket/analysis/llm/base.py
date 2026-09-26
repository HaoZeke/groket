"""Base analyzer for structured LLM session reviews."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Unpack

from ...models import JsonObject, Severity
from ..base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from .client import GrokCliClient
from .context import SessionContextPack, build_session_context_pack
from .review import (
    is_incomplete_review,
    map_review_findings,
    render_prompt_envelope,
    render_review_report,
)
from .schema import REVIEW_FINDINGS_SCHEMA

logger = logging.getLogger(__name__)

_DEFAULT_DIGEST_CHARS = 24_000
_RETRY_DIGEST_CHARS = 12_000


def _raw_failure_hint(raw: str | None) -> str:
    """Extract stopReason / structuredOutputError from grok JSON stdout."""
    if not raw:
        return ""
    text = raw.strip()
    if not text.startswith("{"):
        # stderr-only failures (e.g. "Error: max turns reached")
        one = " ".join(text.split())
        return one[:160] if one else ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    bits: list[str] = []
    stop = data.get("stopReason")
    if stop:
        bits.append(f"stopReason={stop}")
    so_err = data.get("structuredOutputError")
    if so_err:
        bits.append(str(so_err)[:120])
    return "; ".join(bits)


class LlmReviewAnalyzer:
    """Analyzer base: implement :meth:`build_instructions`; plumbing is shared.

    Produces timeline-linked :class:`~groket.analysis.base.Finding` rows for the
    Findings tab and ``artifacts["report"]`` markdown for the Report tab.
    """

    review_id: str = "llm-review"
    review_name: str = "LLM Review"
    review_version: str = "1"
    review_description: str = "Structured LLM session review."
    defer: bool = True
    optional: bool = True
    effort: str = "medium"
    detail_mode: str = "one_line"
    digest_chars: int = _DEFAULT_DIGEST_CHARS
    retry_digest_chars: int = _RETRY_DIGEST_CHARS
    report_title_prefix: str = "Feedback review"

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id=self.review_id,
            name=self.review_name,
            description=self.review_description,
            optional=self.optional,
            defer=self.defer,
            version=self.review_version,
        )

    def build_instructions(self, pack: SessionContextPack) -> str:
        """Return rubric / review instructions (not the full prompt envelope)."""
        raise NotImplementedError

    def build_prompt(self, pack: SessionContextPack) -> str:
        """Full prompt; default wraps :meth:`build_instructions` in the envelope."""
        return render_prompt_envelope(pack, self.build_instructions(pack))

    def schema(self) -> JsonObject:
        """JSON Schema for structured output (default: review findings)."""
        return REVIEW_FINDINGS_SCHEMA

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        prior = list(kwargs.get("prior_findings") or [])
        flags = list(kwargs.get("flags") or [])
        plugin_id = self.review_id
        client = GrokCliClient()

        try:
            pack = build_session_context_pack(
                session_dir,
                prior_findings=prior,
                flags=flags,
                digest_chars=self.digest_chars,
            )
        except Exception as exc:
            logger.warning("Context pack failed for %s: %s", session_dir.name, exc)
            return AnalysisResult(
                session_id=Path(session_dir).name,
                session_dir=str(session_dir),
                analyzer_id=plugin_id,
                ok=False,
                error=f"context pack failed: {exc}",
            )

        budgets = (self.digest_chars, self.retry_digest_chars)
        payload: JsonObject | None = None
        raw: str | None = None
        for i, budget in enumerate(budgets):
            if i > 0:
                pack = build_session_context_pack(
                    session_dir,
                    prior_findings=prior,
                    flags=flags,
                    digest_chars=budget,
                )
            prompt = self.build_prompt(pack)
            result = client.complete_structured(
                prompt,
                schema=self.schema(),
                effort=self.effort,
            )
            payload, raw = result.payload, result.raw
            if not is_incomplete_review(payload):
                break
            logger.warning(
                "LLM review incomplete for %s (attempt %d); retrying",
                pack.session_id,
                i + 1,
            )

        artifacts: dict[str, str] = {}
        findings: list[Finding] = []
        summary_bits = [
            f"{pack.turn_count} turn(s)",
            f"{pack.event_count} event(s)",
            f"{len(prior)} detector hint(s)",
        ]

        if payload is not None and not is_incomplete_review(payload):
            findings = map_review_findings(
                payload,
                pack.timeline,
                plugin_id=plugin_id,
                detail_mode=self.detail_mode,
            )
            artifacts["report"] = render_review_report(
                payload,
                findings,
                pack,
                title_prefix=self.report_title_prefix,
            )
            try:
                artifacts["review_json"] = json.dumps(payload, indent=2, ensure_ascii=False)
            except (TypeError, ValueError):
                pass
            n = len(findings)
            if n:
                summary_bits.append(f"{n} LLM finding(s)")
            elif bool(payload.get("all_clear")):
                summary_bits.append("all clear")
            else:
                summary_bits.append("no LLM findings")
        else:
            detail = (
                "LLM structured review did not return a usable result "
                "(missing grok binary, auth failure, timeout, max-turns, or "
                "incomplete offloaded-prompt shell)."
            )
            if payload is not None:
                detail += f" Last summary: {str(payload.get('summary') or '')[:240]}"
            hint = _raw_failure_hint(raw)
            if hint:
                detail += f" ({hint})"
            findings.append(
                Finding(
                    id=f"{plugin_id}-no-review",
                    plugin_id=plugin_id,
                    severity=Severity.LOW,
                    title="LLM feedback review unavailable",
                    detail=detail,
                    category="Feedback",
                )
            )
            if payload is not None:
                try:
                    artifacts["review_json"] = json.dumps(payload, indent=2, ensure_ascii=False)
                except (TypeError, ValueError):
                    pass
            if raw:
                artifacts["review_raw"] = raw[:50_000]
            artifacts["report"] = f"# {self.report_title_prefix} — {pack.session_id}\n\n{detail}\n"
            summary_bits.append("no usable LLM review")

        return AnalysisResult(
            session_id=pack.session_id,
            session_dir=str(pack.session_dir),
            analyzer_id=plugin_id,
            ok=True,
            summary=", ".join(summary_bits),
            findings=findings,
            artifacts=artifacts,
            extras={
                "turn_count": pack.turn_count,
                "event_count": pack.event_count,
                "digest_truncated": pack.digest_truncated,
            },
        )
