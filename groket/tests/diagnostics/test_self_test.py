"""Host self-test checks (Docker faked; no live daemon required)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from groket.diagnostics.self_test import CheckResult, SelfTestReport, run_self_test


def test_work_dir_writable(tmp_path: Path):
    wd = tmp_path / "groket-home"
    with (
        patch("groket.diagnostics.self_test._check_docker") as d,
        patch("groket.diagnostics.self_test._check_auth_json") as a,
        patch("groket.diagnostics.self_test._check_grok_config") as c,
        patch("groket.diagnostics.self_test._check_grok_cli") as g,
        patch("groket.diagnostics.self_test._check_models_cache") as m,
    ):
        d.return_value = CheckResult("docker", "Docker", True)
        a.return_value = CheckResult("grok_auth", "Auth", True)
        c.return_value = CheckResult("grok_config", "Cfg", True, required=False)
        g.return_value = CheckResult("grok_cli", "CLI", True, required=False)
        m.return_value = CheckResult("models_cache", "Models", True, required=False)
        report = run_self_test(work_dir=wd)
    assert report.ok is True
    assert (wd / "runs").is_dir()
    assert any(c.id == "work_dir" and c.ok for c in report.checks)


def test_auth_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from groket.diagnostics import self_test as st

    r = st._check_auth_json()
    assert r.ok is False
    assert r.required is True


def test_auth_present(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    auth = tmp_path / ".grok" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"accessToken": "x"}), encoding="utf-8")
    from groket.diagnostics import self_test as st

    r = st._check_auth_json()
    assert r.ok is True


def test_auth_bad_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    auth = tmp_path / ".grok" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("not-json", encoding="utf-8")
    from groket.diagnostics import self_test as st

    assert st._check_auth_json().ok is False


def test_report_lines_and_fail():
    rep = SelfTestReport(
        checks=[
            CheckResult("a", "A", True),
            CheckResult("b", "B", False, detail="nope", required=True),
            CheckResult("c", "C", False, required=False),
        ]
    )
    assert rep.ok is False
    assert rep.fail_count == 1
    assert rep.warn_count == 1
    text = "\n".join(rep.lines())
    assert "FAIL" in text
    assert "WARN" in text
    assert CheckResult("x", "X", True).level == "ok"
    assert CheckResult("y", "Y", False, required=False).level == "warn"
    assert CheckResult("z", "Z", False, required=True).level == "error"


def test_docker_ok(tmp_path: Path):
    fake = MagicMock()
    fake.check_docker_available.return_value = True
    with patch("groket.docker.orchestrator.DockerOrchestrator", return_value=fake):
        from groket.diagnostics import self_test as st

        r = st._check_docker(tmp_path)
    assert r.ok is True


def test_docker_unavailable(tmp_path: Path):
    fake = MagicMock()
    fake.check_docker_available.return_value = False
    with patch("groket.docker.orchestrator.DockerOrchestrator", return_value=fake):
        from groket.diagnostics import self_test as st

        r = st._check_docker(tmp_path)
    assert r.ok is False


def test_docker_exception(tmp_path: Path):
    with patch(
        "groket.docker.orchestrator.DockerOrchestrator",
        side_effect=RuntimeError("boom"),
    ):
        from groket.diagnostics import self_test as st

        r = st._check_docker(tmp_path)
    assert r.ok is False
    assert "boom" in r.detail


def test_docker_timeout(tmp_path: Path):
    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return True

    with patch.object(threading, "Thread", FakeThread):
        from groket.diagnostics import self_test as st

        r = st._check_docker(tmp_path)
    assert r.ok is False
    assert "timed out" in r.detail.lower()


def test_grok_config_cli_models(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from groket.diagnostics import self_test as st

    assert st._check_grok_config().ok is False
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "config.toml").write_text("x=1\n", encoding="utf-8")
    assert st._check_grok_config().ok is True

    with patch("groket.diagnostics.self_test.shutil.which", return_value=None):
        assert st._check_grok_cli().ok is False
    with patch("groket.diagnostics.self_test.shutil.which", return_value="/bin/grok"):
        assert st._check_grok_cli().ok is True

    assert st._check_models_cache().ok is False
    (tmp_path / ".grok" / "models_cache.json").write_text("[]", encoding="utf-8")
    assert st._check_models_cache().ok is True


def test_work_dir_not_writable(tmp_path: Path, monkeypatch):
    from groket.diagnostics import self_test as st

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    real_mkdir = Path.mkdir

    def boom(self, *a, **k):
        if self == blocked or blocked in self.parents or self == blocked / "runs":
            raise OSError("read-only")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", boom)
    r = st._check_work_dir(blocked)
    assert r.ok is False


def test_auth_empty_object(tmp_path: Path, monkeypatch):
    """Empty auth.json object returns False (line 137)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    auth = tmp_path / ".grok" / "auth.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text("{}", encoding="utf-8")
    from groket.diagnostics import self_test as st

    r = st._check_auth_json()
    assert r.ok is False


def test_auth_with_generic_keys(tmp_path: Path, monkeypatch):
    """Auth with non-standard keys shows key count (line 162)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    auth = tmp_path / ".grok" / "auth.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text(json.dumps({"custom_field": "val"}), encoding="utf-8")
    from groket.diagnostics import self_test as st

    r = st._check_auth_json()
    assert r.ok is True
    assert "keys=" in r.detail


def test_grok_config_unreadable(tmp_path: Path, monkeypatch):
    """Unreadable config.toml returns warn level (lines 184-185)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg_dir = tmp_path / ".grok"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "config.toml"
    cfg.write_text("ok", encoding="utf-8")  # exists as file so is_file() → True

    from groket.diagnostics import self_test as st

    # Patch read_text to raise OSError after is_file passes
    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        r = st._check_grok_config()
    assert r.ok is False
    assert r.required is False


def test_models_cache_bad_json(tmp_path: Path, monkeypatch):
    """Bad JSON in models_cache returns warn (lines 266-267)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cache = tmp_path / ".grok" / "models_cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("not-json!", encoding="utf-8")
    from groket.diagnostics import self_test as st

    r = st._check_models_cache()
    assert r.ok is False
