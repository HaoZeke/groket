"""UI prefs (show_tips) and admonition gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.ui import prefs
from groket.ui.panel_render import tip_line


@pytest.fixture(autouse=True)
def _isolate_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(prefs, "app_config_path", lambda: cfg)
    prefs.invalidate_prefs_cache()
    yield  # type: ignore[misc]  # fixture teardown requires yield
    prefs.invalidate_prefs_cache()


def test_show_tips_default_true() -> None:
    assert prefs.show_tips_enabled() is True
    assert "┌" in tip_line("hello").plain


def test_show_tips_off_hides_admonitions() -> None:
    prefs.set_show_tips(False)
    assert prefs.show_tips_enabled() is False
    assert tip_line("hello").plain == ""


def test_toggle_show_tips() -> None:
    assert prefs.toggle_show_tips() is False
    assert prefs.toggle_show_tips() is True


class TestGetPref:
    def test_default_from_defaults(self) -> None:
        assert prefs.get_pref("show_tips") is True

    def test_explicit_default_overrides(self) -> None:
        result = prefs.get_pref("nonexistent", default=42)
        assert result == 42

    def test_returns_none_for_unknown(self) -> None:
        assert prefs.get_pref("nonexistent") is None


class TestSetPref:
    def test_persists_and_reads_back(self, tmp_path: Path) -> None:
        prefs.set_pref("custom_key", "custom_value")
        prefs.invalidate_prefs_cache()
        assert prefs.get_pref("custom_key") == "custom_value"

    def test_write_failure_still_caches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(prefs, "app_config_path", lambda: Path("/dev/null/nope/config.json"))
        prefs.invalidate_prefs_cache()
        prefs.set_pref("x", "y")
        assert prefs.get_pref("x") == "y"


class TestReadFile:
    def test_invalid_json_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text("not json at all", encoding="utf-8")
        monkeypatch.setattr(prefs, "app_config_path", lambda: cfg)
        prefs.invalidate_prefs_cache()
        assert prefs.get_pref("show_tips") is True

    def test_non_dict_json_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        monkeypatch.setattr(prefs, "app_config_path", lambda: cfg)
        prefs.invalidate_prefs_cache()
        assert prefs.get_pref("show_tips") is True
