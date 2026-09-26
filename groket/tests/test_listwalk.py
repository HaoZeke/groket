"""Session-discovery Limited API contract (passes without the .so)."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

from groket import native
from groket.native import (
    WALK_SKIP_DIRS,
    looks_like_session_dir,
    skip_dir_name,
)


def _python_find_sessions(root: Path) -> list[Path]:
    """Pure-Python twin of the C walk (followlinks=False, no descend into hits)."""
    found: list[Path] = []
    if not root.exists():
        return found
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not skip_dir_name(d)]
        path = Path(dirpath)
        if "subagents" in path.parts:
            dirnames.clear()
            continue
        if looks_like_session_dir(path):
            found.append(path)
            dirnames.clear()
    return found


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "traces"
    sess = root / "sess"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    (sess / "workspace" / "fake").mkdir(parents=True)
    (sess / "workspace" / "fake" / "summary.json").write_text("{}", encoding="utf-8")

    junk = root / "workspace" / "junk"
    junk.mkdir(parents=True)
    (junk / "summary.json").write_text("{}", encoding="utf-8")

    plugins = root / "groket-plugins" / "x"
    plugins.mkdir(parents=True)
    (plugins / "summary.json").write_text("{}", encoding="utf-8")

    stage = root / "foo.stage" / "inner"
    stage.mkdir(parents=True)
    (stage / "summary.json").write_text("{}", encoding="utf-8")

    seed = root / ".groket-resume-seed" / "seed"
    seed.mkdir(parents=True)
    (seed / "summary.json").write_text("{}", encoding="utf-8")

    child = root / "keep" / "child-sess"
    child.mkdir(parents=True)
    (child / "events.jsonl").write_text("{}\n", encoding="utf-8")

    empty = root / "empty-ev"
    empty.mkdir(parents=True)
    (empty / "events.jsonl").write_text("", encoding="utf-8")

    nested = root / "parent" / "subagents" / "nested"
    nested.mkdir(parents=True)
    (root / "parent" / "summary.json").write_text("{}", encoding="utf-8")
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    return root


def test_listwalk_is_optional_module() -> None:
    assert native.listwalk is None or isinstance(native.listwalk, ModuleType)


def test_walk_skip_dirs_include_workspace_and_resume_seed() -> None:
    assert WALK_SKIP_DIRS == {
        "groket-plugins",
        "groket-skills",
        "subagents",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        ".cache",
        ".tox",
        ".groket-resume-seed",
        ".groket-workspace-seed",
        "workspace",
    }
    assert "workspace" in WALK_SKIP_DIRS
    assert ".groket-resume-seed" in WALK_SKIP_DIRS
    assert skip_dir_name("workspace")
    assert skip_dir_name(".groket-resume-seed")
    assert skip_dir_name("foo.stage")
    assert not skip_dir_name("keep")


def test_looks_like_summary_json(tmp_path: Path) -> None:
    sd = tmp_path / "with-summary"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    assert looks_like_session_dir(sd) is True


def test_looks_like_updates_jsonl(tmp_path: Path) -> None:
    sd = tmp_path / "with-updates"
    sd.mkdir()
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    assert looks_like_session_dir(sd) is True


def test_looks_like_empty_events_false(tmp_path: Path) -> None:
    sd = tmp_path / "empty-events"
    sd.mkdir()
    (sd / "events.jsonl").write_text("", encoding="utf-8")
    assert looks_like_session_dir(sd) is False


def test_looks_like_nonempty_events_true(tmp_path: Path) -> None:
    sd = tmp_path / "live-events"
    sd.mkdir()
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert looks_like_session_dir(sd) is True


def test_looks_like_missing_false(tmp_path: Path) -> None:
    assert looks_like_session_dir(tmp_path / "missing") is False


def test_looks_like_dangling_events_symlink(tmp_path: Path) -> None:
    sd = tmp_path / "dangling"
    sd.mkdir()
    (sd / "events.jsonl").symlink_to(tmp_path / "missing-target")
    assert looks_like_session_dir(sd) is False


def test_python_walk_skips_workspace_and_finds_sessions(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    found = {p.relative_to(root).as_posix() for p in _python_find_sessions(root)}
    assert found == {"sess", "keep/child-sess", "parent"}


def test_native_find_sessions_optional_or_matches_python(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    got = native.find_sessions(root)
    if native.listwalk is None:
        assert got is None
        return
    expected = _python_find_sessions(root)
    assert got is not None
    assert sorted(got) == sorted(expected)
    assert native.listwalk.find_sessions(str(root))
    assert native.listwalk.looks_like_session_dir(str(root / "sess")) is True
    assert native.listwalk.looks_like_session_dir(str(root / "empty-ev")) is False


def test_c_skip_dirs_match_python_walk_skip_dirs() -> None:
    """listwalk.c SKIP_DIRS must match :data:`WALK_SKIP_DIRS`."""
    import re

    src = Path(__file__).resolve().parents[1] / "native" / "listwalk.c"
    text = src.read_text(encoding="utf-8")
    block = text.split("SKIP_DIRS[]", 1)[1].split("};", 1)[0]
    names = set(re.findall(r'"([^"]+)"', block))
    assert names == WALK_SKIP_DIRS
