"""Grok disk adapter (harness contract over parser)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from groket.harness import (
    GROK_HARNESS_ID,
    discover,
    load_meta,
    looks_like,
    parse_timeline,
    watch_hints,
)
from groket.models import SessionMeta, TraceEvent

_MINIMAL = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "minimal_session"


def _write_summary_session(root: Path, name: str = "sess-1") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(json.dumps({"generated_title": "t"}), encoding="utf-8")
    return sd


def test_harness_id_is_grok() -> None:
    assert GROK_HARNESS_ID == "grok"


def test_discover_finds_summary_json_session(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path)
    found = discover([tmp_path])
    assert sd.resolve() in {p.resolve() for p in found}


def test_discover_host_root_uses_shallow_collector(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = host / "%2Fproj" / "host-sid"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    found = discover([host])
    assert sess.resolve() in {p.resolve() for p in found}
    meta = load_meta(sess)
    assert meta.origin == "host"


def test_looks_like_true_and_false(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert looks_like(sd) is True
    assert looks_like(str(sd)) is True
    assert looks_like(empty) is False
    assert looks_like(tmp_path / "missing") is False


def test_load_meta_returns_session_meta(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "meta-sid")
    meta = load_meta(sd)
    assert isinstance(meta, SessionMeta)
    assert meta.session_id == "meta-sid"
    assert meta.origin == "work"


def test_parse_timeline_minimal_session() -> None:
    assert _MINIMAL.is_dir()
    events = parse_timeline(_MINIMAL)
    assert events
    assert all(isinstance(ev, TraceEvent) for ev in events)


def test_watch_hints_include_updates_jsonl() -> None:
    assert "updates.jsonl" in watch_hints()


def test_importing_grok_adapter_does_not_import_ui() -> None:
    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "groket.harness" or name.startswith("groket.harness.")
    }
    for name in list(saved):
        del sys.modules[name]
    ui_before = {n for n in sys.modules if n == "groket.ui" or n.startswith("groket.ui.")}
    try:
        import groket.harness.grok as grok_mod

        ui_after = {n for n in sys.modules if n == "groket.ui" or n.startswith("groket.ui.")}
        assert ui_after == ui_before
        assert grok_mod.GROK_HARNESS_ID == "grok"
    finally:
        for name in list(sys.modules):
            if name == "groket.harness" or name.startswith("groket.harness."):
                del sys.modules[name]
        sys.modules.update(saved)
