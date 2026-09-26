"""Canonical engine smoke: minimal detector + rule → Finding."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_tool_call
from groket.analysis.base import Finding
from groket.engine.detectors import clear_detectors, get_all_detectors
from groket.engine.loader import reload_config
from groket.engine.runner import run_rules


@pytest.fixture()
def install_minimal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point user dirs at a temp install of the minimal example."""
    root = Path(__file__).resolve().parents[2]
    src_det = root / "examples" / "detection" / "minimal" / "detectors" / "demo_detector.py"
    src_rule = root / "examples" / "detection" / "minimal" / "rules" / "demo_rule.yaml"
    det = tmp_path / "detectors"
    rules = tmp_path / "rules"
    det.mkdir()
    rules.mkdir()
    (det / "demo_detector.py").write_text(src_det.read_text(encoding="utf-8"), encoding="utf-8")
    (rules / "demo_rule.yaml").write_text(src_rule.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Force paths module to use tmp — isolate APP_HOME via monkeypatch on path helpers
    plugins = tmp_path / "plugins"
    plugins.mkdir(exist_ok=True)
    for mod in ("groket.paths", "groket.engine.loader"):
        monkeypatch.setattr(f"{mod}.user_detectors_dir", lambda d=det: d)
        monkeypatch.setattr(f"{mod}.user_rules_dir", lambda r=rules: r)
        monkeypatch.setattr(f"{mod}.user_analysis_plugins_dir", lambda p=plugins: p)
    clear_detectors()
    reload_config()
    return tmp_path


def test_minimal_detector_registers(install_minimal: Path) -> None:
    assert "demo_error_shell" in get_all_detectors()


def test_minimal_rule_emits_finding(install_minimal: Path) -> None:
    tc = make_tool_call(
        call_id="c1",
        tool_name="run_terminal_command",
        raw_input={"command": "false"},
        is_error=True,
        result_content="exit 1",
    )
    findings = run_rules([tc], [], rule_ids=["demo-error-shell"])
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, Finding)
    assert f.id == "demo-error-shell"
    assert f.plugin_id == "rules"
    assert "false" in f.title.lower() or "Failed" in f.title


def test_no_error_no_finding(install_minimal: Path) -> None:
    tc = make_tool_call(
        call_id="c1",
        tool_name="run_terminal_command",
        raw_input={"command": "true"},
        is_error=False,
        result_content="ok",
    )
    findings = run_rules([tc], [], rule_ids=["demo-error-shell"])
    assert findings == []
