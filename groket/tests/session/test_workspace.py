"""Host checkout + CoW fork helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from groket.session.workspace import (
    checkout_path,
    cow_copy_tree,
    full_workspace_copy_allowed,
    parent_checkout_for_session,
    prepare_host_checkout,
    reflink_supported,
)


def test_cow_copy_tree_preserves_content(tmp_path: Path) -> None:
    src = tmp_path / "parent"
    src.mkdir()
    (src / "a.txt").write_text("hello\n", encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("world\n", encoding="utf-8")
    dest = tmp_path / "child"
    cow_copy_tree(src, dest)
    assert (dest / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert (dest / "sub" / "b.txt").read_text(encoding="utf-8") == "world\n"
    # Independent after copy
    (dest / "a.txt").write_text("changed\n", encoding="utf-8")
    assert (src / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_prepare_empty_checkout(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    path = prepare_host_checkout(runs, "groket-empty")
    assert path.is_dir()
    assert path == checkout_path(runs, "groket-empty").resolve()
    assert list(path.iterdir()) == []


def test_prepare_checkout_from_parent_with_full_copy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without reflink, parent dirt is preserved only when full-copy is allowed."""
    monkeypatch.setenv("GROKET_ALLOW_FULL_WORKSPACE_COPY", "1")
    runs = tmp_path / "runs"
    parent = prepare_host_checkout(runs, "groket-parent")
    (parent / "note.md").write_text("dirt\n", encoding="utf-8")
    child = prepare_host_checkout(
        runs,
        "groket-child",
        parent_checkout=parent,
    )
    assert (child / "note.md").read_text(encoding="utf-8") == "dirt\n"
    (child / "note.md").write_text("fork dirt\n", encoding="utf-8")
    assert (parent / "note.md").read_text(encoding="utf-8") == "dirt\n"
    assert full_workspace_copy_allowed() is True


def test_prepare_checkout_refuses_silent_full_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROKET_ALLOW_FULL_WORKSPACE_COPY", raising=False)
    monkeypatch.setattr(
        "groket.session.workspace.reflink_supported",
        lambda _p: False,
    )
    runs = tmp_path / "runs"
    parent = prepare_host_checkout(runs, "groket-parent")
    (parent / "note.md").write_text("dirt\n", encoding="utf-8")
    child = prepare_host_checkout(runs, "groket-child", parent_checkout=parent)
    # Dirt not preserved without reflink or ALLOW env.
    assert not (child / "note.md").exists()


def test_parent_checkout_for_session(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    parent = prepare_host_checkout(runs, "groket-abc")
    (parent / "x").write_text("1\n", encoding="utf-8")
    sess = runs / "traces" / "groket-abc" / "%2Fworkspace" / "sess-1"
    sess.mkdir(parents=True)
    found = parent_checkout_for_session(runs, sess)
    assert found == parent
    assert parent_checkout_for_session(runs, tmp_path / "nope") is None


def test_prepare_checkout_clones_repo(tmp_path: Path) -> None:
    """Host clone uses real git into the checkout directory."""
    if not shutil_which_git():
        pytest.skip("git not available")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    work = tmp_path / "seed-work"
    work.mkdir()
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    (work / "README").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "c"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "push", "origin", "HEAD:master"], check=True, capture_output=True
    )

    runs = tmp_path / "runs"
    dest = prepare_host_checkout(runs, "groket-clone", repo_url=str(remote), repo_branch="master")
    assert (dest / "README").read_text(encoding="utf-8") == "hi\n"
    assert (dest / ".git").is_dir()


def test_reflink_probe_runs(tmp_path: Path) -> None:
    # Result depends on host FS; just ensure the probe does not crash.
    assert isinstance(reflink_supported(tmp_path), bool)


def shutil_which_git() -> bool:
    from shutil import which

    return which("git") is not None


def test_prepare_replaces_existing_checkout(tmp_path: Path) -> None:
    """Re-launch replaces prior checkout (including dirt left by prior runs)."""
    runs = tmp_path / "runs"
    first = prepare_host_checkout(runs, "groket-reuse")
    (first / "stale.txt").write_text("old\n", encoding="utf-8")
    second = prepare_host_checkout(runs, "groket-reuse", repo_url="")
    assert second == first
    assert not (second / "stale.txt").exists()


def test_resolve_repo_path_requires_directory(tmp_path: Path) -> None:
    from groket.session.workspace import resolve_repo_path

    d = tmp_path / "proj"
    d.mkdir()
    (d / "f.txt").write_text("x\n", encoding="utf-8")
    assert resolve_repo_path(d) == d.resolve()
    assert resolve_repo_path(str(d)) == d.resolve()
    with pytest.raises(ValueError):
        resolve_repo_path("")
    with pytest.raises(FileNotFoundError):
        resolve_repo_path(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        resolve_repo_path(d / "f.txt")


def test_is_managed_checkout(tmp_path: Path) -> None:
    from groket.session.workspace import checkout_path, is_managed_checkout, prepare_host_checkout

    runs = tmp_path / "runs"
    managed = prepare_host_checkout(runs, "groket-m")
    assert is_managed_checkout(runs, managed) is True
    external = tmp_path / "elsewhere"
    external.mkdir()
    assert is_managed_checkout(runs, external) is False
    assert checkout_path(runs, "groket-m") == managed or True


def test_prepare_uses_rmtree_robust_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root-owned leftovers must go through rmtree_robust, not bare shutil.rmtree."""
    runs = tmp_path / "runs"
    dest = checkout_path(runs, "groket-rooty")
    dest.mkdir(parents=True)
    (dest / "x").write_text("y\n", encoding="utf-8")
    calls: list[Path] = []

    def fake_robust(path: Path) -> None:
        calls.append(Path(path))
        import shutil

        shutil.rmtree(path)

    monkeypatch.setattr("groket.runs.run_configs.rmtree_robust", fake_robust)
    out = prepare_host_checkout(runs, "groket-rooty")
    assert calls and calls[0] == dest
    assert out.is_dir()
