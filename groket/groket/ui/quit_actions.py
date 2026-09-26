"""Quit action mixin for screens/modals (avoids circular imports with bindings)."""

from __future__ import annotations


class QuitActions:
    """``q`` / action ``quit`` delegates to the app from any screen or modal.

    Expects to be mixed into a Textual :class:`~textual.dom.DOMNode` (Screen /
    ModalScreen) that provides ``.app``.
    """

    async def action_quit(self) -> None:
        app = getattr(self, "app", None)
        aq = getattr(app, "action_quit", None) if app is not None else None
        if callable(aq):
            result = aq()
            if hasattr(result, "__await__"):
                await result
