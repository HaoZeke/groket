"""Tests for analysis pipeline configuration."""

from __future__ import annotations

import json
from pathlib import Path

from groket.analysis.config import (
    AnalysisPipelineConfig,
    load_pipeline_config,
    save_pipeline_config,
)


class TestAnalysisPipelineConfig:
    def test_defaults(self):
        cfg = AnalysisPipelineConfig()
        assert cfg.plugins == []
        assert cfg.auto_analyze_when == "session_complete"
        assert cfg.analysis_workers == 1
        assert cfg.live_refresh_workers == 1

    def test_from_dict(self):
        cfg = AnalysisPipelineConfig.from_dict(
            {
                "plugins": ["mod.a", "mod.b"],
                "auto_analyze_when": "never",
            }
        )
        assert len(cfg.plugins) == 2
        assert cfg.auto_analyze_when == "never"

    def test_from_dict_none(self):
        cfg = AnalysisPipelineConfig.from_dict(None)
        assert cfg.plugins == []

    def test_from_dict_empty(self):
        cfg = AnalysisPipelineConfig.from_dict({})
        assert cfg.plugins == []

    def test_from_dict_filters_bad_plugins(self):
        cfg = AnalysisPipelineConfig.from_dict(
            {
                "plugins": ["valid:func", 123, None, "", "  "],
            }
        )
        assert cfg.plugins == ["valid:func"]

    def test_from_dict_ignores_unknown_keys(self):
        cfg = AnalysisPipelineConfig.from_dict(
            {
                "plugins": ["a:b"],
                "force_on_refresh": True,
            }
        )
        assert cfg.plugins == ["a:b"]
        assert not hasattr(cfg, "force_on_refresh")

    def test_to_dict_roundtrip(self):
        cfg = AnalysisPipelineConfig(
            plugins=["a:b"],
            auto_analyze_when="never",
            analysis_workers=2,
        )
        d = cfg.to_dict()
        restored = AnalysisPipelineConfig.from_dict(d)
        assert restored.plugins == cfg.plugins
        assert restored.auto_analyze_when == "never"
        assert restored.analysis_workers == 2


class TestLoadPipelineConfig:
    def test_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path / "empty")
        cfg = load_pipeline_config(tmp_path)
        assert cfg.plugins == []

    def test_load_from_config_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path / "empty")
        config_data = {
            "analysis": {
                "plugins": ["groket.analysis.plugins.engine.analyzer:EngineDetectorAnalyzer"],
            }
        }
        (tmp_path / "config.json").write_text(json.dumps(config_data))
        cfg = load_pipeline_config(tmp_path)
        assert len(cfg.plugins) == 1

    def test_app_home_takes_precedence(self, tmp_path, monkeypatch):
        """App-home config wins over work_dir config."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text(
            json.dumps(
                {
                    "analysis": {"plugins": ["home:plugin"]},
                }
            )
        )
        monkeypatch.setattr("groket.paths.APP_HOME", home)
        work = tmp_path / "work"
        work.mkdir()
        (work / "config.json").write_text(
            json.dumps(
                {
                    "analysis": {"plugins": ["work:plugin"]},
                }
            )
        )
        cfg = load_pipeline_config(work)
        assert cfg.plugins == ["home:plugin"]

    def test_explicit_config_path(self, tmp_path):
        config_file = tmp_path / "custom.json"
        config_file.write_text(
            json.dumps({"analysis": {"plugins": ["x:y"], "auto_analyze_when": "never"}})
        )
        cfg = load_pipeline_config(config_path=config_file)
        assert cfg.plugins == ["x:y"]
        assert cfg.auto_analyze_when == "never"

    def test_malformed_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path / "empty")
        (tmp_path / "config.json").write_text("not json {{{")
        cfg = load_pipeline_config(tmp_path)
        # Should fall back to defaults
        assert cfg.plugins == []


class TestSavePipelineConfig:
    def test_save_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path)
        cfg = AnalysisPipelineConfig(plugins=["a:b"])
        save_pipeline_config(cfg=cfg)
        assert (tmp_path / "config.json").exists()
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["analysis"]["plugins"] == ["a:b"]

    def test_save_merges_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path)
        existing = {"other_key": "value", "analysis": {"plugins": []}}
        (tmp_path / "config.json").write_text(json.dumps(existing))
        cfg = AnalysisPipelineConfig(plugins=["new:plugin"])
        save_pipeline_config(cfg=cfg)
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["other_key"] == "value"
        assert data["analysis"]["plugins"] == ["new:plugin"]

    def test_save_falls_back_to_work_dir(self, tmp_path: Path, monkeypatch: object) -> None:
        """When app home has no config but work_dir does, update work_dir's."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr("groket.paths.APP_HOME", fake_home)  # type: ignore[union-attr]  # monkeypatch string target
        work = tmp_path / "work"
        work.mkdir()
        existing = {"analysis": {"plugins": []}}
        (work / "config.json").write_text(json.dumps(existing))
        cfg = AnalysisPipelineConfig(plugins=["x:y"])
        save_pipeline_config(work, cfg)
        data = json.loads((work / "config.json").read_text())
        assert data["analysis"]["plugins"] == ["x:y"]

    def test_save_with_no_existing_config(self, tmp_path: Path, monkeypatch: object) -> None:
        """save creates the file from scratch when nothing exists."""
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path)  # type: ignore[union-attr]  # monkeypatch string target
        cfg = AnalysisPipelineConfig(plugins=["a:b"], auto_analyze_when="never")
        save_pipeline_config(cfg=cfg)
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["analysis"]["plugins"] == ["a:b"]

    def test_save_corrupt_existing_config(self, tmp_path: Path, monkeypatch: object) -> None:
        """save handles corrupt existing config.json gracefully."""
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path)  # type: ignore[union-attr]  # monkeypatch string target
        (tmp_path / "config.json").write_text("not json {{{")
        cfg = AnalysisPipelineConfig(plugins=["x:y"])
        save_pipeline_config(cfg=cfg)
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["analysis"]["plugins"] == ["x:y"]

    def test_save_default_config_when_none(self, tmp_path: Path, monkeypatch: object) -> None:
        """save with cfg=None uses defaults."""
        monkeypatch.setattr("groket.paths.APP_HOME", tmp_path)  # type: ignore[union-attr]  # monkeypatch string target
        save_pipeline_config(cfg=None)
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["analysis"]["plugins"] == []
