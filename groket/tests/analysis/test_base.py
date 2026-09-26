"""Tests for analysis base types: Finding, AnalysisResult, NoopAnalyzer."""

from __future__ import annotations

from groket.analysis.base import (
    AnalysisResult,
    Finding,
    NoopAnalyzer,
)
from groket.models import Severity

# ── Finding ───────────────────────────────────────────────────────────────


class TestFinding:
    def test_basic_construction(self):
        f = Finding(
            id="rule-1",
            plugin_id="engine",
            severity=Severity.HIGH,
            title="Bad tool call",
            detail="Details here",
            category="Behavior",
            tool_call_ids=["c1", "c2"],
            update_indices=[10, 20],
        )
        assert f.id == "rule-1"
        assert f.plugin_id == "engine"
        assert f.severity == Severity.HIGH
        assert f.title == "Bad tool call"

    def test_all_tool_call_ids_flat(self):
        f = Finding(
            id="r1",
            plugin_id="p",
            severity=Severity.LOW,
            title="t",
            tool_call_ids=["a", "b"],
        )
        assert f.all_tool_call_ids == ["a", "b"]

    def test_all_tool_call_ids_with_children(self):
        child1 = Finding(
            id="c1",
            plugin_id="p",
            severity=Severity.LOW,
            title="child1",
            tool_call_ids=["b", "c"],
        )
        child2 = Finding(
            id="c2",
            plugin_id="p",
            severity=Severity.LOW,
            title="child2",
            tool_call_ids=["c", "d"],
        )
        parent = Finding(
            id="p1",
            plugin_id="p",
            severity=Severity.HIGH,
            title="parent",
            tool_call_ids=["a"],
            children=[child1, child2],
        )
        ids = parent.all_tool_call_ids
        # Deduped, order preserved
        assert ids == ["a", "b", "c", "d"]

    def test_all_update_indices_sorted(self):
        child = Finding(
            id="c",
            plugin_id="p",
            severity=Severity.LOW,
            title="c",
            update_indices=[30, 10],
        )
        parent = Finding(
            id="p",
            plugin_id="p",
            severity=Severity.HIGH,
            title="p",
            update_indices=[20, 5],
            children=[child],
        )
        assert parent.all_update_indices == [5, 10, 20, 30]

    def test_all_event_indices_sorted(self):
        child = Finding(
            id="c",
            plugin_id="p",
            severity=Severity.LOW,
            title="c",
            event_indices=[30, 10],
        )
        parent = Finding(
            id="p",
            plugin_id="p",
            severity=Severity.HIGH,
            title="p",
            event_indices=[20, 5],
            children=[child],
        )
        assert parent.all_event_indices == [5, 10, 20, 30]

    def test_event_indices_roundtrip(self):
        f = Finding(
            id="f1",
            plugin_id="eng",
            severity=Severity.HIGH,
            title="Title",
            event_indices=[2, 47],
        )
        restored = Finding.from_dict(f.to_dict())
        assert restored.event_indices == [2, 47]
        assert restored.all_event_indices == [2, 47]


# ── AnalysisResult ────────────────────────────────────────────────────────


class TestAnalysisResult:
    def test_empty(self):
        r = AnalysisResult(session_id="s1", analyzer_id="basic")
        assert r.finding_count == 0
        assert r.high_count == 0
        assert r.medium_count == 0
        assert r.ok is True

    def test_counts(self):
        findings = [
            Finding(id="a", plugin_id="p", severity=Severity.HIGH, title="h1"),
            Finding(id="b", plugin_id="p", severity=Severity.HIGH, title="h2"),
            Finding(id="c", plugin_id="p", severity=Severity.MEDIUM, title="m1"),
            Finding(id="d", plugin_id="p", severity=Severity.LOW, title="l1"),
        ]
        r = AnalysisResult(session_id="s1", analyzer_id="eng", findings=findings)
        assert r.finding_count == 4
        assert r.high_count == 2
        assert r.medium_count == 1

    def test_to_dict(self):
        f = Finding(
            id="x",
            plugin_id="p",
            severity=Severity.MEDIUM,
            title="Title",
            detail="Detail",
            category="Cat",
        )
        r = AnalysisResult(
            session_id="s1",
            session_dir="/tmp/s1",
            analyzer_id="engine",
            findings=[f],
            summary="1 finding",
        )
        d = r.to_dict()
        assert d["session_id"] == "s1"
        assert d["finding_count"] == 1
        assert d["findings"][0]["severity"] == "medium"
        assert d["findings"][0]["title"] == "Title"

    def test_error_result(self):
        r = AnalysisResult(
            session_id="s1",
            analyzer_id="broken",
            ok=False,
            error="plugin crashed",
        )
        assert r.ok is False
        assert r.error == "plugin crashed"
        assert r.finding_count == 0

    def test_to_dict_includes_children(self):
        child = Finding(
            id="c1",
            plugin_id="p",
            severity=Severity.LOW,
            title="child",
            tool_call_ids=["tc1"],
        )
        parent = Finding(
            id="p1",
            plugin_id="p",
            severity=Severity.HIGH,
            title="parent",
            children=[child],
        )
        r = AnalysisResult(session_id="s1", findings=[parent])
        d = r.to_dict()
        assert len(d["findings"]) == 1
        assert d["findings"][0]["children"][0]["id"] == "c1"

    def test_from_dict_roundtrip(self):
        child = Finding(
            id="c1",
            plugin_id="eng",
            severity=Severity.LOW,
            title="child",
            detail="d",
            category="Cat",
            tool_call_ids=["tc1"],
            update_indices=[5],
        )
        f = Finding(
            id="f1",
            plugin_id="eng",
            severity=Severity.HIGH,
            title="Title",
            detail="Detail",
            category="Cat",
            tool_call_ids=["tc0"],
            update_indices=[1, 10],
            children=[child],
            extras={"key": "val"},
        )
        original = AnalysisResult(
            session_id="s1",
            session_dir="/tmp/s1",
            analyzer_id="engine",
            ok=True,
            findings=[f],
            summary="summary",
            artifacts={"report": "# Report"},
            extras={"meta": 42},
        )
        restored = AnalysisResult.from_dict(original.to_dict())
        assert restored.session_id == "s1"
        assert restored.analyzer_id == "engine"
        assert restored.finding_count == 1
        assert restored.findings[0].severity == Severity.HIGH
        assert restored.findings[0].children[0].id == "c1"
        assert restored.findings[0].children[0].severity == Severity.LOW
        assert restored.findings[0].tool_call_ids == ["tc0"]
        assert restored.findings[0].extras == {"key": "val"}
        assert restored.summary == "summary"
        assert restored.artifacts == {"report": "# Report"}
        assert restored.extras == {"meta": 42}

    def test_from_dict_empty(self):
        r = AnalysisResult.from_dict({})
        assert r.session_id == ""
        assert r.ok is True
        assert r.findings == []


# ── NoopAnalyzer ──────────────────────────────────────────────────────────


class TestNoopAnalyzer:
    def test_info(self):
        a = NoopAnalyzer()
        assert a.info.id == "noop"
        assert a.info.name == "None"

    def test_analyze(self, tmp_path):
        a = NoopAnalyzer()
        result = a.analyze(tmp_path / "some-session")
        assert result.ok is True
        assert result.findings == []
        assert result.analyzer_id == "noop"
