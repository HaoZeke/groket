"""Consistent panel rendering: UI chrome vs Markdown payloads.

UI structure (titles, keys, sections, status, tips, lists) uses Rich Text/Rule
only — never markdown strings. No decorative glyphs; section = bold title +
dim rule.

Guidance roles (do not mash them into one cyan box):

| Role | Widget | When |
|------|--------|------|
| Empty pane | :class:`EmptyState` | Section has no data yet (flags, notes, …) |
| Keyboard tutorial | Footer / ``?`` / palette | Not permanent in-pane callouts |
| Rare callout | :class:`TipSurface` | Real warning/info that must interrupt |

:class:`TipSurface` is **not** for empty states or permanent shortcut lessons.
``show_tips`` only affects TipSurface. EmptyState always shows when set.

Markdown payloads (assistant text, plugin reports, MD diffs) use md_content().
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from .keys import format_key_chord

TIP_SURFACE_CLASS = "tip-surface"
EMPTY_STATE_CLASS = "empty-state"


def looks_like_markdown(text: str) -> bool:
    s = (text or "").lstrip()
    if not s:
        return False
    if s.startswith("#") or "```" in s:
        return True
    if s.startswith(("- ", "* ", "> ")):
        return True
    if "**" in s or "__" in s or "](http" in s or ("](/" in s):
        return True
    if "\n## " in s or "\n# " in s:
        return True
    return False


def md_content(text: str, *, max_chars: int = 120000, indent: int = 2) -> RenderableType:
    body = (text or "").strip()
    if not body:
        return Text("(empty)\n", style="dim")
    if len(body) > max_chars:
        half = max_chars // 2
        body = body[:half] + "\n\n…\n\n" + body[-half:]
    rendered: RenderableType
    try:
        rendered = Markdown(body)
    except Exception:
        rendered = Text(body)
    if indent:
        return Padding(rendered, (0, 0, 0, indent))
    return rendered


def content_block(text: str, *, max_chars: int = 120000, indent: int = 2) -> RenderableType:
    if looks_like_markdown(text):
        return md_content(text, max_chars=max_chars, indent=indent)
    body = text or ""
    if len(body) > max_chars:
        body = body[: max_chars // 2] + "\n…\n" + body[-(max_chars // 2) :]
    t = Text(body)
    if not body.endswith("\n"):
        t.append("\n")
    return Padding(t, (0, 0, 0, indent)) if indent else t


def section_header(title: str) -> Text:
    """Bold section title + dim horizontal rule (standard TUI pattern, no icon)."""
    t = Text()
    t.append("\n")
    t.append(f"{title}\n", style="bold")
    t.append("─" * min(40, max(12, len(title) + 4)) + "\n", style="dim")
    return t


def kv_line(key: str, value: str, *, key_width: int = 12) -> Text:
    t = Text()
    t.append(f"  {key:<{key_width}} ", style="dim")
    t.append(f"{value}\n")
    return t


def list_row(label: str, *, meta: str = "") -> Text:
    """Indented list row — no bullet glyph."""
    t = Text()
    t.append("  ")
    t.append(label)
    if meta:
        t.append(meta, style="dim")
    t.append("\n")
    return t


def bullet(label: str, *, detail: str = "") -> Text:
    """Alias for list_row (historical name)."""
    return list_row(label, meta=detail)


def status_chip(label: str, *, kind: str = "unknown") -> Text:
    """Inline status using the shared run/outcome palette."""
    from .styles import status_rich_style

    kind_l = (kind or "unknown").lower()
    if kind_l in ("ok", "success", "completed"):
        style = status_rich_style("completed")
    elif kind_l in ("bad", "error", "failed"):
        style = status_rich_style("failed")
    elif kind_l in ("running", "active"):
        style = status_rich_style("running")
    elif kind_l in ("ending", "finishing"):
        style = status_rich_style("ending")
    elif kind_l in ("pending", "idle", "unknown"):
        style = status_rich_style("idle")
    else:
        style = status_rich_style(kind_l)
    chip = Text()
    chip.append(label, style=style)
    return chip


def _footer_key_rich_style() -> str:
    """Match Textual ``FooterKey`` / ``.footer-key--key`` (bold + accent, not reverse).

    Footer uses ``color: $footer-key-foreground`` (theme accent) and
    ``background: $footer-key-background`` (usually transparent) with ``text-style: bold``
    and horizontal padding — not inverted video.
    """
    try:
        from textual.app import App

        app = getattr(App, "get_running_app", lambda: None)()
        if app is not None:
            vars_ = getattr(app, "get_css_variables", None)
            if callable(vars_):
                css_vars = vars_() or {}
                fg = (
                    css_vars.get("footer-key-foreground")
                    or css_vars.get("accent")
                    or css_vars.get("primary")
                )
                if fg:
                    return f"bold {fg}"
            theme = getattr(app, "theme", None) or ""
            _ = theme
    except Exception:
        pass
    return "bold"


def key_chip(key: str) -> Text:
    """Key label styled like Textual Footer bindings (bold accent, not reverse video).

    Mirrors ``FooterKey`` component: bold + ``$footer-key-foreground``, padding
    ``0 1`` (spaces), transparent background — same look as the app Footer.
    """
    label = (key or "").strip() or "?"
    display = format_key_chord(label)
    style = _footer_key_rich_style()
    t = Text()
    t.append(f" {display} ", style=style)
    return t


def keys_rich(message: str) -> Text:
    """Inline text with `` `key` `` segments as :func:`key_chip` (no tip box).

    Prefer :class:`TipSurface` for user-facing guidance in the TUI.
    """
    t = Text()
    _append_tip_body(t, message, body_style="")
    return t


def _append_tip_body(t: Text, message: str, *, body_style: str = "dim") -> None:
    """Append tip text; segments in `backticks` render as :func:`key_chip`."""
    body = (message or "").strip()
    if not body:
        return
    parts = re.split("(`[^`]+`)", body)
    for part in parts:
        if part.startswith("`") and part.endswith("`") and (len(part) >= 3):
            t.append_text(key_chip(part[1:-1]))
        elif body_style:
            t.append(part, style=body_style)
        else:
            t.append(part)


_ADMONITION_STYLES: dict[str, tuple[str, str, str]] = {
    "tip": (" tip ", "cyan", "bold black on cyan"),
    "info": (" info ", "blue", "bold white on blue"),
    "note": (" note ", "white", "bold black on white"),
    "warning": (" warn ", "yellow", "bold black on yellow"),
    "danger": (" danger ", "red", "bold white on red"),
    "success": (" ok ", "green", "bold black on green"),
}
_ADMONITION_INDENT = "  "
_ADMONITION_MIN_INNER = 28


def _admonition_line_width(plain_line: str) -> int:
    """Cell width for one line of plain text (no ANSI)."""
    return len(plain_line.replace("\n", ""))


def admonition(message: str, *, kind: str = "tip") -> Text:
    """Framed callout as ``Text`` (safe for ``append_text`` / ``Static.update``).

    Kinds: ``tip``, ``info``, ``note``, ``warning``, ``danger``, ``success``.
    Unknown kinds fall back to ``tip``.

    Respects app pref ``show_tips`` (``~/.groket/config.json``): when false,
    returns an empty ``Text`` so learned users can hide callouts globally.

    Geometry uses the **actual** rendered body width (including key-chip spaces).
    Never ``strip()`` for width while still painting unstripped body — that was
    the off-by-1/2 corner bug on long tips.

    Put shortcuts in backticks: ``Press `s` or `space` `` → :func:`key_chip`.
    """
    try:
        from .prefs import show_tips_enabled

        if not show_tips_enabled():
            return Text("")
    except Exception:
        pass
    kind_key = (kind or "tip").strip().lower()
    title_plain, border_style, title_style = _ADMONITION_STYLES.get(
        kind_key, _ADMONITION_STYLES["tip"]
    )
    body = Text()
    _append_tip_body(body, message)
    if "\n" in body.plain:
        flat = Text()
        _append_tip_body(flat, " ".join(message.split()))
        body = flat
    body_w = _admonition_line_width(body.plain)
    content_min = body_w + 2
    inner_w = max(content_min, len(title_plain) + 1, _ADMONITION_MIN_INNER)
    pad_mid = inner_w - body_w - 2
    title_rest = max(0, inner_w - 1 - len(title_plain))
    ind = _ADMONITION_INDENT
    out = Text()
    out.append(f"{ind}┌", style=border_style)
    out.append("─", style=border_style)
    out.append(title_plain, style=title_style)
    if title_rest:
        out.append("─" * title_rest, style=border_style)
    out.append("┐\n", style=border_style)
    out.append(f"{ind}│", style=border_style)
    out.append(" ")
    out.append_text(body)
    if pad_mid:
        out.append(" " * pad_mid)
    out.append(" ")
    out.append("│\n", style=border_style)
    out.append(f"{ind}└", style=border_style)
    out.append("─" * inner_w, style=border_style)
    out.append("┘\n", style=border_style)
    return out


def tip_line(message: str) -> Text:
    """Shortcut / guidance callout — :func:`admonition` with ``kind='tip'``."""
    return admonition(message, kind="tip")


def info_line(message: str) -> Text:
    return admonition(message, kind="info")


def note_line(message: str) -> Text:
    return admonition(message, kind="note")


def warning_line(message: str) -> Text:
    return admonition(message, kind="warning")


def danger_line(message: str) -> Text:
    return admonition(message, kind="danger")


def success_line(message: str) -> Text:
    return admonition(message, kind="success")


def shortcut_tip(message: str) -> Text:
    """Alias for :func:`tip_line` — key-oriented guidance."""
    return tip_line(message)


def tip_surface_content(message: str, *, kind: str = "tip") -> Text:
    """Adaptive tip body for :class:`TipSurface` (no fixed-width box art).

    Character frames from :func:`admonition` break when the widget is narrower
    than the line (modals, half panes). TipSurface uses **CSS borders** instead;
    this function only paints the kind badge + key-chip body, which can wrap.
    """
    try:
        from .prefs import show_tips_enabled

        if not show_tips_enabled():
            return Text("")
    except Exception:
        pass
    if not (message or "").strip():
        return Text("")
    kind_key = (kind or "tip").strip().lower()
    title_plain, _border_style, title_style = _ADMONITION_STYLES.get(
        kind_key, _ADMONITION_STYLES["tip"]
    )
    label = title_plain.strip() or kind_key
    out = Text()
    out.append(f" {label} ", style=title_style)
    out.append(" ")
    _append_tip_body(out, " ".join((message or "").split()))
    return out


class EmptyState(Static):
    """Quiet empty-pane chrome: one dim line, no border, no ``tip`` badge.

    Use when a section has nothing to show yet (no flags, no notes, no analysis).
    Not for keyboard tutorials (Footer / ``?``) and not for warnings
    (:class:`TipSurface`). Always visible when a message is set — independent
    of ``show_tips``.
    """

    DEFAULT_CSS = """
    EmptyState {
        height: auto;
        width: 100%;
        max-width: 100%;
        margin: 0 0 1 0;
        padding: 0 1;
        color: $text-muted;
        border: none;
        background: transparent;
    }
    EmptyState.empty-state-hidden {
        display: none;
        height: 0;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(
        self,
        message: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        name: str | None = None,
    ) -> None:
        self._empty_message = (message or "").strip()
        extra = EMPTY_STATE_CLASS
        if classes:
            for part in str(classes).split():
                if part and part != EMPTY_STATE_CLASS:
                    extra = f"{extra} {part}"
        super().__init__(
            self._render_body(),
            id=id,
            classes=extra,
            disabled=disabled,
            name=name,
        )
        self._sync_hidden()

    def set_message(self, message: str) -> None:
        """Show *message* as dim empty chrome, or hide when empty."""
        self._empty_message = (message or "").strip()
        self._sync_hidden()
        with suppress(Exception):
            self.update(self._render_body())

    def clear_message(self) -> None:
        """Hide this empty state."""
        self.set_message("")

    def _render_body(self) -> Text:
        if not self._empty_message:
            return Text("")
        t = Text()
        _append_tip_body(t, self._empty_message, body_style="dim")
        return t

    def _sync_hidden(self) -> None:
        hidden = not self._empty_message
        with suppress(Exception):
            if hidden:
                self.add_class("empty-state-hidden")
            else:
                self.remove_class("empty-state-hidden")


class TipSurface(Static):
    """Framed callout for **rare** info/warning/success that must interrupt.

    Prefer :class:`EmptyState` for empty panes and Footer / ``?`` for keys.
    Always has CSS class ``tip-surface`` (``TIP_SURFACE_CLASS``). Toggle
    ``show_tips`` by querying this class/widget type — never embed
    :func:`admonition` / :func:`tip_line` inside other ``Static`` / ``Text`` trees.

    Frame is **CSS border** (width-adaptive). Content is :func:`tip_surface_content`.

    Use ``kind=`` for admonition flavour (``tip``, ``info``, ``note``, …).
    """

    DEFAULT_CSS = """
    TipSurface {
        height: auto;
        width: 100%;
        max-width: 100%;
        margin: 0 0 1 0;
        padding: 0 1;
        border: solid cyan;
        background: $boost;
    }
    TipSurface.tip-surface-empty {
        display: none;
        height: 0;
        margin: 0;
        padding: 0;
        border: none;
    }
    """
    _KIND_BORDER: dict[str, str] = {
        "tip": "solid cyan",
        "info": "solid blue",
        "note": "solid white",
        "warning": "solid yellow",
        "danger": "solid red",
        "success": "solid green",
    }

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "tip",
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        name: str | None = None,
    ) -> None:
        self._tip_message = message or ""
        self._tip_kind = (kind or "tip").strip().lower() or "tip"
        extra = TIP_SURFACE_CLASS
        if classes:
            for part in str(classes).split():
                if part and part != TIP_SURFACE_CLASS:
                    extra = f"{extra} {part}"
        super().__init__(
            tip_surface_content(self._tip_message, kind=self._tip_kind),
            id=id,
            classes=extra,
            disabled=disabled,
            name=name,
        )
        self._sync_empty_class()
        self._apply_kind_border()

    def set_message(self, message: str, *, kind: str | None = None) -> None:
        """Update copy/kind and re-render (alias of :meth:`set_tip`)."""
        self.set_tip(message, kind=kind)

    def set_tip(self, message: str, *, kind: str | None = None) -> None:
        """Change message/kind and re-render (respects current show_tips pref)."""
        self._tip_message = message or ""
        if kind is not None:
            self._tip_kind = (kind or "tip").strip().lower() or "tip"
            self._apply_kind_border()
        self.refresh_tip()

    def clear_message(self) -> None:
        """Hide callout content (empty message; still respects show_tips)."""
        self.set_tip("")

    def refresh_tip(self) -> None:
        """Re-paint adaptive content (empty when tips are disabled or message empty)."""
        self._apply_tip_content()

    def _sync_empty_class(self) -> None:
        content = tip_surface_content(self._tip_message, kind=self._tip_kind)
        empty = not (content.plain or "").strip()
        with suppress(Exception):
            if empty:
                self.add_class("tip-surface-empty")
            else:
                self.remove_class("tip-surface-empty")

    def _apply_kind_border(self) -> None:
        border = self._KIND_BORDER.get(self._tip_kind, self._KIND_BORDER["tip"])
        with suppress(Exception):
            self.styles.border = border  # type: ignore[assignment]

    def _content_text(self) -> Text:
        return tip_surface_content(self._tip_message, kind=self._tip_kind)

    def _apply_tip_content(self) -> None:
        content = self._content_text()
        self._sync_empty_class()
        try:
            self.update(content)
        except Exception:
            with suppress(Exception):
                if getattr(self, "is_mounted", False):
                    self.call_after_refresh(self._apply_tip_content_once)

    def _apply_tip_content_once(self) -> None:
        content = self._content_text()
        self._sync_empty_class()
        with suppress(Exception):
            self.update(content)


def refresh_all_tip_surfaces(root: Widget | App) -> int:
    """Refresh every :class:`TipSurface` under *root* (app or screen). Prefer this."""
    return refresh_tip_surfaces_in(root)


def refresh_tip_surfaces_in(widget: Widget | App) -> int:
    """Call :meth:`TipSurface.refresh_tip` on all descendants; return count.

    Discovery is **only** by widget type / ``.tip-surface`` — no screen-specific hooks.
    """
    n = 0
    query = getattr(widget, "query", None)
    if not callable(query):
        return 0
    with suppress(Exception):
        for tip in query(TipSurface):
            tip.refresh_tip()
            n += 1
    if n == 0:
        with suppress(Exception):
            for tip in query(f".{TIP_SURFACE_CLASS}"):
                if isinstance(tip, TipSurface):
                    tip.refresh_tip()
                    n += 1
                elif hasattr(tip, "refresh_tip"):
                    tip.refresh_tip()
                    n += 1
    return n


def meta_strip(parts: list[str]) -> Text:
    """``a  ·  b  ·  c`` with dim separators; callers pass full words."""
    t = Text("  ")
    for i, part in enumerate(parts):
        if i:
            t.append("  ·  ", style="dim")
        t.append(part)
    t.append("\n")
    return t


def dim_rule() -> Rule:
    return Rule(style="dim")


def panel_group(*parts: RenderableType | None) -> RenderableType:
    blocks = [p for p in parts if p is not None]
    if not blocks:
        return Text("")
    if len(blocks) == 1:
        return blocks[0]
    return Group(*blocks)
