"""Localize session pending / ending labels."""

from __future__ import annotations

from groket.ui.session_status import localize_session_pending_label


def test_localize_ending_keys() -> None:
    text, kind = localize_session_pending_label("ending_done")
    assert "ending" in text.lower()
    assert kind == "ending"
    text, kind = localize_session_pending_label("ending_last_turn")
    assert "last turn" in text.lower()
    assert kind == "ending"


def test_localize_running_and_awaiting() -> None:
    text, kind = localize_session_pending_label("turn in progress")
    assert text
    assert kind == "running"
    _, kind = localize_session_pending_label("agent running (turn 2)")
    assert kind == "running"
    _, kind = localize_session_pending_label("awaiting follow-up (turn 1)")
    assert kind == "awaiting"
    text, kind = localize_session_pending_label("custom")
    assert text == "custom"
    assert kind == "ok"
