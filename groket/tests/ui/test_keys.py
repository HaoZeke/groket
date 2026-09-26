"""Canonical keyboard-shortcut labels (Ctrl+S, not ^s or ⌘⇧)."""

from __future__ import annotations

from groket.ui.keys import format_key_chord


def test_format_key_chord_modifiers_and_letters() -> None:
    assert format_key_chord("ctrl+s") == "Ctrl+S"
    assert format_key_chord("ctrl+shift+c") == "Ctrl+Shift+C"
    assert format_key_chord("ctrl+p") == "Ctrl+P"
    assert format_key_chord("ctrl+enter,ctrl+j") == "Ctrl+Enter / Ctrl+J"
    assert format_key_chord("f5") == "F5"
    assert format_key_chord("escape") == "Esc"
    assert format_key_chord("esc") == "Esc"
    assert format_key_chord("space") == "Space"
    assert format_key_chord("slash") == "/"
    assert format_key_chord("left_square_bracket") == "["
    assert format_key_chord("") == "?"


def test_format_key_chord_bare_letters_keep_case() -> None:
    assert format_key_chord("s") == "s"
    assert format_key_chord("S") == "S"
    assert format_key_chord("q") == "q"


def test_format_key_chord_cmd_and_super() -> None:
    assert format_key_chord("cmd+shift+g") == "Cmd+Shift+G"
    assert format_key_chord("super+shift+g") == "Super+Shift+G"
