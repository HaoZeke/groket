"""Tests for AnalysisService."""

from __future__ import annotations

from pathlib import Path
from typing import Unpack

import pytest
from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.analysis.config import AnalysisPipelineConfig
from groket.analysis.registry import _REGISTRY, register_analyzer
from groket.analysis.service import AnalysisService
from groket.models import Severity


class _TestAnalyzer:
    def __init__(self, aid: str = "test-svc"):
        self._id = aid

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(id=self._id, name="Test", description="for service tests")

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        return AnalysisResult(
            session_id=session_dir.name,
            session_dir=str(session_dir),
            analyzer_id=self._id,
            ok=True,
            findings=[
                Finding(
                    id="f1", plugin_id=self._id, severity=Severity.MEDIUM, title="test finding"
                ),
            ],
            summary="1 finding",
        )


class _CrashingAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(id="crasher", name="Crasher", description="always fails")

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


class TestAnalysisService:
    def test_list_plugins(self, work_dir):
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig())
        plugins = svc.list_plugins()
        assert any(p.id == "basic" for p in plugins)
        assert any(p.id == "engine" for p in plugins)

    def test_analyze_session_basic(self, work_dir, session_dir):
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig())
        result = svc.analyze_session(session_dir, analyzer_id="basic")
        assert result.ok is True
        assert result.analyzer_id == "basic"

    def test_analyze_all_runs_builtins_only_by_default(self, work_dir, session_dir):
        """Registry-only plugins must not run unless enabled for this service."""
        register_analyzer(_TestAnalyzer("svc-test"))
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig())
        results = svc.analyze_all(session_dir)
        assert "basic" in results
        assert "engine" in results
        assert "svc-test" not in results

    def test_analyze_all_runs_explicitly_enabled(self, work_dir, session_dir):
        register_analyzer(_TestAnalyzer("svc-test"))
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            enabled_ids={"basic", "engine", "svc-test"},
        )
        results = svc.analyze_all(session_dir)
        assert "svc-test" in results
        assert results["svc-test"].finding_count == 1

    def test_analyze_all_skips_noop(self, work_dir, session_dir):
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig())
        results = svc.analyze_all(session_dir)
        assert "noop" not in results

    def test_analyze_all_catches_plugin_crash(self, work_dir, session_dir):
        register_analyzer(_CrashingAnalyzer())
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            enabled_ids={"basic", "engine", "crasher"},
        )
        results = svc.analyze_all(session_dir)
        assert "crasher" in results
        assert results["crasher"].ok is False
        assert "exception" in results["crasher"].error

    def test_analyze_all_ignores_cache_for_disabled_plugins(
        self,
        work_dir,
        session_dir,
    ):
        """On-disk cache for a plugin not in config must not appear in results."""
        cache_root = work_dir / "cache"
        register_analyzer(_TestAnalyzer("orphan-plugin"))
        # First service: enable plugin and populate cache
        svc_on = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=cache_root,
            enabled_ids={"basic", "engine", "orphan-plugin"},
        )
        r1 = svc_on.analyze_all(session_dir)
        assert "orphan-plugin" in r1
        cached = cache_root / "analysis" / session_dir.name / "orphan-plugin.json"
        assert cached.is_file()
        # Second service: builtins only — must not serve orphan cache
        svc_off = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=cache_root,
        )
        r2 = svc_off.analyze_all(session_dir)
        assert "orphan-plugin" not in r2
        assert "basic" in r2

    def test_reload_config(self, work_dir):
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig())
        cfg = svc.reload_config()
        assert isinstance(cfg, AnalysisPipelineConfig)
        assert "basic" in svc.enabled_ids

    def test_config_plugins_loaded(self, work_dir):
        cfg = AnalysisPipelineConfig(
            plugins=["groket.analysis.plugins.engine.analyzer:EngineDetectorAnalyzer"]
        )
        svc = AnalysisService(work_dir, config=cfg)
        ids = [p.id for p in svc.list_plugins()]
        assert "engine" in ids
        assert "engine" in svc.enabled_ids

    def test_deferred_plugin_runs_second_with_prior_findings(
        self,
        work_dir,
        session_dir,
    ):
        """External plugins set AnalyzerInfo.defer; they get prior_findings."""

        class _Deferred:
            def __init__(self) -> None:
                self.seen_prior: list | None = None

            @property
            def info(self) -> AnalyzerInfo:
                return AnalyzerInfo(
                    id="deferred-ext",
                    name="Deferred",
                    description="test",
                    defer=True,
                )

            def analyze(
                self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]
            ) -> AnalysisResult:
                self.seen_prior = list(kwargs.get("prior_findings") or [])
                return AnalysisResult(
                    session_id=session_dir.name,
                    session_dir=str(session_dir),
                    analyzer_id="deferred-ext",
                    ok=True,
                    summary=f"prior={len(self.seen_prior)}",
                )

        plugin = _Deferred()
        register_analyzer(plugin)
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            enabled_ids={"basic", "engine", "deferred-ext"},
        )
        # Deferred plugins only execute on force (auto path is cache-only).
        results = svc.analyze_all(session_dir, force=True)
        assert "deferred-ext" in results
        assert plugin.seen_prior is not None

    def test_caching_saves_and_reuses(self, work_dir, session_dir):
        """Completed sessions get cached; second call returns cached result."""
        cache_root = work_dir / "cache"
        register_analyzer(_TestAnalyzer("cache-test"))
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=cache_root,
            enabled_ids={"basic", "engine", "cache-test"},
        )
        # First call — fresh analysis
        r1 = svc.analyze_all(session_dir)
        assert "cache-test" in r1
        assert r1["cache-test"].finding_count == 1
        # Cache file should exist
        cached = cache_root / "analysis" / session_dir.name / "cache-test.json"
        assert cached.is_file()
        # Second call — should hit cache (same version, same mtime)
        r2 = svc.analyze_all(session_dir)
        assert r2["cache-test"].finding_count == 1

    def test_deferred_stale_cache_not_auto_rerun(self, work_dir, session_dir):
        """Version-bumped deferred plugins keep disk results until force."""
        from groket.analysis._cache import save_cached_result

        cache_root = work_dir / "cache"
        runs = {"n": 0}

        class _DeferredV2:
            @property
            def info(self) -> AnalyzerInfo:
                return AnalyzerInfo(
                    id="deferred-llm",
                    name="LLM",
                    version="2",
                    defer=True,
                    optional=True,
                )

            def analyze(
                self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]
            ) -> AnalysisResult:
                runs["n"] += 1
                return AnalysisResult(
                    session_id=session_dir.name,
                    session_dir=str(session_dir),
                    analyzer_id="deferred-llm",
                    ok=True,
                    summary="fresh-v2",
                )

        register_analyzer(_DeferredV2())
        save_cached_result(
            cache_root,
            session_dir,
            "deferred-llm",
            "1",  # older plugin version on disk
            AnalysisResult(
                session_id=session_dir.name,
                session_dir=str(session_dir),
                analyzer_id="deferred-llm",
                ok=True,
                summary="stale-v1",
            ),
        )
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=cache_root,
            enabled_ids={"basic", "engine", "deferred-llm"},
        )
        r1 = svc.analyze_all(session_dir, force=False)
        assert r1["deferred-llm"].summary == "stale-v1"
        assert runs["n"] == 0
        r2 = svc.analyze_all(session_dir, force=True)
        assert r2["deferred-llm"].summary == "fresh-v2"
        assert runs["n"] == 1

    def test_deferred_never_auto_runs_without_cache(self, work_dir, session_dir):
        """Missing deferred cache must not invoke LLM on auto path."""
        runs = {"n": 0}

        class _DeferredCold:
            @property
            def info(self) -> AnalyzerInfo:
                return AnalyzerInfo(
                    id="deferred-cold",
                    name="LLM",
                    version="1",
                    defer=True,
                    optional=True,
                )

            def analyze(
                self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]
            ) -> AnalysisResult:
                runs["n"] += 1
                return AnalysisResult(
                    session_id=session_dir.name,
                    analyzer_id="deferred-cold",
                    ok=True,
                    summary="ran",
                )

        register_analyzer(_DeferredCold())
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=work_dir / "cache",
            enabled_ids={"basic", "engine", "deferred-cold"},
        )
        r = svc.analyze_all(session_dir, force=False)
        assert runs["n"] == 0
        assert r["deferred-cold"].extras.get("skipped_deferred") is True
        r2 = svc.analyze_all(session_dir, force=True)
        assert runs["n"] == 1
        assert r2["deferred-cold"].summary == "ran"

    def test_load_cached_all_without_running(self, work_dir, session_dir):
        """load_cached_all returns disk cache only and never invokes analyzers."""
        cache_root = work_dir / "cache"
        register_analyzer(_TestAnalyzer("cache-only"))
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=cache_root,
            enabled_ids={"basic", "engine", "cache-only"},
        )
        assert svc.load_cached_all(session_dir) == {}
        svc.analyze_all(session_dir)
        cached = svc.load_cached_all(session_dir)
        assert "cache-only" in cached
        assert cached["cache-only"].ok

    def test_load_cached_all_no_cache_root(self, work_dir, session_dir):
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig(), cache_root=None)
        assert svc.load_cached_all(session_dir) == {}

    def test_no_cache_for_incomplete_session(self, work_dir: Path, tmp_path: Path) -> None:
        """Sessions without turn_ended should not be cached."""
        cache_root = work_dir / "cache"
        sd = tmp_path / "incomplete-session"
        sd.mkdir()
        (sd / "summary.json").write_text('{"info": {}}')
        (sd / "updates.jsonl").write_text("")
        # No events.jsonl → incomplete
        register_analyzer(_TestAnalyzer("nc-test"))
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            cache_root=cache_root,
            enabled_ids={"basic", "engine", "nc-test"},
        )
        svc.analyze_all(sd)
        cached = cache_root / "analysis" / sd.name / "nc-test.json"
        assert not cached.exists()


class TestSessionIsComplete:
    def test_no_events_file(self, tmp_path: Path) -> None:
        from groket.analysis.service import _session_is_complete

        sd = tmp_path / "s"
        sd.mkdir()
        assert _session_is_complete(sd) is False

    def test_with_turn_ended(self, tmp_path: Path) -> None:
        import json

        from groket.analysis.service import _session_is_complete

        sd = tmp_path / "s"
        sd.mkdir()
        events = [
            {"type": "turn_started"},
            {"type": "turn_ended", "outcome": "success"},
        ]
        (sd / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        assert _session_is_complete(sd) is True

    def test_without_turn_ended(self, tmp_path: Path) -> None:
        import json

        from groket.analysis.service import _session_is_complete

        sd = tmp_path / "s"
        sd.mkdir()
        (sd / "events.jsonl").write_text(json.dumps({"type": "turn_started"}) + "\n")
        assert _session_is_complete(sd) is False

    def test_corrupt_events_line(self, tmp_path: Path) -> None:
        import json

        from groket.analysis.service import _session_is_complete

        sd = tmp_path / "s"
        sd.mkdir()
        (sd / "events.jsonl").write_text("not json\n" + json.dumps({"type": "turn_ended"}) + "\n")
        assert _session_is_complete(sd) is True


class TestGetAnalysisService:
    def test_singleton(self, work_dir: Path, monkeypatch: object) -> None:
        import groket.analysis.service as svc_mod

        svc_mod._service = None
        monkeypatch.setattr("groket.analysis.service.default_work_dir", lambda: work_dir)  # type: ignore[union-attr]  # monkeypatch string target
        monkeypatch.setattr(
            "groket.analysis.service.analysis_cache_dir", lambda: work_dir / "cache"
        )  # type: ignore[union-attr]  # monkeypatch string target
        try:
            from groket.analysis.service import get_analysis_service

            s1 = get_analysis_service()
            s2 = get_analysis_service()
            assert s1 is s2
        finally:
            svc_mod._service = None

    def test_set_analysis_service(self, work_dir: Path) -> None:
        import groket.analysis.service as svc_mod

        svc_mod._service = None
        try:
            from groket.analysis.service import get_analysis_service, set_analysis_service

            custom = AnalysisService(work_dir, config=AnalysisPipelineConfig())
            set_analysis_service(custom)
            assert get_analysis_service() is custom
        finally:
            svc_mod._service = None

    def test_get_with_traces(self, work_dir: Path, monkeypatch: object) -> None:
        import groket.analysis.service as svc_mod

        svc_mod._service = None
        monkeypatch.setattr("groket.analysis.service.default_work_dir", lambda: work_dir)  # type: ignore[union-attr]  # monkeypatch string target
        monkeypatch.setattr(
            "groket.analysis.service.analysis_cache_dir", lambda: work_dir / "cache"
        )  # type: ignore[union-attr]  # monkeypatch string target
        try:
            from groket.analysis.service import get_analysis_service

            s1 = get_analysis_service(work_dir)
            s2 = get_analysis_service(traces=work_dir / "new_traces")
            assert s1 is s2
            assert s2.traces == work_dir / "new_traces"
        finally:
            svc_mod._service = None


class TestAnalysisServiceEdge:
    def test_list_plugins_empty_enabled(self, work_dir: Path) -> None:
        """With no enabled ids, returns noop info."""
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            enabled_ids=set(),
        )
        plugins = svc.list_plugins()
        assert any(p.id == "noop" for p in plugins)

    def test_run_one_not_enabled(self, work_dir: Path, session_dir: Path) -> None:
        """_run_one for a disabled plugin returns error result."""
        svc = AnalysisService(
            work_dir,
            config=AnalysisPipelineConfig(),
            enabled_ids={"basic"},
        )
        from groket.analysis.base import AnalyzerInfo

        info = AnalyzerInfo(id="disabled_one", name="Disabled")
        result = svc._run_one(info, session_dir, {})
        assert result.ok is False
        assert "not enabled" in (result.error or "")

    def test_analyze_session_default_basic(self, work_dir: Path, session_dir: Path) -> None:
        """analyze_session defaults to 'basic' analyzer."""
        svc = AnalysisService(work_dir, config=AnalysisPipelineConfig())
        result = svc.analyze_session(session_dir)
        assert result.analyzer_id == "basic"

    def test_load_flags_exception_returns_empty(self, work_dir: Path, tmp_path: Path) -> None:
        """_load_flags returns [] on exception."""
        flags = AnalysisService._load_flags(tmp_path / "nonexistent")
        assert flags == []
