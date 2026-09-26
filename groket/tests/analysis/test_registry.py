"""Tests for analyzer registry."""

from __future__ import annotations

from pathlib import Path
from typing import Unpack

import pytest
from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.analysis.registry import (
    _REGISTRY,
    get_analyzer,
    list_analyzers,
    load_config_plugins,
    register_analyzer,
    set_default_analyzer,
)
from groket.models import Severity


class _DummyAnalyzer:
    """Minimal analyzer for testing."""

    def __init__(self, aid: str = "dummy"):
        self._id = aid

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(id=self._id, name="Dummy", description="test")

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        return AnalysisResult(
            session_id=session_dir.name,
            session_dir=str(session_dir),
            analyzer_id=self._id,
            ok=True,
            findings=[
                Finding(
                    id="dummy-1",
                    plugin_id=self._id,
                    severity=Severity.LOW,
                    title="dummy finding",
                )
            ],
        )


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot and restore registry around each test."""
    saved = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


class TestRegisterAnalyzer:
    def test_register_and_retrieve(self):
        analyzer = _DummyAnalyzer("test-reg")
        register_analyzer(analyzer)
        retrieved = get_analyzer("test-reg")
        assert retrieved is analyzer

    def test_register_as_default(self):
        analyzer = _DummyAnalyzer("test-default")
        register_analyzer(analyzer, default=True)
        default = get_analyzer()
        assert default is analyzer


class TestGetAnalyzer:
    def test_unknown_returns_noop(self):
        result = get_analyzer("nonexistent-xyz")
        assert result.info.id == "noop"

    def test_noop_explicit(self):
        result = get_analyzer("noop")
        assert result.info.id == "noop"


class TestSetDefaultAnalyzer:
    def test_set_valid(self):
        analyzer = _DummyAnalyzer("for-default")
        register_analyzer(analyzer)
        set_default_analyzer("for-default")
        assert get_analyzer().info.id == "for-default"

    def test_set_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown analyzer"):
            set_default_analyzer("nonexistent-xyz-abc")


class TestListAnalyzers:
    def test_includes_noop(self):
        infos = list_analyzers()
        ids = [i.id for i in infos]
        assert "noop" in ids

    def test_includes_registered(self):
        register_analyzer(_DummyAnalyzer("list-test"))
        infos = list_analyzers()
        ids = [i.id for i in infos]
        assert "list-test" in ids


class TestLoadConfigPlugins:
    def test_class_spec(self):

        loaded, failed, registered = load_config_plugins(
            [
                "groket.analysis.plugins.engine.analyzer:EngineDetectorAnalyzer",
            ]
        )
        assert len(loaded) == 1
        assert failed == []
        assert "engine" in registered

    def test_module_only_fails(self):

        loaded, failed, registered = load_config_plugins(
            [
                "groket.analysis.plugins.engine.analyzer",
            ]
        )
        assert loaded == []
        assert len(failed) == 1

    def test_invalid_spec(self):

        loaded, failed, registered = load_config_plugins(
            [
                "nonexistent.module.that.does.not.exist:Nope",
            ]
        )
        assert loaded == []
        assert len(failed) == 1
        assert registered == []

    def test_empty_and_whitespace(self):

        loaded, failed, registered = load_config_plugins(["", "  ", "\t"])
        assert loaded == []
        assert failed == []
        assert registered == []


class TestAnalyzerFromModuleAttr:
    def test_engine_class(self) -> None:
        from groket.analysis.plugins.engine import analyzer as eng
        from groket.analysis.registry import analyzer_from_module_attr

        a = analyzer_from_module_attr(eng, "EngineDetectorAnalyzer")
        assert a.info.id == "engine"

    def test_invalid_class_name(self) -> None:
        from groket.analysis.plugins.engine import analyzer as eng
        from groket.analysis.registry import analyzer_from_module_attr

        with pytest.raises(ValueError, match="invalid analyzer class name"):
            analyzer_from_module_attr(eng, "not-an-identifier")

    def test_missing_class_raises(self) -> None:
        from groket.analysis.plugins.engine import analyzer as eng
        from groket.analysis.registry import analyzer_from_module_attr

        with pytest.raises(ValueError, match="has no analyzer class"):
            analyzer_from_module_attr(eng, "NonexistentClass")


class TestInstantiateAnalyzer:
    def test_instance_passthrough(self) -> None:
        from groket.analysis.registry import instantiate_analyzer

        a = _DummyAnalyzer("pass")
        result = instantiate_analyzer(a)
        assert result is a

    def test_class_instantiation(self) -> None:
        from groket.analysis.registry import instantiate_analyzer

        result = instantiate_analyzer(_DummyAnalyzer)
        assert result.info.id == "dummy"

    def test_not_analyzer_raises(self) -> None:
        from groket.analysis.registry import instantiate_analyzer

        with pytest.raises(TypeError, match="not an Analyzer"):
            instantiate_analyzer("not an analyzer")  # type: ignore[arg-type]  # deliberate wrong type

    def test_class_producing_non_analyzer_raises(self) -> None:
        from groket.analysis.registry import instantiate_analyzer

        class _BadFactory:
            def __call__(self) -> str:
                return "not an analyzer"

            analyze = None  # looks like a class with analyze attr

        with pytest.raises(TypeError):
            instantiate_analyzer(_BadFactory)  # type: ignore[arg-type]  # deliberate wrong type


class TestAnalyzeSessionPath:
    def test_basic(self, tmp_path: Path) -> None:
        from groket.analysis.registry import analyze_session_path

        sd = tmp_path / "test-session"
        sd.mkdir()
        result = analyze_session_path(sd)
        assert result.analyzer_id in ("noop", "basic")

    def test_with_analyzer_id(self, tmp_path: Path) -> None:
        from groket.analysis.registry import analyze_session_path

        register_analyzer(_DummyAnalyzer("path-test"))
        sd = tmp_path / "test-session"
        sd.mkdir()
        result = analyze_session_path(sd, analyzer_id="path-test")
        assert result.analyzer_id == "path-test"


class TestPluginSearchDirs:
    def test_returns_cwd(self) -> None:
        from groket.analysis.registry import _plugin_search_dirs

        dirs = _plugin_search_dirs(None)
        assert str(Path.cwd()) in dirs

    def test_with_config_dir(self, tmp_path: Path) -> None:
        from groket.analysis.registry import _plugin_search_dirs

        plugins = tmp_path / "plugins"
        plugins.mkdir()
        dirs = _plugin_search_dirs(tmp_path)
        assert str(plugins) in dirs

    def test_with_examples_plugins(self, tmp_path: Path) -> None:
        from groket.analysis.registry import _plugin_search_dirs

        examples = tmp_path / "examples" / "plugins"
        examples.mkdir(parents=True)
        dirs = _plugin_search_dirs(tmp_path)
        assert str(examples) in dirs


class TestLoadConfigPluginsEdge:
    def test_empty_module_path(self) -> None:
        loaded, failed, registered = load_config_plugins([":ClassName"])
        assert loaded == []
        assert len(failed) == 1

    def test_empty_class_name(self) -> None:
        loaded, failed, registered = load_config_plugins(["some.module:"])
        assert loaded == []
        assert len(failed) == 1

    def test_user_plugins_win_over_examples(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``~/.groket/plugins`` must shadow repo ``examples/analysis/plugins``."""
        import sys

        import groket.paths as paths

        user = tmp_path / "user_plugins"
        examples = tmp_path / "examples" / "analysis" / "plugins"
        user.mkdir()
        examples.mkdir(parents=True)
        stub = (
            "from groket.analysis.base import AnalyzerInfo, AnalysisResult\n"
            "class A:\n"
            "    @property\n"
            "    def info(self):\n"
            "        return AnalyzerInfo(id='shadow', name={name!r}, version={name!r})\n"
            "    def analyze(self, session_dir, context=None):\n"
            "        return AnalysisResult(session_id='x', session_dir=str(session_dir),\n"
            "                              analyzer_id='shadow')\n"
        )
        (user / "shadow_mod.py").write_text(stub.format(name="user"), encoding="utf-8")
        (examples / "shadow_mod.py").write_text(stub.format(name="examples"), encoding="utf-8")

        monkeypatch.setattr(paths, "user_analysis_plugins_dir", lambda: user)
        monkeypatch.chdir(tmp_path)
        sys.modules.pop("shadow_mod", None)

        loaded, failed, registered = load_config_plugins(
            ["shadow_mod:A"],
            config_dir=tmp_path,
        )
        assert failed == []
        assert loaded == ["shadow_mod:A"]
        assert registered == ["shadow"]
        a = get_analyzer("shadow")
        assert a.info.version == "user"
        assert a.info.name == "user"
        sys.modules.pop("shadow_mod", None)
