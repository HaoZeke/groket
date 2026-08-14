"""Shared key action catalog: ids, reserved chords, binding/HUD coverage."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from groket.keys.catalog import (
    ACTIONS,
    ACTIONS_BY_ID,
    RESERVED_KEYS,
    ActionScope,
    ActionSurface,
    KeyAction,
    _index_by_id,
    action_by_id,
    chord_is_reserved,
)
from groket.ui import bindings as B
from groket.ui.tab_panes import tab_nav_bindings

_REPO = Path(__file__).resolve().parents[2]
_HELP_RS = _REPO / "groket-hud" / "src" / "help.rs"
_PUSH_ID = re.compile(r'push\(\s*&mut table,\s*"([^"]+)"')

_BINDING_TUPLES: tuple[tuple[object, ...], ...] = (
    B.APP_GLOBAL_PRIORITY,
    B.GLOBAL_ALWAYS,
    B.LIST_SELECT,
    B.LIST_SELECT_ALL,
    B.APP_SESSIONS,
    B.SCREEN_CHROME,
    B.BROWSER,
    B.RUNNER,
    B.RUN_CONFIGS,
    B.CAPABILITY_PICKER,
    B.PERSONAS,
    B.PERSONA_EDITOR,
    B.FORM_SAVE,
    B.RULES,
    B.MODAL_DISMISS,
    B.JOBS_MODAL,
)


def _hud_push_ids(source: str) -> set[str]:
    ids = set(_PUSH_ID.findall(source))
    if 'format!("pane.{n}")' in source:
        ids.update(f"pane.{i}" for i in range(1, 6))
    return ids


def test_catalog_ids_are_globally_unique() -> None:
    ids = [row.id for row in ACTIONS]
    assert len(ids) == len(set(ids))
    assert set(ACTIONS_BY_ID) == set(ids)


def test_catalog_ids_are_dotted() -> None:
    for row in ACTIONS:
        assert "." in row.id, row.id
        assert row.id == row.id.strip()
        assert row.scope in ActionScope
        assert row.surfaces in ActionSurface


def test_reserved_keys_are_not_remappable() -> None:
    assert action_by_id("help.toggle").remappable is False
    assert action_by_id("overlay.hide").remappable is False
    assert action_by_id("session.open").remappable is False
    assert action_by_id("pane.next").remappable is False
    assert action_by_id("pane.prev").remappable is False
    for row in ACTIONS:
        if chord_is_reserved(row.default):
            assert row.remappable is False, row.id


def test_reserved_set_covers_spec_keys() -> None:
    for key in ("escape", "enter", "tab", "shift+tab", "?"):
        assert key in RESERVED_KEYS
        assert chord_is_reserved(key)
    assert chord_is_reserved("esc")
    assert not chord_is_reserved("ctrl+enter,ctrl+j")
    assert not chord_is_reserved("n")
    assert not chord_is_reserved("")


def test_default_chords_match_today() -> None:
    assert action_by_id("session.follow").default == "n"
    assert action_by_id("session.done").default == "e"
    assert action_by_id("edit.copy").default == "y"
    assert action_by_id("help.toggle").default == "?"
    assert action_by_id("list.down").default == "j"
    assert action_by_id("list.up").default == "k"
    assert action_by_id("edit.copy_chord").default == "ctrl+shift+c"


def test_every_tui_binding_has_catalog_id() -> None:
    missing: list[str] = []
    for tup in _BINDING_TUPLES:
        for binding in tup:
            bid = getattr(binding, "id", None)
            if not bid or bid not in ACTIONS_BY_ID:
                missing.append(
                    f"{getattr(binding, 'key', '?')}->{getattr(binding, 'action', '?')}:{bid!r}"
                )
    assert missing == []


def test_binding_spot_check_keys() -> None:
    follow = [b for b in B.APP_SESSIONS if b.id == "session.follow"]
    done = [b for b in B.APP_SESSIONS if b.id == "session.done"]
    copy = [b for b in B.BROWSER if b.id == "edit.copy"]
    help_b = [b for b in B.GLOBAL_ALWAYS if b.id == "help.toggle"]
    assert follow and follow[0].key == "n"
    assert done and done[0].key == "e"
    assert copy and copy[0].key == "y"
    assert help_b and help_b[0].key == "?"


def test_every_hud_push_id_is_in_catalog() -> None:
    source = _HELP_RS.read_text(encoding="utf-8")
    ids = _hud_push_ids(source)
    assert ids, "help.rs should declare push ids"
    missing = sorted(ids - set(ACTIONS_BY_ID))
    assert missing == []


def test_hud_named_ids_present() -> None:
    for action_id in (
        "help.toggle",
        "overlay.hide",
        "session.open",
        "list.down",
        "list.up",
        "search.focus",
        "pane.next",
        "pane.prev",
        "pane.1",
        "pane.5",
        "edit.copy",
        "edit.copy_chord",
        "session.follow",
        "session.done",
        "pane.notes",
        "events.next_turn",
        "events.all_turns",
        "turns.timeline",
    ):
        assert action_id in ACTIONS_BY_ID


def test_list_nav_is_shared_without_tui_binding() -> None:
    down = action_by_id("list.down")
    up = action_by_id("list.up")
    assert down.surfaces is ActionSurface.SHARED
    assert up.surfaces is ActionSurface.SHARED
    assert down.tui_action is None
    assert up.tui_action is None
    assert not any(getattr(b, "id", None) == "list.down" for tup in _BINDING_TUPLES for b in tup)


def test_tab_nav_bindings_use_catalog_ids() -> None:
    nav = tab_nav_bindings(9)
    assert {b.id for b in nav} == {
        "app.pane.prev",
        "app.pane.next",
        *(f"app.pane.{i}" for i in range(1, 10)),
    }
    for binding in nav:
        assert binding.id in ACTIONS_BY_ID


def test_action_by_id_roundtrip() -> None:
    row = action_by_id("session.follow")
    assert isinstance(row, KeyAction)
    assert row is ACTIONS_BY_ID["session.follow"]
    with pytest.raises(KeyError):
        action_by_id("not.an.action")


def test_index_rejects_duplicate_ids() -> None:
    row = action_by_id("help.toggle")
    with pytest.raises(ValueError, match="duplicate catalog ids"):
        _index_by_id((row, row))
