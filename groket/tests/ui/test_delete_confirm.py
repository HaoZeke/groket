"""Unit tests for double-press delete helper."""

from __future__ import annotations

from groket.ui.delete_confirm import second_press_armed


def test_first_press_arms_pending() -> None:
    commit, pending = second_press_armed(None, ["a", "b"])
    assert commit is False
    assert pending == ["a", "b"]


def test_second_press_same_targets_commits() -> None:
    commit, pending = second_press_armed(["a", "b"], ["b", "a"])
    assert commit is True
    assert pending == []


def test_changed_selection_rearms() -> None:
    commit, pending = second_press_armed(["a"], ["a", "b"])
    assert commit is False
    assert pending == ["a", "b"]
