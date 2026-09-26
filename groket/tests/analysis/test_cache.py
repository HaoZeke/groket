"""Tests for analysis result cache."""

from __future__ import annotations

import json
from pathlib import Path

from groket.analysis._cache import (
    load_cached_result,
    save_cached_result,
)
from groket.analysis.base import AnalysisResult, Finding
from groket.models import Severity


def _make_session(tmp_path: Path, name: str = "session-1") -> Path:
    """Create a fake session directory with an events.jsonl containing turn_ended."""
    sd = tmp_path / name
    sd.mkdir(parents=True)
    events = [
        {"type": "turn_started"},
        {"type": "turn_ended", "outcome": "ended"},
    ]
    (sd / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return sd


def _make_result(session_dir: Path, analyzer_id: str = "engine") -> AnalysisResult:
    return AnalysisResult(
        session_id=session_dir.name,
        session_dir=str(session_dir),
        analyzer_id=analyzer_id,
        ok=True,
        findings=[
            Finding(
                id="f1",
                plugin_id=analyzer_id,
                severity=Severity.HIGH,
                title="Issue",
                detail="details",
                category="Cat",
            ),
        ],
        summary="1 finding",
    )


class TestSaveAndLoad:
    def test_roundtrip(self, tmp_path):
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path, "sess-1")
        result = _make_result(sd)
        save_cached_result(cache, sd, "engine", "1.0", result)
        loaded = load_cached_result(cache, sd, "engine", "1.0")
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert loaded.finding_count == 1
        assert loaded.findings[0].severity == Severity.HIGH

    def test_miss_when_no_cache(self, tmp_path):
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        assert load_cached_result(cache, sd, "engine", "1.0") is None

    def test_miss_on_version_change(self, tmp_path):
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        result = _make_result(sd)
        save_cached_result(cache, sd, "engine", "1.0", result)
        assert load_cached_result(cache, sd, "engine", "2.0") is None

    def test_allow_stale_returns_version_mismatch(self, tmp_path):
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        result = _make_result(sd)
        save_cached_result(cache, sd, "engine", "1.0", result)
        loaded = load_cached_result(cache, sd, "engine", "2.0", allow_stale=True)
        assert loaded is not None
        assert loaded.finding_count == 1

    def test_miss_on_trace_mtime_change(self, tmp_path):
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        result = _make_result(sd)
        save_cached_result(cache, sd, "engine", "1.0", result)
        # Force a visible mtime change by backdating the cache entry
        import json as _json

        fp = cache / "analysis" / sd.name / "engine.json"
        data = _json.loads(fp.read_text())
        data["_trace_mtime"] = data["_trace_mtime"] - 10.0
        fp.write_text(_json.dumps(data))
        assert load_cached_result(cache, sd, "engine", "1.0") is None

    def test_corrupt_cache_file(self, tmp_path):
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path, "bad")
        fp = cache / "analysis" / "bad" / "engine.json"
        fp.parent.mkdir(parents=True)
        fp.write_text("not json {{{")
        assert load_cached_result(cache, sd, "engine", "1.0") is None


class TestSaveOSError:
    def test_save_oserror_is_silent(self, tmp_path: Path) -> None:
        """save_cached_result handles OSError on write gracefully."""
        sd = _make_session(tmp_path)
        result = _make_result(sd)
        # Create a directory structure that blocks the cache file write
        cache = tmp_path / "blocked_cache"
        fp = cache / "analysis" / sd.name / "engine.json"
        fp.parent.mkdir(parents=True)
        # Make the target a directory so writing to it as a file will fail
        fp.mkdir()
        save_cached_result(cache, sd, "engine", "1.0", result)


class TestTraceMtime:
    def test_no_trace_files(self, tmp_path: Path) -> None:
        from groket.analysis._cache import _trace_mtime

        sd = tmp_path / "empty"
        sd.mkdir()
        assert _trace_mtime(sd) == 0.0

    def test_trace_mtime_uses_newest(self, tmp_path: Path) -> None:
        import time

        from groket.analysis._cache import _trace_mtime

        sd = tmp_path / "s"
        sd.mkdir()
        (sd / "events.jsonl").write_text("{}")
        time.sleep(0.01)
        (sd / "updates.jsonl").write_text("{}")
        mt = _trace_mtime(sd)
        assert mt > 0

    def test_trace_mtime_includes_operator_notes(self, tmp_path: Path) -> None:
        import time

        from groket.analysis._cache import _trace_mtime
        from groket.notes import NoteEntry, NotesDoc, save_notes

        sd = tmp_path / "s-notes-mt"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        base = _trace_mtime(sd)
        time.sleep(0.02)
        doc = NotesDoc(session_id=sd.name)
        doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "x"}, note_id="n1"))
        save_notes(sd, doc)
        assert _trace_mtime(sd) >= base
        assert _trace_mtime(sd) > 0


class TestCacheDataEdgeCases:
    def test_non_dict_data(self, tmp_path: Path) -> None:
        """Cache file containing a JSON list (not dict) returns None."""
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        fp = cache / "analysis" / sd.name / "engine.json"
        fp.parent.mkdir(parents=True)
        fp.write_text("[1, 2, 3]")
        assert load_cached_result(cache, sd, "engine", "1.0") is None

    def test_wrong_schema_version(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        fp = cache / "analysis" / sd.name / "engine.json"
        fp.parent.mkdir(parents=True)
        fp.write_text(json.dumps({"_schema": 999, "_plugin_version": "1.0"}))
        assert load_cached_result(cache, sd, "engine", "1.0") is None

    def test_missing_result_key(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        sd = _make_session(tmp_path)
        result = _make_result(sd)
        save_cached_result(cache, sd, "engine", "1.0", result)
        fp = cache / "analysis" / sd.name / "engine.json"
        data = json.loads(fp.read_text())
        del data["result"]
        fp.write_text(json.dumps(data))
        assert load_cached_result(cache, sd, "engine", "1.0") is None


class TestAnalyzerInfoVersion:
    def test_default_version(self) -> None:
        from groket.analysis.base import AnalyzerInfo

        info = AnalyzerInfo(id="test", name="Test")
        assert info.version == "0"

    def test_custom_version(self) -> None:
        from groket.analysis.base import AnalyzerInfo

        info = AnalyzerInfo(id="test", name="Test", version="2.1")
        assert info.version == "2.1"
