"""panel_render admonitions and tip boxes."""

from __future__ import annotations

import pytest
from groket.ui import prefs
from groket.ui.panel_render import (
    TIP_SURFACE_CLASS,
    TipSurface,
    admonition,
    tip_line,
    tip_surface_content,
)
from rich.text import Text

from .pilot_helpers import assert_rich_contains, rich_plain


def setup_function():
    prefs.invalidate_prefs_cache()
    prefs.set_show_tips(True)


def _line_widths(t: Text) -> list[int]:
    return [len(ln) for ln in t.plain.rstrip("\n").split("\n")]


def test_admonition_box_lines_same_width_short():
    for kind in ("tip", "info", "note", "warning", "danger", "success"):
        t = admonition("Press `s` or `space` to select", kind=kind)
        widths = _line_widths(t)
        assert len(widths) == 3
        assert len(set(widths)) == 1, (kind, widths)


def test_admonition_box_lines_same_width_long_keys():
    """Regression: key-chip padding used to break width via strip() vs full body."""
    msg = (
        "`ctrl+enter` launch · `ctrl+s` save · `[` `]` panes · `j` jobs · `p` personas · `esc` back"
    )
    t = tip_line(msg)
    widths = _line_widths(t)
    assert len(set(widths)) == 1, widths
    assert "┌" in t.plain and "┘" in t.plain


def test_tip_line_append_text():
    acc = Text()
    acc.append_text(tip_line("Use Filter above"))
    assert "┌" in acc.plain and "┘" in acc.plain


def test_tips_off_empty():
    prefs.set_show_tips(False)
    assert tip_line("hello").plain == ""
    prefs.set_show_tips(True)


def test_empty_state_quiet_chrome():
    from groket.ui.panel_render import EMPTY_STATE_CLASS, EmptyState

    empty = EmptyState("No flags yet", id="es")
    assert EMPTY_STATE_CLASS in empty.classes
    assert "No flags yet" in empty._render_body().plain
    assert "┌" not in empty._render_body().plain
    empty.clear_message()
    assert empty._empty_message == ""
    assert "empty-state-hidden" in empty.classes


def test_tip_surface_always_has_class():
    tip = TipSurface("hello", kind="info", classes="extra-layout")
    assert TIP_SURFACE_CLASS in tip.classes
    assert "extra-layout" in tip.classes
    assert tip._tip_message == "hello"
    assert tip._tip_kind == "info"


def test_tip_surface_kinds_share_component():
    """All admonition kinds use the same TipSurface widget class + CSS class."""
    for kind in ("tip", "info", "note", "warning", "danger", "success"):
        tip = TipSurface("msg", kind=kind)
        assert isinstance(tip, TipSurface)
        assert TIP_SURFACE_CLASS in tip.classes
        assert tip._tip_kind == kind
        prefs.set_show_tips(True)
        # Adaptive content (no box art) — used by TipSurface in the TUI.
        body = tip_surface_content("msg", kind=kind)
        assert "msg" in body.plain
        assert "┌" not in body.plain
        # Character-frame admonition (for append_text / unit geometry checks).
        assert "┌" in admonition("msg", kind=kind).plain
        prefs.set_show_tips(False)
        assert tip_surface_content("msg", kind=kind).plain == ""
        assert admonition("msg", kind=kind).plain == ""
        prefs.set_show_tips(True)


def test_tip_surface_content_wraps_safely():
    """Narrow modal regression: adaptive content has no fixed-width frame."""
    msg = "`space` mark models · none marked = each recipe's saved models"
    t = tip_surface_content(msg)
    assert "┌" not in t.plain and "│" not in t.plain
    assert "mark models" in t.plain


def test_tip_surface_set_tip_stores_message():
    tip = TipSurface("a")
    tip.set_tip("b", kind="warning")
    assert tip._tip_message == "b"
    assert tip._tip_kind == "warning"
    tip.clear_message()
    assert tip._tip_message == ""


# ── Rich text helpers ─────────────────────────────────────────────────────

from groket.ui.panel_render import (
    bullet,
    content_block,
    danger_line,
    dim_rule,
    info_line,
    key_chip,
    keys_rich,
    kv_line,
    list_row,
    looks_like_markdown,
    md_content,
    meta_strip,
    note_line,
    panel_group,
    refresh_all_tip_surfaces,
    refresh_tip_surfaces_in,
    section_header,
    shortcut_tip,
    status_chip,
    success_line,
    warning_line,
)
from rich.text import Text as RichText


class TestLooksLikeMarkdown:
    def test_heading(self):
        assert looks_like_markdown("# Title") is True

    def test_fenced_code(self):
        assert looks_like_markdown("some ```code```") is True

    def test_bullet(self):
        assert looks_like_markdown("- item") is True
        assert looks_like_markdown("* item") is True
        assert looks_like_markdown("> quote") is True

    def test_bold(self):
        assert looks_like_markdown("**bold** text") is True

    def test_link(self):
        assert looks_like_markdown("[link](http://x)") is True

    def test_plain_text(self):
        assert looks_like_markdown("just text") is False

    def test_empty(self):
        assert looks_like_markdown("") is False

    def test_newline_heading(self):
        assert looks_like_markdown("abc\n## heading") is True


class TestMdContent:
    def test_empty_shows_placeholder(self):
        r = md_content("")
        assert "empty" in str(r).lower() or isinstance(r, RichText)

    def test_long_text_truncated(self):
        r = md_content("x" * 200_000, max_chars=1000)
        plain = rich_plain(r)
        assert "x" in plain
        assert len(plain) < 200_000

    def test_no_indent(self):
        r = md_content("hello", indent=0)
        assert_rich_contains(r, "hello")


class TestContentBlock:
    def test_markdown_content(self):
        r = content_block("# Title\n\nSome text")
        assert_rich_contains(r, "Title")

    def test_plain_text(self):
        r = content_block("just plain text")
        assert_rich_contains(r, "just plain text")

    def test_long_text_truncated(self):
        r = content_block("x" * 200_000, max_chars=1000)
        plain = rich_plain(r)
        assert "x" in plain
        assert len(plain) < 200_000


class TestSectionHeader:
    def test_contains_title(self):
        t = section_header("My Section")
        assert "My Section" in t.plain

    def test_has_rule(self):
        t = section_header("Test")
        assert "─" in t.plain


class TestKvLine:
    def test_key_and_value(self):
        t = kv_line("Key", "Value")
        assert "Key" in t.plain
        assert "Value" in t.plain


class TestListRow:
    def test_basic(self):
        t = list_row("item")
        assert "item" in t.plain

    def test_with_meta(self):
        t = list_row("item", meta=" (note)")
        assert "(note)" in t.plain


class TestBullet:
    def test_basic(self):
        t = bullet("item")
        assert "item" in t.plain


class TestStatusChip:
    def test_ok(self):
        t = status_chip("PASS", kind="ok")
        assert "PASS" in t.plain

    def test_bad(self):
        t = status_chip("FAIL", kind="bad")
        assert "FAIL" in t.plain

    def test_unknown(self):
        t = status_chip("???", kind="unknown")
        assert "???" in t.plain


class TestKeyChip:
    def test_basic(self):
        t = key_chip("s")
        assert "s" in t.plain

    def test_alias_slash(self):
        t = key_chip("slash")
        assert "/" in t.plain

    def test_ctrl_chord_uses_words(self):
        t = key_chip("ctrl+s")
        assert "Ctrl+S" in t.plain
        assert "^" not in t.plain

    def test_space_named(self):
        t = key_chip("space")
        assert "Space" in t.plain

    def test_empty(self):
        t = key_chip("")
        assert "?" in t.plain


class TestKeysRich:
    def test_inline_keys(self):
        t = keys_rich("`s` select · `space` toggle")
        assert "s" in t.plain
        assert "Space" in t.plain


class TestAdmonitionAliases:
    def test_info_line(self):
        prefs.set_show_tips(True)
        t = info_line("info msg")
        assert "┌" in t.plain

    def test_note_line(self):
        prefs.set_show_tips(True)
        t = note_line("note msg")
        assert "┌" in t.plain

    def test_warning_line(self):
        prefs.set_show_tips(True)
        t = warning_line("warn msg")
        assert "┌" in t.plain

    def test_danger_line(self):
        prefs.set_show_tips(True)
        t = danger_line("danger msg")
        assert "┌" in t.plain

    def test_success_line(self):
        prefs.set_show_tips(True)
        t = success_line("success msg")
        assert "┌" in t.plain

    def test_shortcut_tip(self):
        prefs.set_show_tips(True)
        t = shortcut_tip("`s` select")
        assert "┌" in t.plain


class TestMetaStrip:
    def test_basic(self):
        t = meta_strip(["a", "b", "c"])
        assert "·" in t.plain

    def test_single(self):
        t = meta_strip(["only"])
        assert "only" in t.plain


class TestDimRule:
    def test_returns_rule(self):
        r = dim_rule()
        assert "─" in rich_plain(r) or "─" in str(r) or rich_plain(r) != ""


class TestPanelGroup:
    def test_empty(self):
        r = panel_group()
        assert isinstance(r, RichText)

    def test_single(self):
        t = RichText("hello")
        r = panel_group(t)
        assert r is t

    def test_multiple(self):
        r = panel_group(RichText("a"), RichText("b"))
        plain = rich_plain(r)
        assert "a" in plain and "b" in plain

    def test_none_filtered(self):
        r = panel_group(None, RichText("a"), None)
        assert_rich_contains(r, "a")


class TestRefreshTipSurfaces:
    def test_no_query_returns_zero(self):
        from types import SimpleNamespace

        obj = SimpleNamespace()
        assert refresh_tip_surfaces_in(obj) == 0  # type: ignore[arg-type]  # stub for test

    def test_refresh_all_alias(self):
        from types import SimpleNamespace

        obj = SimpleNamespace()
        assert refresh_all_tip_surfaces(obj) == 0  # type: ignore[arg-type]  # stub for test

    def test_css_class_fallback_path(self):
        """CSS class fallback is used when type query returns no TipSurface."""
        from types import SimpleNamespace

        tip = TipSurface("hello")

        class _FakeQuery:
            """Simulate query that finds zero TipSurface by type, then finds via CSS."""

            def __init__(self):
                self._first = True

            def __call__(self, selector):
                if isinstance(selector, type) and issubclass(selector, TipSurface):
                    # Type query returns nothing → trigger CSS fallback
                    return []
                # CSS class query returns the tip
                return [tip]

        root = SimpleNamespace(query=_FakeQuery())
        n = refresh_tip_surfaces_in(root)  # type: ignore[arg-type]  # stub for test
        assert n == 1

    def test_css_class_fallback_non_tip_with_refresh(self):
        """CSS fallback invokes refresh_tip on non-TipSurface widgets."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        widget = SimpleNamespace(refresh_tip=MagicMock())

        class _FakeQuery:
            def __init__(self):
                self._first = True

            def __call__(self, selector):
                if isinstance(selector, type):
                    return []
                return [widget]

        root = SimpleNamespace(query=_FakeQuery())
        n = refresh_tip_surfaces_in(root)  # type: ignore[arg-type]  # stub for test
        assert n == 1
        widget.refresh_tip.assert_called_once()


class TestMdContentExceptionFallback:
    def test_markdown_parse_exception(self):
        """Markdown parse exception falls back to plain Text."""
        from unittest.mock import patch

        with patch("groket.ui.panel_render.Markdown", side_effect=ValueError("bad")):
            r = md_content("# Title")
            assert_rich_contains(r, "Title")


class TestFooterKeyRichStyle:
    def test_with_mock_app(self):
        """_footer_key_rich_style reads CSS vars from a running app."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        css_vars = {"footer-key-foreground": "#aabbcc"}
        app = SimpleNamespace(
            get_css_variables=lambda: css_vars,
            theme="textual-dark",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert "bold" in result
            assert "#aabbcc" in result

    def test_with_accent_fallback(self):
        """accent CSS var is used when footer-key-foreground is missing."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        app = SimpleNamespace(
            get_css_variables=lambda: {"accent": "#112233"},
            theme="",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert "#112233" in result

    def test_with_primary_fallback(self):
        """primary CSS var is used as final colour fallback."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        app = SimpleNamespace(
            get_css_variables=lambda: {"primary": "#334455"},
            theme="dark",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert "#334455" in result

    def test_no_css_vars(self):
        """_footer_key_rich_style returns bold when no CSS vars are set."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        app = SimpleNamespace(
            get_css_variables=lambda: {},
            theme="some-theme",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert result == "bold"

    def test_exception_returns_bold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_footer_key_rich_style returns bold on app detection exception."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        # Make get_css_variables raise inside the try block
        app = SimpleNamespace(
            get_css_variables=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            theme="dark",
        )

        class _FakeApp:
            @staticmethod
            def get_running_app() -> SimpleNamespace:
                return app

        with patch("textual.app.App", _FakeApp):
            result = _footer_key_rich_style()
            assert result == "bold"


class TestAppendTipBodyEmpty:
    def test_empty_message_returns_early(self):
        """_append_tip_body is a no-op for empty messages."""
        from groket.ui.panel_render import _append_tip_body

        t = Text()
        _append_tip_body(t, "")
        assert t.plain == ""

        _append_tip_body(t, "   ")
        assert t.plain == ""


class TestAdmonitionNewlineBody:
    def test_multiline_message_flattened(self):
        """Multiline admonition message is flattened to a single line."""
        prefs.set_show_tips(True)
        t = admonition("line one\nline two", kind="tip")
        assert "┌" in t.plain
        # Newlines should be flattened to spaces
        assert "line one line two" in t.plain

    def test_prefs_import_exception(self):
        """Admonition renders when prefs import raises."""
        from unittest.mock import patch

        with patch("groket.ui.prefs.show_tips_enabled", side_effect=ImportError):
            t = admonition("msg")
            assert "┌" in t.plain


class TestTipSurfaceContentPrefsException:
    def test_prefs_exception_still_renders(self):
        """tip_surface_content renders when prefs import raises."""
        from unittest.mock import patch

        with patch("groket.ui.prefs.show_tips_enabled", side_effect=ImportError):
            t = tip_surface_content("msg")
            assert "msg" in t.plain


class TestTipSurfaceSetMessage:
    def test_set_message_alias(self):
        """set_message is an alias for set_tip."""
        tip = TipSurface("a")
        tip.set_message("b", kind="danger")
        assert tip._tip_message == "b"
        assert tip._tip_kind == "danger"


class TestTipSurfaceApplyTipContentException:
    def test_apply_tip_content_not_mounted(self):
        """_apply_tip_content handles unmounted widget without crash."""
        tip = TipSurface("msg")
        # _apply_tip_content is called internally; update() will fail since not mounted
        tip._apply_tip_content()
        # Should not raise
