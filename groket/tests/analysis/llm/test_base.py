"""Tests for LlmReviewAnalyzer base class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from groket.analysis.llm.base import LlmReviewAnalyzer
from groket.analysis.llm.client import GrokStructuredResult
from groket.analysis.llm.context import SessionContextPack


class _Tiny(LlmReviewAnalyzer):
    review_id = "tiny"
    review_name = "Tiny"
    review_version = "1"

    def build_instructions(self, pack: SessionContextPack) -> str:
        return f"turns={pack.turn_count}"


def test_info() -> None:
    a = _Tiny()
    assert a.info.id == "tiny"
    assert a.info.defer is True
    assert a.info.version == "1"


def test_analyze_success(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(
        '{"info":{"id":"sid"},"current_model_id":"m"}',
        encoding="utf-8",
    )
    payload = {
        "summary": "ok issues",
        "all_clear": False,
        "findings": [
            {
                "id": "f1",
                "severity": "medium",
                "title": "Issue",
                "what_model_did": "Did X",
                "what_should_have_done": "Do Y",
                "why_mistake": "Because Z",
                "evidence": [],
            }
        ],
    }
    with patch(
        "groket.analysis.llm.base.GrokCliClient.complete_structured",
        return_value=GrokStructuredResult(payload=payload, raw="{}"),
    ):
        r = _Tiny().analyze(tmp_path)
    assert r.ok
    assert len(r.findings) == 1
    assert r.findings[0].title == "Issue"
    assert "report" in r.artifacts
    assert "What the model did" in r.artifacts["report"]
    assert "Did X" in r.findings[0].detail or r.findings[0].detail


def test_analyze_unavailable(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    with patch(
        "groket.analysis.llm.base.GrokCliClient.complete_structured",
        return_value=GrokStructuredResult(payload=None, raw=None),
    ):
        r = _Tiny().analyze(tmp_path)
    assert r.ok
    assert r.findings[0].id.endswith("no-review")
    assert "report" in r.artifacts


def test_analyze_unavailable_surfaces_stop_reason(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    import json

    raw = json.dumps(
        {
            "stopReason": "Cancelled",
            "structuredOutputError": "model did not produce structured output",
            "structuredOutput": None,
            "text": '{"summary":"Reading the full offloaded prompt.","all_clear":false,"findings":[]}',
        }
    )
    bad = {
        "summary": "Reading the full offloaded prompt.",
        "all_clear": False,
        "findings": [],
    }
    with patch(
        "groket.analysis.llm.base.GrokCliClient.complete_structured",
        return_value=GrokStructuredResult(payload=bad, raw=raw),
    ):
        r = _Tiny().analyze(tmp_path)
    detail = r.findings[0].detail
    assert "stopReason=Cancelled" in detail
    assert "structured" in detail.lower()


def test_analyze_incomplete_then_retry(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    bad = {
        "summary": "Reading the full offloaded prompt before producing.",
        "all_clear": False,
        "findings": [],
    }
    good = {
        "summary": "Fine",
        "all_clear": True,
        "findings": [],
    }
    results = [
        GrokStructuredResult(payload=bad, raw="1"),
        GrokStructuredResult(payload=good, raw="2"),
    ]

    def _side_effect(*_a, **_k):
        return results.pop(0)

    with patch(
        "groket.analysis.llm.base.GrokCliClient.complete_structured",
        side_effect=_side_effect,
    ):
        r = _Tiny().analyze(tmp_path)
    assert "all clear" in r.summary
    assert r.artifacts.get("report")


def test_analyze_context_failure(tmp_path: Path) -> None:
    with patch(
        "groket.analysis.llm.base.build_session_context_pack",
        side_effect=RuntimeError("boom"),
    ):
        r = _Tiny().analyze(tmp_path)
    assert r.ok is False
    assert "boom" in r.error
