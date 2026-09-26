"""Static content that supports Textual text selection and plain-text copy.

Detail panes often hold Markdown / Syntax / Group renderables. Textual's
default :meth:`Widget.get_selection` only extracts from ``Text`` / ``Content``,
so mouse selection can fail for rich bodies. This widget keeps a plain-text
cache for clipboard yank and selection fallback, while **displaying** the
original renderable so layout reflows and content is not pre-wrapped/clipped
at a stale width.

Yank uses a **full** plain extract (no pane-width crop of long lines). Selection
coordinates use a **layout** plain extract at the widget width so drag offsets
match the screen.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.protocol import is_renderable
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.content import Content
from textual.selection import Selection
from textual.visual import VisualType
from textual.widgets import Static

# Cap console width for full yank of non-source renderables. Very large widths
# make Rich pad Markdown blank/code lines with spaces (width-long runs) — bad
# for clipboard. Prefer Markdown.markup / Syntax.code instead of huge consoles.
_FULL_YANK_WIDTH = 240


def _rstrip_lines(plain: str) -> str:
    """Drop trailing spaces Rich adds when padding to console width."""
    if not plain:
        return ""
    return "\n".join(line.rstrip() for line in plain.splitlines()).rstrip("\n")


def materialize_selectable(
    renderable: object, *, width: int = 100
) -> tuple[str | Text | Content, str]:
    """Turn a display renderable into selectable Text (or str) + plain cache.

    Used for the plain-text cache / selection fallback — not required for
    on-screen display (prefer the original Markdown/Syntax/Group there).

    :param renderable: String, Rich text, Markdown/Syntax/Group, or similar.
    :param width: Wrap width for plain extraction layout.
    :returns: ``(visual_text, plain_text)``.
    """
    if renderable is None:
        return "", ""
    if isinstance(renderable, str):
        return renderable, renderable
    if isinstance(renderable, Text):
        plain = _rstrip_lines(renderable.plain)
        return renderable, plain
    if isinstance(renderable, Content):
        plain = _rstrip_lines(renderable.plain)
        return renderable, plain
    # Unwrap layout chrome so we can recover Markdown source / Syntax code.
    if isinstance(renderable, Padding):
        return materialize_selectable(renderable.renderable, width=width)
    # Prefer raw source — Console.print(Markdown) pads lines to *width* with
    # spaces (catastrophic at large widths); Syntax crops to width.
    if isinstance(renderable, Markdown):
        markup = str(getattr(renderable, "markup", None) or "")
        if markup:
            return Text(markup), markup
    if isinstance(renderable, Syntax):
        code = renderable.code or ""
        return Text(code), code
    if isinstance(renderable, Group):
        plains: list[str] = []
        for child in getattr(renderable, "renderables", ()) or ():
            _vis, plain = materialize_selectable(child, width=width)
            if plain:
                plains.append(plain)
        joined = "\n".join(plains)
        return Text(joined), joined
    if not is_renderable(renderable):
        s = str(renderable)
        return s, s

    w = max(40, min(int(width) or 100, 500))
    # Capture to a buffer (not the real TTY). Large height avoids soft-clipping
    # long bodies to the terminal row count.
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=w,
        height=max(10_000, w),
        soft_wrap=True,
        legacy_windows=False,
    )
    try:
        console.print(renderable)
    except Exception:
        s = str(renderable)
        return s, s
    plain = _rstrip_lines(buf.getvalue())
    if not plain:
        s = str(renderable)
        return s, s
    # Text without ANSI — selection extract uses plain coordinates.
    return Text(plain), plain


def plain_from_renderable(renderable: object, *, width: int = 100, full: bool = False) -> str:
    """Best-effort plain text (clipboard / tests).

    :param renderable: String, Rich text, or other console-renderable.
    :param width: Console width for wrapping (layout / selection coords).
    :param full: When True, use a wider extract for non-source renderables.
        Markdown/Syntax still prefer raw ``.markup`` / ``.code`` (no pad/crop).
    :returns: Plain text without ANSI / without width-padding spaces.
    """
    w = max(40, int(width) or 100)
    if full:
        w = max(w, _FULL_YANK_WIDTH)
    _visual, plain = materialize_selectable(renderable, width=w)
    return plain


class SelectableStatic(Static):
    """:class:`~textual.widgets.Static` with reliable text selection and yank.

    Displays the original rich renderable (so the detail pane reflows and is
    not pre-truncated). Maintains plain-text caches for :meth:`get_selection`
    (layout width) and :meth:`get_plain_text` (full yank, no line crop).

    **Contract:** any body content a human may want to extract from the TUI
    must use this widget (not plain ``Static``). See AGENTS.md §6.5a.
    Focusable so Tab can land on Report sub-panes (and other bodies) for ``y``.
    """

    ALLOW_SELECT = True
    can_focus = True

    def __init__(self, content: VisualType = "", **kwargs) -> None:  # Textual Static
        super().__init__(content, **kwargs)
        self._source: object = content
        # Layout-width cache for selection coords (eager). Full yank cache is lazy —
        # recomputing a 16k-wide Console extract on every summary/report/detail
        # update made session open and timeline browse feel frozen.
        self._plain_cache: str = plain_from_renderable(content, width=100)
        self._plain_full: str | None = None
        self._materialize_width: int = 100

    def _widget_width(self) -> int:
        try:
            w = int(self.size.width) or 0
            if w < 20:
                parent = self.parent
                # parent may be a plain DOMNode without size (mypy).
                pw = getattr(parent, "size", None) if parent is not None else None
                if pw is not None:
                    w = int(pw.width) or 0
            return max(40, w or 100)
        except Exception:
            return 100

    def _refresh_plain_cache(self) -> None:
        width = self._widget_width()
        self._materialize_width = width
        self._plain_cache = plain_from_renderable(self._source, width=width)
        self._plain_full = None  # invalidate; rebuilt on next get_plain_text

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Update display with the original renderable; refresh plain cache."""
        self._source = content
        # Show Markdown/Syntax/Group as-is — reflows with the pane; no fixed-width bake.
        super().update(content, layout=layout)
        self._refresh_plain_cache()

    def on_resize(self, event: events.Resize) -> None:
        """Recompute plain cache when width changes (selection line offsets)."""
        width = self._widget_width()
        if width == self._materialize_width:
            return
        if self._source is None or self._source == "":
            self._materialize_width = width
            return
        self._refresh_plain_cache()

    def get_plain_text(self) -> str:
        """Return full plain text for clipboard yank (not pane-width cropped).

        Built lazily so display updates (session open, report fill, timeline
        detail) only pay layout-width materialization cost.
        """
        if self._plain_full is None:
            width = self._widget_width()
            self._plain_full = plain_from_renderable(self._source, width=width, full=True)
        return self._plain_full

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract the dragged region; fall back to plain cache for rich bodies.

        Prefer Textual's native extract when the visual is ``Text``/``Content``.
        For Markdown/Syntax/Group, extract from the layout-width plain cache so a
        drag still yields a region (coordinates track the plain wrap width).
        """
        try:
            result = super().get_selection(selection)
        except Exception:
            result = None
        if result is not None:
            text, end = result
            if text:
                return text, end
        plain = self._plain_cache or ""
        if not plain:
            return None
        try:
            extracted = selection.extract(plain)
        except Exception:
            return None
        if not extracted:
            return None
        return extracted, "\n"


def is_extractable_static(widget: object) -> bool:
    """True when *widget* is a :class:`SelectableStatic` body (not chrome).

    Product rule: use :class:`SelectableStatic` for any operator-facing body
    (detail, summary, diff, report sections, …). Keep chrome as plain
    :class:`~textual.widgets.Static` so ``y`` does not yank labels/tips.
    """
    return isinstance(widget, SelectableStatic)
