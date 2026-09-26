"""SelectableStatic plain-text cache and partial (line) selection."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.ui.selectable_static import (
    SelectableStatic,
    materialize_selectable,
    plain_from_renderable,
)
from groket.ui.widgets.detail_view import DetailView
from rich.console import Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.selection import Selection


def test_plain_from_renderable_str() -> None:
    assert plain_from_renderable("hello") == "hello"
    assert plain_from_renderable(None) == ""


def test_plain_from_renderable_text() -> None:
    assert plain_from_renderable(Text("styled", style="bold")) == "styled"


def test_plain_from_renderable_markdown() -> None:
    plain = plain_from_renderable(Markdown("**bold** and `code`"))
    assert "bold" in plain
    assert "code" in plain


def test_plain_from_renderable_markdown_fences_not_width_padded() -> None:
    """Rich Console pads Markdown to width with spaces — yank must use source."""
    import re

    from groket.ui.panel_render import content_block

    body = (
        "Where do you see it?\n\n"
        "```bash\n"
        "mkdir -p …/grokos-agent-pi/{src,bin,scripts,test}\n"
        "```\n\n"
        "```text\n"
        "…/grokos-agent-pi/package.json\n"
        '+ "name": "@grokos/agent-pi"\n'
        "```\n"
    )
    for obj in (Markdown(body), content_block(body)):
        plain = plain_from_renderable(obj, width=80, full=True)
        max_spaces = max((len(m.group(0)) for m in re.finditer(r" +", plain)), default=0)
        assert max_spaces < 20, max_spaces
        assert "mkdir -p" in plain
        assert '"name": "@grokos/agent-pi"' in plain
        assert len(plain) < 500


def test_plain_from_renderable_syntax() -> None:
    plain = plain_from_renderable(Syntax("print(1)", "python"))
    assert "print" in plain


def test_plain_from_renderable_syntax_long_line_not_cropped_at_narrow_width() -> None:
    """Rich Syntax crops to console width; yank must use raw .code instead."""
    long_line = "print('" + ("z" * 400) + "END')"
    plain = plain_from_renderable(Syntax(long_line, "python"), width=40)
    assert "END" in plain
    assert len(plain) >= 400
    assert plain_from_renderable(Syntax(long_line, "python"), width=40, full=True).endswith("')")


def test_plain_from_renderable_group_preserves_syntax_code() -> None:
    long_line = "payload_" + ("x" * 300) + "_TAIL"
    group = Group(Text("hdr"), Syntax(long_line, "text"), Text("foot"))
    plain = plain_from_renderable(group, width=30)
    assert "hdr" in plain
    assert "foot" in plain
    assert "_TAIL" in plain


def test_materialize_group_preserves_line_for_partial_extract() -> None:
    """Rich Group → Text so Selection.extract can take one line / a word."""
    body = Group(
        Text("#0 user message"),
        Text(""),
        Text("first line of the prompt"),
        Text("second line ONLY_THIS_LINE"),
        Text("third line"),
    )
    _vis, plain = materialize_selectable(body, width=60)
    lines = plain.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "ONLY_THIS_LINE" in ln)
    full_line = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
    assert "ONLY_THIS_LINE" in full_line.extract(plain)
    assert "first line" not in full_line.extract(plain)
    start = lines[idx].index("ONLY")
    word = Selection(Offset(start, idx), Offset(start + 4, idx))
    assert word.extract(plain) == "ONLY"


class _SelApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("line one\nline two\nline three", id="body")


@pytest.mark.asyncio
async def test_selectable_static_plain_cache_and_partial_selection() -> None:
    app = _SelApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        assert "line one" in body.get_plain_text()
        # First line, columns 0–4 → "line"
        sel = Selection(Offset(0, 0), Offset(4, 0))
        got = body.get_selection(sel)
        assert got is not None
        text, _end = got
        assert text == "line"
        # Whole second line
        sel2 = Selection(Offset(0, 1), Offset(8, 1))
        got2 = body.get_selection(sel2)
        assert got2 is not None
        assert got2[0] == "line two"


class _MdApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("", id="body")


@pytest.mark.asyncio
async def test_selectable_static_markdown_partial_line_selection() -> None:
    """Plain cache tracks Markdown so a line-scoped selection is partial."""
    app = _MdApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        body.update(
            Group(
                Text("meta header"),
                Markdown("## Section\n\nAgent said hello world uniquely"),
            )
        )
        plain = body.get_plain_text()
        assert "hello world uniquely" in plain
        lines = plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "hello world" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        text, end = got
        assert "hello world uniquely" in text
        assert "meta header" not in text
        assert end == "\n"


class _DetailApp(App):
    def compose(self) -> ComposeResult:
        yield DetailView(id="detail")


@pytest.mark.asyncio
async def test_detail_view_get_plain_text_yanks_message() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="please copy this exact phrase XYZ123",
        )
        dv.show_event(ev)
        plain = dv.get_plain_text()
        assert "XYZ123" in plain
        assert "please copy" in plain


@pytest.mark.asyncio
async def test_detail_view_yank_keeps_full_tool_output_past_display_cap() -> None:
    """Display mid-caps tool output; y must still include the whole body."""
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        # Mid-cap keeps head+tail; middle unique marker must survive full yank only.
        middle = "UNIQUE_MIDDLE_CHUNK_991177"
        out = ("A" * 9000) + middle + ("B" * 9000)
        ev = make_trace_event(
            index=1,
            event_type="tool_call",
            tool_name="run_terminal_command",
            content=out,
            raw_input={"command": "echo hi"},
        )
        dv.show_event(ev)
        body = dv.query_one("#detail-body", SelectableStatic)
        display_plain = body.get_plain_text()
        yank = dv.get_plain_text()
        assert middle in yank
        # Display path may omit the middle when mid-truncated.
        from groket.ui.i18n import t

        if t("truncate-marker") in display_plain:
            assert middle not in display_plain


@pytest.mark.asyncio
async def test_detail_view_multiline_message_partial_line() -> None:
    """Multi-line user prompts keep lines distinct for drag-select."""
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="line alpha\nline bravo UNIQUE99\nline charlie",
        )
        dv.show_event(ev)
        body = dv.query_one("#detail-body", SelectableStatic)
        plain = body.get_plain_text()
        assert "UNIQUE99" in plain
        lines = plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "UNIQUE99" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        assert "UNIQUE99" in got[0]
        assert "charlie" not in got[0]


@pytest.mark.asyncio
async def test_detail_view_get_plain_text_empty_when_cleared() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        assert dv.get_plain_text().strip() == ""
        dv.clear_detail()
        assert dv.get_plain_text().strip() == ""


@pytest.mark.asyncio
async def test_action_copy_detail_yanks_full_body() -> None:
    """Browser y (copy_detail) puts detail plain text on the clipboard."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    class _BrowserCopyApp(App):
        def compose(self) -> ComposeResult:
            yield DetailView(id="detail-panel")

    app = _BrowserCopyApp()
    async with app.run_test():
        dv = app.query_one("#detail-panel", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="agent_message_chunk",
            content="clipboard-target-phrase-99",
        )
        dv.show_event(ev)

        copied: list[str] = []
        notes: list[str] = []
        host = SimpleNamespace(
            get_selected_text=lambda: None,
            focused=None,
            _selected_finding=None,
            _active_browser_tab=lambda: "tab-timeline",
            _collect_active_tab_plain_text=lambda: (
                app.query_one("#detail-panel", DetailView).get_plain_text(),
                "detail",
            ),
            app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
            notify=lambda msg, **kwargs: notes.append(str(msg)),
        )
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert copied
        assert "clipboard-target-phrase-99" in copied[0]
        assert notes


def test_action_copy_detail_yanks_report_sections() -> None:
    """On Report tab with no selection, y yanks visible report plain text."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        get_selected_text=lambda: None,
        focused=None,
        _selected_finding=None,
        _active_browser_tab=lambda: "tab-reports",
        _collect_active_tab_plain_text=lambda: (
            "SESSION REPORT\n\nFlags section body",
            "report",
        ),
        app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
    assert copied == ["SESSION REPORT\n\nFlags section body"]
    assert notes


def test_action_copy_detail_yanks_focused_report_pane() -> None:
    """Focused Report sub-pane (e.g. Issue box) yanks only that pane body."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen
    from groket.ui.selectable_static import SelectableStatic

    issue_body = (
        "What: Claimed MCP failed\n"
        "Where: Turn 0\n"
        "Why: Instruction required MCP-first.\n"
        "Should have: Call preferred MCP tools first.\n"
        "Pattern: none\n"
    )
    focused = SelectableStatic(issue_body, id="report-pane-feedback-3")
    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        get_selected_text=lambda: None,
        focused=focused,
        _selected_finding=None,
        _active_browser_tab=lambda: "tab-reports",
        _collect_active_tab_plain_text=lambda: ("whole report", "report"),
        app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
    assert len(copied) == 1
    assert copied[0].startswith("What: Claimed MCP failed")
    assert "whole report" not in copied[0]
    assert notes


def test_is_extractable_static() -> None:
    from groket.ui.selectable_static import SelectableStatic, is_extractable_static
    from textual.widgets import Static

    assert is_extractable_static(SelectableStatic("x")) is True
    assert is_extractable_static(Static("x")) is False
    assert is_extractable_static(None) is False


def test_action_copy_detail_yanks_selected_finding_issue_box() -> None:
    """On Findings tab, y copies MF Issue box (What/Where/Why/Should/Pattern)."""
    from types import SimpleNamespace

    from groket.analysis.base import Finding
    from groket.models import Severity
    from groket.ui.screens.browser import BrowserScreen

    finding = Finding(
        id="feedback-f1",
        plugin_id="feedback",
        severity=Severity.HIGH,
        title="Ignored MCP",
        detail="short",
        category="Instruction Following",
        event_indices=[4, 32],
        extras={
            "what_model_did": "Claimed MCP failed without trying the bridge.",
            "what_should_have_done": "Call preferred MCP tools first.",
            "why_mistake": "Instruction required MCP-first.",
            "where": "Turn 0, assistant #4",
            "pattern": "Asserts preferred integration is down without attempting it",
        },
    )
    copied: list[str] = []
    notes: list[str] = []

    class _Host:
        meta = SimpleNamespace(model_display="test-model", session_id="sess-1")
        _selected_finding = finding

        def get_selected_text(self) -> None:
            return None

        @property
        def focused(self) -> None:
            return None

        def _active_browser_tab(self) -> str:
            return "tab-findings"

        def _finding_clipboard_text(self, f: Finding) -> str:
            return BrowserScreen._finding_clipboard_text(self, f)  # type: ignore[arg-type]

        def _format_finding_issue_box(self, f: Finding) -> str | None:
            return BrowserScreen._format_finding_issue_box(self, f)  # type: ignore[arg-type]

        def _finding_plain_text(self, f: Finding) -> str:
            return BrowserScreen._finding_plain_text(self, f)  # type: ignore[arg-type]

        def _collect_active_tab_plain_text(self) -> tuple[str, str]:
            return ("", "none")

        def notify(self, msg: str, **kwargs: object) -> None:
            notes.append(str(msg))

        app = SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text))

    BrowserScreen.action_copy_detail(_Host())  # type: ignore[arg-type]
    assert len(copied) == 1
    body = copied[0]
    assert body.startswith("What: Claimed MCP failed")
    assert "Where: Turn 0, assistant #4" in body
    assert "Why: Instruction required MCP-first." in body
    assert "Should have: Call preferred MCP tools first." in body
    assert "Pattern: Asserts preferred integration is down" in body
    # Not the whole report / form fields dump
    assert "Model Name:" not in body
    assert "Form fields" not in body
    assert notes


@pytest.mark.asyncio
async def test_action_help_quit_copies_selection() -> None:
    """Ctrl+C copies selection when present instead of only showing quit hint."""
    from types import SimpleNamespace

    from groket.ui.app import TraceEvalApp

    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        screen=SimpleNamespace(get_selected_text=lambda: "selected-bit"),
        copy_to_clipboard=lambda text: copied.append(text),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
        active_bindings={},
    )
    TraceEvalApp.action_help_quit(host)  # type: ignore[arg-type]
    assert copied == ["selected-bit"]
    assert notes
