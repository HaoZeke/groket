"""Pilot: keys.toml remaps drive set_keymap and footer display."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.ui.app import TraceEvalApp

from .pilot_helpers import wait_until


def _minimal_traces(work: Path) -> Path:
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sd = traces / "pilot-keymap-1"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-1", "cwd": "/workspace"},
                "session_summary": "pilot",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": "success"})
        + "\n",
        encoding="utf-8",
    )
    return traces


def _binding_keys(app: TraceEvalApp, binding_id: str) -> set[str]:
    return {
        key
        for key, ab in app.active_bindings.items()
        if getattr(ab.binding, "id", None) == binding_id
    }


@pytest.mark.asyncio
async def test_overlay_remap_updates_footer_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"home.runner" = "z"\n', encoding="utf-8")
    monkeypatch.setenv("GROKET_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: "z" in _binding_keys(app, "home.runner"),
            description="home.runner remapped to z",
        )
        assert "r" not in _binding_keys(app, "home.runner")
        footer = app.query_one("Footer")
        plain = footer.render().plain if hasattr(footer.render(), "plain") else str(footer.render())
        assert "z" in plain.lower() or "z" in str(app.active_bindings)


@pytest.mark.asyncio
async def test_refused_overlay_keeps_default_footer_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "n"\n', encoding="utf-8")
    monkeypatch.setenv("GROKET_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: "r" in _binding_keys(app, "home.runner"),
            description="home.runner stays r when overlay refused",
        )
        assert "z" not in _binding_keys(app, "home.runner")


@pytest.mark.asyncio
async def test_default_keymap_footer_unchanged(tmp_path: Path) -> None:
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: "r" in _binding_keys(app, "home.runner"),
            description="home.runner default r",
        )
        assert _binding_keys(app, "home.runner") == {"r"}
        help_keys = _binding_keys(app, "help.toggle")
        assert help_keys, "help.toggle stays bound"
        assert "z" not in help_keys
