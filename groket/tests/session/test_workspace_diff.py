"""Tests for workspace diff extraction."""

from __future__ import annotations

import json
from pathlib import Path

from groket.session.workspace_diff import (
    _snap_map,
    _unified_diff,
    format_diff_meta_line,
    load_workspace_diff,
)

# ── _snap_map ────────────────────────────────────────────────────────────


class TestSnapMap:
    def test_dict_value_format(self):
        block = {"f1": {"content": "hello", "path": "/src/f1.py"}}
        result = _snap_map(block)
        assert result == {"src/f1.py": "hello"}

    def test_string_value_format(self):
        block = {"/src/main.py": "print('hi')"}
        result = _snap_map(block)
        assert result == {"src/main.py": "print('hi')"}

    def test_none_input(self):
        assert _snap_map(None) == {}

    def test_dict_without_content_skipped(self):
        block = {"f": {"path": "/x.py"}}
        assert _snap_map(block) == {}


# ── _unified_diff ────────────────────────────────────────────────────────


class TestUnifiedDiff:
    def test_new_file(self):
        diff_text, added, removed = _unified_diff(None, "line1\nline2", "new.py")
        assert "--- /dev/null" in diff_text
        assert "+++ b/new.py" in diff_text
        assert added == 2
        assert removed == 0

    def test_deleted_file(self):
        diff_text, added, removed = _unified_diff("old content", None, "gone.py")
        assert "--- a/gone.py" in diff_text
        assert "+++ /dev/null" in diff_text
        assert added == 0
        assert removed == 1

    def test_no_change(self):
        diff_text, added, removed = _unified_diff("same", "same", "f.py")
        assert "(no textual diff for f.py)" in diff_text
        assert added == 0
        assert removed == 0


# ── format_diff_meta_line ────────────────────────────────────────────────


class TestFormatDiffMetaLine:
    def test_rewind_points_source(self):
        meta = {
            "source": "rewind_points",
            "files_changed": 3,
            "lines_added": 10,
            "lines_removed": 5,
        }
        assert format_diff_meta_line(meta) == "rewind: 3 files +10/-5"

    def test_search_replace_source(self):
        meta = {
            "source": "search_replace",
            "files_changed": 2,
            "lines_added": 4,
            "lines_removed": 1,
        }
        assert format_diff_meta_line(meta) == "~2 search_replace edits +4/-1"

    def test_no_source(self):
        assert format_diff_meta_line({"source": None}) == "no diff data"


# ── load_workspace_diff ──────────────────────────────────────────────────


class TestLoadWorkspaceDiff:
    def test_rewind_points_file_changes(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        rp = {
            "file_snapshots": {"app.py": "old line"},
            "after_snapshots": {
                "app.py": "new line",
                "added.py": "brand new",
            },
        }
        (sd / "rewind_points.jsonl").write_text(json.dumps(rp) + "\n")

        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 2  # modified + added
        assert meta["lines_added"] > 0
        assert "app.py" in body
        assert "added.py" in body

    def test_rewind_points_deleted_file(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        rp = {
            "file_snapshots": {"gone.py": "will be removed"},
            "after_snapshots": {},
        }
        (sd / "rewind_points.jsonl").write_text(json.dumps(rp) + "\n")

        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 1
        assert meta["lines_removed"] >= 1
        assert "/dev/null" in body

    def test_search_replace_fallback(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        update = {
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "search_replace",
                    "rawInput": {
                        "file_path": "main.py",
                        "old_string": "foo",
                        "new_string": "bar",
                    },
                }
            }
        }
        (sd / "updates.jsonl").write_text(json.dumps(update) + "\n")

        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "search_replace"
        assert meta["files_changed"] == 1
        assert "main.py" in body

    def test_empty_session(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()

        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None
        assert meta["files_changed"] == 0
        assert "No rewind snapshots" in body

    def test_rewind_points_no_differences(self, tmp_path: Path):
        """Rewind snapshots with identical before/after → no content changes."""
        sd = tmp_path / "session"
        sd.mkdir()
        rp = {
            "file_snapshots": {"same.py": "content"},
            "after_snapshots": {"same.py": "content"},
        }
        (sd / "rewind_points.jsonl").write_text(json.dumps(rp) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 0
        assert "No content differences" in body

    def test_rewind_points_parse_error(self, tmp_path: Path):
        """Corrupt rewind_points.jsonl falls through to search_replace path."""
        sd = tmp_path / "session"
        sd.mkdir()
        (sd / "rewind_points.jsonl").write_text("not-json\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    def test_updates_jsonl_malformed_lines(self, tmp_path: Path):
        """Malformed JSONL lines in updates.jsonl are skipped."""
        sd = tmp_path / "session"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("not json\n{bad}\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    def test_search_replace_target_file_key(self, tmp_path: Path):
        """Fallback rawInput key 'target_file' works."""
        sd = tmp_path / "session"
        sd.mkdir()
        update = {
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "search_replace",
                    "rawInput": {
                        "target_file": "alt.py",
                        "old_string": "old",
                        "new_string": "new",
                    },
                }
            }
        }
        (sd / "updates.jsonl").write_text(json.dumps(update) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "search_replace"
        assert "alt.py" in body

    def test_non_search_replace_update_ignored(self, tmp_path: Path):
        """Updates that are not search_replace are ignored."""
        sd = tmp_path / "session"
        sd.mkdir()
        update = {
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "read_file",
                    "rawInput": {"target_file": "x.py"},
                }
            }
        }
        (sd / "updates.jsonl").write_text(json.dumps(update) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    # ── _unified_diff both sides present ────────────────────────────────────

    def test_search_replace_bad_params(self, tmp_path: Path):
        """Updates with non-dict params or update are skipped."""
        sd = tmp_path / "session"
        sd.mkdir()
        updates = [
            {"params": "not-a-dict"},
            {"params": {"update": "not-a-dict"}},
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "title": "search_replace",
                        "rawInput": "not-dict",
                    }
                }
            },
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "title": "search_replace",
                        "rawInput": {"old_string": "", "new_string": ""},
                    }
                }
            },
        ]
        (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    def test_updates_oserror(self, tmp_path: Path):
        """_iter_updates handles OSError by returning empty."""
        from groket.session.workspace_diff import _iter_updates

        sd = tmp_path / "session"
        sd.mkdir()
        # Write a file, then make it unreadable
        f = sd / "updates.jsonl"
        f.write_text("{}\n")
        f.chmod(0o000)
        results = list(_iter_updates(sd))
        f.chmod(0o644)  # restore so tmp_path cleanup works
        assert results == []


class TestUnifiedDiffBothSides:
    def test_modified_file(self):
        diff_text, added, removed = _unified_diff("line1", "line2", "f.py")
        assert "--- a/f.py" in diff_text
        assert "+++ b/f.py" in diff_text
        assert added == 1
        assert removed == 1
