"""Stale plugin version hints for force re-analyze (no auto run)."""

from __future__ import annotations

from pathlib import Path

from groket.analysis._cache import save_cached_result
from groket.analysis.base import AnalysisResult
from groket.analysis.service import AnalysisService


def test_stale_hints_version_bump(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    (sess / "events.jsonl").write_text('{"type":"turn_ended","timestamp":1}\n', encoding="utf-8")
    save_cached_result(
        cache,
        sess,
        "feedback",
        "10",
        AnalysisResult(session_id="sess", analyzer_id="feedback", ok=True),
    )
    svc = AnalysisService(
        tmp_path,
        enabled_ids={"basic", "engine", "feedback"},
        cache_root=cache,
    )
    from groket.analysis.base import AnalyzerInfo

    def _list() -> list:
        return [
            AnalyzerInfo(id="feedback", name="F", version="11", defer=True),
            AnalyzerInfo(id="engine", name="E", version="1"),
        ]

    svc.list_plugins = _list  # type: ignore[method-assign]
    # Recent plugin edit so the version-bump nag window is open.
    svc._analyzer_own_source_mtime = lambda _aid: __import__("time").time()  # type: ignore[method-assign]
    hints = svc.stale_analyzer_hints(sess)
    assert any("feedback" in h and "10" in h and "11" in h for h in hints)
    # Non-deferred "not in cache" must not banner (engine is free/optional here).
    assert not any("engine" in h and "not in cache" in h for h in hints)


def test_stale_hints_deferred_missing_from_cache(tmp_path: Path) -> None:
    """Missing deferred LLM cache is worth a banner when other analysis exists."""
    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    save_cached_result(
        cache,
        sess,
        "engine",
        "1",
        AnalysisResult(session_id="sess", analyzer_id="engine", ok=True),
    )
    svc = AnalysisService(tmp_path, enabled_ids={"engine", "feedback"}, cache_root=cache)
    from groket.analysis.base import AnalyzerInfo

    svc.list_plugins = lambda: [  # type: ignore[method-assign]
        AnalyzerInfo(id="engine", name="E", version="1"),
        AnalyzerInfo(id="feedback", name="F", version="13", defer=True),
    ]
    hints = svc.stale_analyzer_hints(sess)
    assert any("feedback" in h and "not in cache" in h for h in hints)


def test_stale_hints_quiet_after_plugin_stable(tmp_path: Path) -> None:
    """Historical version gaps stop nagging once the plugin file is old."""
    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    save_cached_result(
        cache,
        sess,
        "feedback",
        "10",
        AnalysisResult(session_id="sess", analyzer_id="feedback", ok=True),
    )
    svc = AnalysisService(tmp_path, enabled_ids={"feedback"}, cache_root=cache)
    from groket.analysis.base import AnalyzerInfo

    svc.list_plugins = lambda: [  # type: ignore[method-assign]
        AnalyzerInfo(id="feedback", name="F", version="13", defer=True)
    ]
    # Plugin last edited a week ago — outside ANALYSIS_STALE_HINT_WINDOW_S.
    svc._analyzer_own_source_mtime = (  # type: ignore[method-assign]
        lambda _aid: __import__("time").time() - 8 * 24 * 3600
    )
    assert svc.stale_analyzer_hints(sess) == []


def test_stale_hints_empty_when_no_cache(tmp_path: Path) -> None:
    sess = tmp_path / "sess"
    sess.mkdir()
    svc = AnalysisService(tmp_path, enabled_ids={"basic"}, cache_root=tmp_path / "c")
    from groket.analysis.base import AnalyzerInfo

    svc.list_plugins = lambda: [AnalyzerInfo(id="basic", name="B", version="1")]  # type: ignore[method-assign]
    assert svc.stale_analyzer_hints(sess) == []


def test_stale_when_source_newer_than_cache(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    from groket.analysis import registry as reg
    from groket.analysis._cache import save_cached_result
    from groket.analysis.base import AnalysisResult, AnalyzerInfo

    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    (sess / "events.jsonl").write_text('{"type":"turn_ended"}\n', encoding="utf-8")
    save_cached_result(
        cache,
        sess,
        "fake",
        "1",
        AnalysisResult(session_id="sess", analyzer_id="fake", ok=True),
    )
    # Make cache older
    cpath = cache / "analysis" / "sess" / "fake.json"
    old = time.time() - 100
    os.utime(cpath, (old, old))

    class Fake:
        @property
        def info(self):
            return AnalyzerInfo(id="fake", name="F", version="1")

        def analyze(self, session_dir, **kw):
            return AnalysisResult(ok=True, analyzer_id="fake")

    # Register with a real module file we can touch — use this test file
    fake = Fake()
    monkeypatch.setitem(reg._REGISTRY, "fake", fake)
    # Point Fake's module to this test file
    Fake.__module__ = __name__

    svc = AnalysisService(tmp_path, enabled_ids={"fake"}, cache_root=cache)
    svc.list_plugins = lambda: [AnalyzerInfo(id="fake", name="F", version="1")]  # type: ignore[method-assign]
    # Touch this test module file (recent edit → nag window open)
    os.utime(Path(__file__), None)
    hints = svc.stale_analyzer_hints(sess)
    assert any("source newer" in h for h in hints), hints


def test_source_newer_ignores_package_base_mtimes(tmp_path: Path, monkeypatch) -> None:
    """Editing groket.analysis.base must not mark a user plugin stale."""
    import os
    import time

    from groket.analysis import registry as reg
    from groket.analysis._cache import save_cached_result
    from groket.analysis.base import AnalysisResult, AnalyzerInfo

    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    save_cached_result(
        cache,
        sess,
        "fake",
        "1",
        AnalysisResult(session_id="sess", analyzer_id="fake", ok=True),
    )
    cpath = cache / "analysis" / "sess" / "fake.json"
    # Cache is newer than this test file; only package bases would be "newer".
    os.utime(cpath, None)

    class Fake:
        @property
        def info(self):
            return AnalyzerInfo(id="fake", name="F", version="1")

        def analyze(self, session_dir, **kw):
            return AnalysisResult(ok=True, analyzer_id="fake")

    fake = Fake()
    monkeypatch.setitem(reg._REGISTRY, "fake", fake)
    Fake.__module__ = __name__
    # Own module older than cache
    os.utime(Path(__file__), (time.time() - 1000, time.time() - 1000))

    svc = AnalysisService(tmp_path, enabled_ids={"fake"}, cache_root=cache)
    svc.list_plugins = lambda: [AnalyzerInfo(id="fake", name="F", version="1")]  # type: ignore[method-assign]
    svc._analyzer_own_source_mtime = lambda _aid: time.time()  # type: ignore[method-assign]
    # Own source is "recent" for the window, but not newer than cache file.
    svc._analyzer_own_source_mtime = lambda _aid: time.time() - 10  # type: ignore[method-assign]
    # Force own mtime older than cache
    own = cpath.stat().st_mtime - 50
    svc._analyzer_own_source_mtime = lambda _aid: own  # type: ignore[method-assign]
    assert svc._analyzer_source_newer_than_cache(sess, "fake") is False
    # Outside recent window → no hints even with version games
    svc._analyzer_own_source_mtime = lambda _aid: time.time() - 9 * 24 * 3600  # type: ignore[method-assign]
    assert svc.stale_analyzer_hints(sess) == []
