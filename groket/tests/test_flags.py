"""Tests for user flags (load/save)."""

from __future__ import annotations

import json

from groket.flags import load_flags, save_flags
from groket.models import Flag, FlagVerdict


class TestLoadFlags:
    def test_list_format(self, tmp_path):
        sd = tmp_path / "sess"
        sd.mkdir()
        flags = [
            {
                "event_index": 3,
                "verdict": "bad",
                "description": "wrong tool",
                "event_type": "tool_call",
                "tool_name": "grep",
            },
            {
                "event_index": 7,
                "verdict": "good",
                "description": "nice fix",
            },
        ]
        (sd / "flags.json").write_text(json.dumps(flags))
        result = load_flags(sd)
        assert len(result) == 2
        assert result[0].event_index == 3
        assert result[0].verdict == FlagVerdict.BAD
        assert result[1].verdict == FlagVerdict.GOOD

    def test_dict_format(self, tmp_path):
        sd = tmp_path / "sess"
        sd.mkdir()
        flags = {
            "5": {"verdict": "acceptable", "description": "ok approach"},
        }
        (sd / "flags.json").write_text(json.dumps(flags))
        result = load_flags(sd)
        assert len(result) == 1
        assert result[0].event_index == 5
        assert result[0].verdict == FlagVerdict.ACCEPTABLE

    def test_no_flags_file(self, tmp_path):
        sd = tmp_path / "sess"
        sd.mkdir()
        result = load_flags(sd)
        assert result == []

    def test_malformed_json(self, tmp_path):
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "flags.json").write_text("not json at all")
        result = load_flags(sd)
        assert result == []


class TestSaveFlags:
    def test_roundtrip(self, tmp_path):
        sd = tmp_path / "sess"
        sd.mkdir()
        flags = [
            Flag(event_index=1, verdict=FlagVerdict.BAD, description="error"),
            Flag(event_index=4, verdict=FlagVerdict.GOOD, description="great"),
        ]
        save_flags(sd, flags)
        loaded = load_flags(sd)
        assert len(loaded) == 2
        assert loaded[0].event_index == 1
        assert loaded[1].verdict == FlagVerdict.GOOD

    def test_fallback_on_permission_error(self, tmp_path, monkeypatch):
        sd = tmp_path / "readonly-sess"
        sd.mkdir()
        flags = [Flag(event_index=0, verdict=FlagVerdict.BAD, description="x")]

        # Monkey-patch open to raise PermissionError for the session dir file
        original_open = open

        def patched_open(path, *args, **kwargs):
            if str(path) == str(sd / "flags.json") and "w" in (
                args[0] if args else kwargs.get("mode", "r")
            ):
                raise PermissionError("read-only")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", patched_open)
        import groket.paths as paths_mod

        monkeypatch.setattr(paths_mod, "APP_HOME", tmp_path / "fakehome" / ".groket")

        save_flags(sd, flags)
        fallback = tmp_path / "fakehome" / ".groket" / "flags" / sd.name / "flags.json"
        assert fallback.exists()
