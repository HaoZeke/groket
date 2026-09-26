"""Detail view widget for displaying event details (Rich/Markdown/Syntax)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message

from ...analysis.base import Finding
from ...models import Flag, TraceEvent
from ..render_detail import render_event_detail, set_static_renderable
from ..selectable_static import SelectableStatic, plain_from_renderable


class DetailView(VerticalScroll):
    """Shows detailed information about a selected trace event.

    Uses a single :class:`SelectableStatic` child whose content is replaced on
    each selection, avoiding the remove_children/mount race that causes
    'NoneType' render_strips errors in Textual. SelectableStatic enables mouse
    text selection and plain-text clipboard yank for Markdown/Syntax bodies.
    """

    class FlagRequested(Message):
        def __init__(self, event: TraceEvent) -> None:
            super().__init__()
            self.event = event

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_event: TraceEvent | None = None
        self._current_finding: Finding | None = None
        self._current_flag: Flag | None = None
        self._current_duration: float | None = None
        self._paired_call: TraceEvent | None = None
        self._paired_result: TraceEvent | None = None
        self._current_turn_index: int | None = None

    def compose(self) -> ComposeResult:
        yield SelectableStatic("", id="detail-body")

    def show_event(
        self,
        event: TraceEvent,
        finding: Finding | None = None,
        flag: Flag | None = None,
        duration: float | None = None,
        *,
        paired_call: TraceEvent | None = None,
        paired_result: TraceEvent | None = None,
        turn_index: int | None = None,
    ) -> None:
        self._current_event = event
        self._current_finding = finding
        self._current_flag = flag
        self._current_duration = duration
        self._paired_call = paired_call
        self._paired_result = paired_result
        self._current_turn_index = turn_index
        self._refresh_content()

    def _refresh_content(self) -> None:
        ev = self._current_event
        body = self.query_one("#detail-body", SelectableStatic)
        if ev is None:
            body.update("")
            return
        renderable = render_event_detail(
            ev,
            finding=self._current_finding,
            flag=self._current_flag,
            duration=self._current_duration,
            paired_call=self._paired_call,
            paired_result=self._paired_result,
            turn_index=self._current_turn_index,
        )
        set_static_renderable(body, renderable)
        self.scroll_home(animate=False)

    def clear_detail(self) -> None:
        self._current_event = None
        self._current_finding = None
        self._current_flag = None
        self._current_duration = None
        self._paired_call = None
        self._paired_result = None
        self._current_turn_index = None
        self.query_one("#detail-body", SelectableStatic).update("")

    def get_plain_text(self) -> str:
        """Plain text of the current detail body (for clipboard yank).

        Rebuilds the event without display mid-caps so ``y`` is not limited to
        the truncated on-screen tool/message bodies. Falls back to the widget
        full plain cache when no event is loaded.
        """
        ev = self._current_event
        if ev is not None:
            renderable = render_event_detail(
                ev,
                finding=self._current_finding,
                flag=self._current_flag,
                duration=self._current_duration,
                paired_call=self._paired_call,
                paired_result=self._paired_result,
                turn_index=self._current_turn_index,
                truncate=False,
            )
            return plain_from_renderable(renderable, full=True)
        try:
            body = self.query_one("#detail-body", SelectableStatic)
        except Exception:
            return ""
        return body.get_plain_text()
