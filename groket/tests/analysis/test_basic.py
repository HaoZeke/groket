"""Tests for BasicAnalyzer."""

from __future__ import annotations

from pathlib import Path

from groket.analysis.basic import BasicAnalyzer


class TestBasicAnalyzer:
    def test_info(self):
        a = BasicAnalyzer()
        assert a.info.id == "basic"
        assert a.info.name == "Basic"
        assert a.info.optional is False

    def test_analyze_full_session(self, session_dir):
        a = BasicAnalyzer()
        result = a.analyze(session_dir)
        assert result.ok is True
        assert result.analyzer_id == "basic"
        assert result.extras["model_id"] == "v9-dietcoke"
        assert result.extras["turn_outcome"] == "success"
        assert result.extras["tool_count"] == 5
        assert "model=v9-dietcoke" in result.summary

    def test_analyze_error_session(self, error_session_dir):
        a = BasicAnalyzer()
        result = a.analyze(error_session_dir)
        assert result.ok is True
        assert "outcome=error" in result.summary

    def test_analyze_minimal_session(self, empty_session_dir):
        a = BasicAnalyzer()
        result = a.analyze(empty_session_dir)
        assert result.ok is True

    def test_analyze_nonexistent(self, tmp_path: Path) -> None:
        a = BasicAnalyzer()
        result = a.analyze(tmp_path / "ghost")
        assert result.ok is True  # load_session_meta doesn't crash on missing files

    def test_analyze_session_no_tool_count(self, tmp_path: Path) -> None:
        """Session with model and outcome but zero tool_call_count."""
        import json

        a = BasicAnalyzer()
        sd = tmp_path / "no-tools"
        sd.mkdir()
        summary = {
            "info": {"id": sd.name},
            "current_model_id": "v9",
        }
        (sd / "summary.json").write_text(json.dumps(summary))
        events = [
            {"type": "turn_started", "model_id": "v9"},
            {"type": "turn_ended", "outcome": "success"},
        ]
        (sd / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        (sd / "updates.jsonl").write_text("")
        result = a.analyze(sd)
        assert result.ok is True
        # tool_count=0 → not appended to summary_parts
        assert "tools=" not in result.summary
