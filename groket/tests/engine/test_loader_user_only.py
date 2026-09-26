"""Loader has no built-in rules when user dirs are empty."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.engine.detectors import clear_detectors
from groket.engine.loader import reload_config


def test_empty_user_dirs_no_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d, r, p = tmp_path / "d", tmp_path / "r", tmp_path / "p"
    d.mkdir()
    r.mkdir()
    p.mkdir()
    for mod in ("groket.paths", "groket.engine.loader"):
        monkeypatch.setattr(f"{mod}.user_detectors_dir", lambda x=d: x)
        monkeypatch.setattr(f"{mod}.user_rules_dir", lambda x=r: x)
        monkeypatch.setattr(f"{mod}.user_analysis_plugins_dir", lambda x=p: x)
    clear_detectors()
    cfg = reload_config()
    # Bundled assets are empty stubs — no package catalog.
    assert cfg.rules == {}
