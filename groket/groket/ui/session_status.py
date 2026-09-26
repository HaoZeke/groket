"""Localize session gate pending labels for TUI chrome."""

from __future__ import annotations

from .i18n import t


def localize_session_pending_label(label: str) -> tuple[str, str]:
    """Map a domain pending label to ``(display text, status_chip kind)``.

    ``session_pending_label`` returns stable keys for shutdown states
    (``ending_done`` / ``ending_last_turn``) and plain English for other
    gate phases.
    """
    key = (label or "").strip()
    if key == "ending_done":
        return t("status-ending-done"), "ending"
    if key == "ending_last_turn":
        return t("status-ending-last-turn"), "ending"
    if key == "turn in progress":
        return t("ui-turn-in-progress"), "running"
    if key.startswith("agent running"):
        return key, "running"
    if key.startswith("awaiting follow-up"):
        return key, "awaiting"
    return key, "ok"
