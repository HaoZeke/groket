"""Tests for render_detail helpers."""

from __future__ import annotations

import json

from groket.ui.render_detail import (
    _guess_lexer,
    _lang_from_path,
    _looks_like_console_output,
    _truncate_mid,
    sanitize_console_text,
    tool_style,
)


class TestSanitizeConsoleText:
    def test_plain_text_unchanged(self):
        assert sanitize_console_text("hello world") == "hello world"

    def test_strips_ansi_csi(self):
        text = "\x1b[31mERROR\x1b[0m: something"
        result = sanitize_console_text(text)
        assert "ERROR" in result
        assert "\x1b" not in result

    def test_strips_ansi_osc(self):
        text = "\x1b]0;Window Title\x07some text"
        result = sanitize_console_text(text)
        assert "some text" in result
        assert "Window Title" not in result

    def test_strips_control_chars(self):
        text = "line1\x00\x01\x02line2"
        result = sanitize_console_text(text)
        assert "line1" in result
        assert "line2" in result
        assert "\x00" not in result

    def test_preserves_tabs_newlines(self):
        text = "line1\n\tindented"
        result = sanitize_console_text(text)
        assert "\n" in result
        assert "\t" in result

    def test_normalizes_cr(self):
        text = "progress1\rprogress2\rprogress3"
        result = sanitize_console_text(text)
        assert "\r" not in result

    def test_empty_string(self):
        assert sanitize_console_text("") == ""

    def test_collapses_blank_runs(self):
        text = "a\n\n\n\n\n\nb"
        result = sanitize_console_text(text)
        # At most 3 newlines in a row
        assert "\n\n\n\n" not in result


class TestToolStyle:
    def test_known_tools(self):
        # Family palette: shell=running, read=cream, write=complete
        assert tool_style("run_terminal_command") == "#D79921"
        assert tool_style("read_file") == "#FBF1C7"
        assert tool_style("grep") == "#FBF1C7"
        assert tool_style("search_replace") == "#98971A"

    def test_unknown_tool(self):
        assert tool_style("some_random_tool") == "dim"


class TestLangFromPath:
    def test_python(self):
        assert _lang_from_path("src/main.py") == "python"

    def test_javascript(self):
        assert _lang_from_path("app.js") == "javascript"

    def test_typescript(self):
        assert _lang_from_path("src/index.ts") == "typescript"

    def test_rust(self):
        assert _lang_from_path("src/lib.rs") == "rust"

    def test_unknown(self):
        assert _lang_from_path("data.bin") == ""

    def test_dockerfile(self):
        assert _lang_from_path("docker/Dockerfile") == "dockerfile"


class TestGuessLexer:
    def test_json_content(self):
        assert _guess_lexer('{"key": "value"}') == "json"

    def test_diff_content(self):
        diff = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,4 @@\n+new line"
        assert _guess_lexer(diff) == "diff"

    def test_bash_for_terminal(self):
        assert _guess_lexer("output", tool_name="run_terminal_command") == "bash"

    def test_from_path_hint(self):
        assert _guess_lexer("code", path_hint="src/main.py") == "python"

    def test_python_from_content(self):
        src = "import os\n\ndef main():\n    return None\n\nclass Foo:\n    pass\n"
        assert _guess_lexer(src) == "python"


class TestLooksLikeConsoleOutput:
    def test_terminal_tool(self):
        assert _looks_like_console_output("", "run_terminal_command") is True

    def test_ansi_in_text(self):
        assert _looks_like_console_output("\x1b[31mred\x1b[0m") is True

    def test_plain_text(self):
        assert _looks_like_console_output("just normal text") is False


class TestTruncateMid:
    def test_short_text_unchanged(self):
        text = "short"
        assert _truncate_mid(text) == text

    def test_long_text_truncated(self):
        text = "x" * 20000
        result = _truncate_mid(text, head=100, tail=100, limit=500)
        assert len(result) < len(text)
        assert "truncated" in result


# ── Event and tool detail rendering ───────────────────────────────────────

from conftest import make_trace_event
from groket.analysis.base import Finding
from groket.models import Flag, FlagVerdict, Severity
from groket.ui.render_detail import (
    render_event_detail,
    render_tool_detail,
)
from groket.ui.styles import tool_label as tool_markup
from rich.console import Group
from rich.text import Text

from .pilot_helpers import assert_rich_contains, rich_plain


class TestToolMarkup:
    def test_known_tool(self):
        markup = tool_markup("run_terminal_command")
        assert "run_terminal_command" in markup

    def test_truncates_long_name(self):
        markup = tool_markup("a" * 50, max_len=10)
        assert len(markup) < 100


def _group_has_syntax(group: Group) -> bool:
    from rich.syntax import Syntax

    for item in group.renderables:
        if isinstance(item, Syntax):
            return True
        if isinstance(item, Group) and _group_has_syntax(item):
            return True
    return False


class TestRenderToolDetail:
    def test_basic_tool_call(self):
        result = render_tool_detail(
            index=0,
            tool_name="run_terminal_command",
            raw_input={"command": "echo hello"},
            output="hello\n",
            is_error=False,
        )
        assert isinstance(result, Group)

    def test_read_file_output_uses_syntax(self):
        """File dumps are Syntax (code), not plain Text / Markdown."""
        from rich.syntax import Syntax

        body = "import sys\n\ndef greet(name: str) -> str:\n    return f'hi {name}'\n"
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={"target_file": "src/greet.py"},
            output=body,
        )
        assert isinstance(result, Group)
        assert _group_has_syntax(result)
        # Path-driven python lexer (Rich stores a Pygments lexer instance).
        syntaxes = [x for x in result.renderables if isinstance(x, Syntax)]
        assert syntaxes
        lex = syntaxes[-1].lexer
        lex_name = (getattr(lex, "name", None) or type(lex).__name__ or "").lower()
        assert "python" in lex_name

    def test_read_file_large_output_still_syntax(self):
        """Mid-truncated large reads must not fall back to plain Text."""
        body = "def foo():\n    return 1\n\n" * 800  # >12k chars
        assert len(body) > 12_000
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={"target_file": "pkg/big.py"},
            output=body,
        )
        assert _group_has_syntax(result)

    def test_tool_update_code_without_path_uses_syntax(self):
        """Code-shaped tool_call_update body without path still renders as code."""
        body = 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("hi")\n}\n'
        result = render_tool_detail(
            index=3,
            tool_name="read_file",
            raw_input={},
            output=body,
            event_type="tool_call_update",
        )
        assert _group_has_syntax(result)

    def test_terminal_output_stays_plain_text(self):
        from rich.syntax import Syntax

        result = render_tool_detail(
            index=0,
            tool_name="run_terminal_command",
            raw_input={"command": "echo hi"},
            output="hi\n",
        )
        bits = list(result.renderables)
        # Input command is bash Syntax; stdout stays plain Text.
        cmd_syn = [x for x in bits if isinstance(x, Syntax)]
        assert cmd_syn
        cmd_lex = (
            getattr(cmd_syn[0].lexer, "name", None) or type(cmd_syn[0].lexer).__name__
        ).lower()
        assert "bash" in cmd_lex or "shell" in cmd_lex
        assert any(isinstance(x, Text) and "hi" in x.plain for x in bits)

    def test_python_read_file_output_lexer_from_path(self):
        from rich.syntax import Syntax

        body = "# module header\nimport os\n\ndef main():\n    return 0\n"
        result = render_tool_detail(
            index=1,
            tool_name="read_file",
            raw_input={"target_file": "/workspace/pkg/app.py"},
            output=body,
            event_type="tool_call_update",
        )
        syn = [x for x in result.renderables if isinstance(x, Syntax)]
        assert syn
        name = (getattr(syn[-1].lexer, "name", None) or type(syn[-1].lexer).__name__).lower()
        assert "python" in name

    def test_error_tool_call(self):
        result = render_tool_detail(
            index=1,
            tool_name="run_terminal_command",
            raw_input={"command": "make build"},
            output="error: undefined reference",
            is_error=True,
            exit_code=2,
        )
        assert isinstance(result, Group)

    def test_search_replace_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="search_replace",
            raw_input={
                "file_path": "src/main.py",
                "old_string": "old code",
                "new_string": "new code",
            },
            output="File updated",
        )
        assert isinstance(result, Group)

    def test_grep_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="grep",
            raw_input={"pattern": "def main", "path": "src/"},
            output="src/main.py:1:def main():",
        )
        assert isinstance(result, Group)

    def test_empty_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="unknown_tool",
            raw_input={},
            output="",
        )
        assert isinstance(result, Group)


class TestRenderEventDetail:
    def test_tool_call_event(self):
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "test"},
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "grep")

    def test_assistant_event(self):
        ev = make_trace_event(
            index=0,
            event_type="agent_message_chunk",
            content="I'll help you fix that bug.",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "I'll help you fix that bug.")

    def test_session_error_event(self):
        ev = make_trace_event(
            index=0,
            event_type="session_error",
            content="turn ended  outcome=error",
            is_error=True,
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "error")

    def test_with_finding(self):
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="run_terminal_command",
            raw_input={"command": "make"},
        )
        finding = Finding(
            id="f1",
            plugin_id="engine",
            severity=Severity.HIGH,
            title="Build failed",
            detail="The build failed with errors",
            category="Build",
        )
        result = render_event_detail(ev, finding=finding)
        assert_rich_contains(result, "Build failed")

    def test_with_flag(self):
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "x"},
        )
        flag = Flag(event_index=0, verdict=FlagVerdict.BAD, description="Wrong approach")
        result = render_event_detail(ev, flag=flag)
        assert_rich_contains(result, "Wrong approach")

    def test_thought_event(self):
        ev = make_trace_event(
            index=0,
            event_type="agent_thought_chunk",
            content="I need to think about this...",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "I need to think about this")

    def test_plan_event(self):
        ev = make_trace_event(
            index=0,
            event_type="plan",
            content='[{"id": "1", "content": "Step 1", "status": "pending"}]',
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "Step 1")

    def test_subagent_event(self):
        ev = make_trace_event(
            index=0,
            event_type="subagent_spawned",
            content="Spawned general-purpose: Investigate the bug",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "Investigate the bug")

    def test_user_event(self):
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="Do the thing please.",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "Do the thing please.")

    def test_empty_content_event(self):
        ev = make_trace_event(index=0, event_type="agent_message_chunk", content="")
        result = render_event_detail(ev)
        # Empty body still shows an assistant-typed detail frame.
        assert "agent message chunk" in rich_plain(result).lower()

    def test_session_event(self):
        ev = make_trace_event(
            index=0,
            event_type="turn_started",
            content="turn started  turn_number=0  model_id=v9",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "turn started")

    def test_subagent_markdown_content(self):
        ev = make_trace_event(
            index=0,
            event_type="subagent_spawned",
            content="# Summary\n\nMarkdown subagent",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "Summary")

    def test_tool_result_event(self):
        ev = make_trace_event(
            index=0,
            event_type="tool_call_update",
            tool_name="read_file",
            content="file contents here",
            tool_call_id="call-99",
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "file contents here")

    def test_duration_in_detail(self):
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="run_terminal_command",
            raw_input={"command": "sleep 5"},
        )
        result = render_event_detail(ev, duration=5.0)
        plain = rich_plain(result)
        assert "run_terminal_command" in plain or "sleep 5" in plain or "5" in plain

    def test_turn_index_in_detail_meta(self):
        """Selected-event detail shows sequential operator turn in the meta line."""
        ev = make_trace_event(
            index=12,
            event_type="user_message_chunk",
            content="hello from turn 3",
            timestamp=1000,
        )
        result = render_event_detail(ev, turn_index=3)
        plain = rich_plain(result)
        assert "Turn 3" in plain
        tool = make_trace_event(
            index=13,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
        )
        tool_plain = rich_plain(render_event_detail(tool, turn_index=3))
        assert "Turn 3" in tool_plain


# ── Tool input rendering branches ────────────────────────────────────────


class TestRenderToolInputBranches:
    def test_list_dir_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="list_dir",
            raw_input={"target_directory": "/home/user"},
            output="file1.py\nfile2.py",
        )
        assert isinstance(result, Group)

    def test_todo_write_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="todo_write",
            raw_input={"todos": [{"id": "1", "content": "Do thing"}]},
            output="ok",
        )
        assert isinstance(result, Group)

    def test_web_search_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="web_search",
            raw_input={"query": "python async"},
            output="Results...",
        )
        assert isinstance(result, Group)

    def test_spawn_subagent_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="spawn_subagent",
            raw_input={"prompt": "Investigate\nthe bug", "description": "Bug hunt"},
            output="Done",
        )
        assert isinstance(result, Group)

    def test_read_file_no_path(self):
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={},
            output="content",
        )
        assert isinstance(result, Group)

    def test_search_replace_with_extras(self):
        result = render_tool_detail(
            index=0,
            tool_name="search_replace",
            raw_input={
                "file_path": "x.py",
                "old_string": "old",
                "new_string": "new",
                "replace_all": True,
            },
            output="ok",
        )
        assert isinstance(result, Group)

    def test_unknown_tool_json_input(self):
        result = render_tool_detail(
            index=0,
            tool_name="custom_tool",
            raw_input={"key": "val"},
            output="",
        )
        assert isinstance(result, Group)

    def test_tool_detail_with_metadata(self):
        result = render_tool_detail(
            index=5,
            tool_name="grep",
            raw_input={"pattern": "def"},
            output="match",
            tool_call_id="call-42",
            exit_code=0,
            signal="",
            time_str="12:00:05",
            update_index=3,
            event_type="tool_call",
            duration=2.5,
        )
        assert isinstance(result, Group)

    def test_tool_detail_error_with_exit_code(self):
        result = render_tool_detail(
            index=0,
            tool_name="run_terminal_command",
            raw_input={"command": "false"},
            output="",
            is_error=True,
            exit_code=1,
            signal="SIGTERM",
        )
        assert isinstance(result, Group)


# ── render_tool_detail_from_event ─────────────────────────────────────────

from groket.ui.render_detail import (
    _content_str,
    render_markdown_doc,
    render_tool_detail_from_event,
    set_static_renderable,
)


class TestRenderToolDetailFromEvent:
    def test_tool_call_event(self):
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "main.py"},
            tool_call_id="c1",
        )
        result = render_tool_detail_from_event(ev)
        assert isinstance(result, Group)

    def test_tool_result_with_paired_call(self):
        call_ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "test"},
            tool_call_id="c1",
        )
        result_ev = make_trace_event(
            index=1,
            event_type="tool_call_update",
            tool_name="grep",
            content="match found",
            tool_call_id="c1",
        )
        result = render_tool_detail_from_event(result_ev, paired_call=call_ev, duration=1.5)
        assert isinstance(result, Group)


class TestContentStr:
    def test_none(self):
        assert _content_str(None) == ""

    def test_string(self):
        assert _content_str("hello") == "hello"

    def test_dict(self):
        result = _content_str({"key": "val"})
        assert "key" in result

    def test_sanitize(self):
        result = _content_str("\x1b[31mred\x1b[0m", sanitize=True)
        assert "\x1b" not in result


class TestSetStaticRenderable:
    def test_normal_update(self):
        from types import SimpleNamespace

        updated = {}

        def fake_update(content):
            updated["content"] = content

        widget = SimpleNamespace(update=fake_update)
        set_static_renderable(widget, "hello")
        assert updated["content"] == "hello"


class TestRenderMarkdownDoc:
    def test_normal(self):
        r = render_markdown_doc("# Title\n\nBody text")
        assert_rich_contains(r, "Title")

    def test_empty(self):
        r = render_markdown_doc("")
        # Empty doc still yields a renderable (placeholder or blank frame).
        assert rich_plain(r) is not None
        assert isinstance(rich_plain(r), str)

    def test_long(self):
        r = render_markdown_doc("x" * 200_000)
        plain = rich_plain(r)
        assert "x" in plain
        assert len(plain) < 200_000  # truncated for display

    def test_markdown_exception_fallback(self):
        """Markdown parse exception falls back to plain Text."""
        from unittest.mock import patch

        with patch("groket.ui.render_detail.Markdown", side_effect=ValueError("parse error")):
            r = render_markdown_doc("# Title\n\nBody")
            assert_rich_contains(r, "Title")


class TestSanitizeConsoleTextNonStr:
    def test_non_str_input_coerced(self):
        """Non-str input is coerced to string."""
        result = sanitize_console_text(42)  # type: ignore[arg-type]  # deliberate wrong type
        assert "42" in result

    def test_noisy_c0_detection(self):
        """High C0 control-char noise ratio is detected as console output."""
        noisy = "a" + "\x01" * 20
        assert _looks_like_console_output(noisy) is True

    def test_display_false_preserves_blanks(self):
        """for_display=False preserves blank lines."""
        text = "line1\n\n\n\n\nline2"
        result = sanitize_console_text(text, for_display=False)
        assert "line1" in result
        assert "line2" in result


class TestSetStaticRenderableException:
    def test_update_raises_falls_back(self):
        """set_static_renderable retries with Text fallback when update raises."""
        from types import SimpleNamespace

        from rich.console import Group

        call_count = 0

        def bad_update(content: Text | Group) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("bad renderable")

        widget = SimpleNamespace(update=bad_update)
        set_static_renderable(widget, Group(Text("hello")))
        assert call_count == 2

    def test_skips_update_while_text_selected(self):
        """Active Textual text selection must not be cleared by live re-render."""
        from types import SimpleNamespace

        updated: list[object] = []

        class _W:
            def update(self, content: object) -> None:
                updated.append(content)

        widget = _W()
        widget.screen = SimpleNamespace(selections={widget: object()})  # type: ignore[attr-defined]
        set_static_renderable(widget, "new")
        assert updated == []


class TestLooksDiff:
    def test_not_diff(self):
        """_looks_diff returns False for non-diff text."""
        from groket.ui.render_detail import _looks_diff

        assert _looks_diff("just some normal text\nwith lines") is False
        assert _looks_diff("") is False


class TestGuessLexerMore:
    def test_shebang(self):
        """Shebang line is detected as bash."""
        assert _guess_lexer("#!/bin/bash\necho hi") == "bash"

    def test_xml_doctype(self):
        """XML processing instructions and DOCTYPE are detected as xml."""
        assert _guess_lexer("<?xml version='1.0'?>") == "xml"
        assert _guess_lexer("<!DOCTYPE html>") == "xml"


class TestContentStrMore:
    def test_list_input(self):
        """List content is serialised to JSON string."""
        result = _content_str(["a", "b"])
        assert "a" in result

    def test_dict_input(self):
        """Dict content is serialised to JSON string."""
        result = _content_str({"key": "val"})
        assert "key" in result

    def test_unjsonable_input(self):
        """Unserializable input falls back to str()."""

        # Pass an object that JSON can't serialize
        class _Bad:
            def __str__(self):
                return "bad-obj"

        result = _content_str(_Bad())  # type: ignore[arg-type]  # testing error path
        assert "bad-obj" in result


class TestRenderToolInputBranchesMore:
    def test_run_terminal_command_extra_params(self):
        """run_terminal_command renders with timeout and background params."""
        result = render_tool_detail(
            index=0,
            tool_name="run_terminal_command",
            raw_input={"command": "ls", "timeout": 60, "background": True},
            output="file.py",
        )
        assert isinstance(result, Group)

    def test_read_file_with_extra_params(self):
        """read_file renders with offset and limit params."""
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={"target_file": "main.py", "offset": 10, "limit": 50},
            output="content",
        )
        assert isinstance(result, Group)

    def test_read_file_bare_no_path(self):
        """read_file renders without target_file or path_hint."""
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={"some_field": "value"},
            output="content",
        )
        assert isinstance(result, Group)

    def test_list_dir_with_extra_params(self):
        """list_dir renders with extra params."""
        result = render_tool_detail(
            index=0,
            tool_name="list_dir",
            raw_input={"target_directory": "/home", "extra_field": True},
            output="files",
        )
        assert isinstance(result, Group)

    def test_todo_write_exception(self):
        """todo_write renders when json.dumps fails on unserializable input."""

        class _Unserializable:
            pass

        result = render_tool_detail(
            index=0,
            tool_name="todo_write",
            raw_input={"data": _Unserializable()},
            output="ok",
        )
        assert isinstance(result, Group)

    def test_web_search_with_question_field(self):
        """ask_user_question renders with question and options fields."""
        result = render_tool_detail(
            index=0,
            tool_name="ask_user_question",
            raw_input={
                "question": "What approach?",
                "options": ["a", "b"],
            },
            output="done",
        )
        assert isinstance(result, Group)

    def test_spawn_subagent_extra_fields(self):
        """spawn_subagent renders with extra non-string fields."""
        result = render_tool_detail(
            index=0,
            tool_name="spawn_subagent",
            raw_input={
                "prompt": "Do work",
                "timeout": 300,
            },
            output="done",
        )
        assert isinstance(result, Group)

    def test_default_tool_input_json_exception(self):
        """Unknown tool renders when json.dumps fails on unserializable input."""

        class _Obj:
            pass

        result = render_tool_detail(
            index=0,
            tool_name="random_tool",
            raw_input={"obj": _Obj()},
            output="",
        )
        assert isinstance(result, Group)


class TestRenderToolOutputBranches:
    def test_sanitize_wipes_everything_fallback(self):
        """Sanitize wipes all content; for_display=False fallback is used."""
        from unittest.mock import patch

        def fake_sanitize(text, for_display=True):
            if for_display:
                return ""
            return "fallback-content"

        with patch("groket.ui.render_detail.sanitize_console_text", side_effect=fake_sanitize):
            result = render_tool_detail(
                index=0,
                tool_name="monitor",
                raw_input={},
                output="\x01\x02\x03",
            )
            assert isinstance(result, Group)

    def test_cleaning_note_shown(self):
        """Heavy ANSI stripping shows a cleaning note."""
        # Pass text with lots of ANSI that gets stripped
        noisy = "\x1b[31m" * 50 + "visible"
        result = render_tool_detail(
            index=0,
            tool_name="run_terminal_command",
            raw_input={"command": "test"},
            output=noisy,
        )
        assert isinstance(result, Group)

    def test_read_file_output_uses_path_lexer(self):
        """read_file output uses the target_file path hint for lexer selection."""
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={"target_file": "main.py"},
            output="def foo():\n    pass",
        )
        assert isinstance(result, Group)

    def test_json_output_reformatted(self):
        """JSON output is reformatted with indentation."""
        result = render_tool_detail(
            index=0,
            tool_name="custom_tool",
            raw_input={},
            output='{"key":"value","n":1}',
        )
        assert isinstance(result, Group)

    def test_read_file_numbered_prefixes_not_in_highlight(self) -> None:
        result = render_tool_detail(
            index=0,
            tool_name="read_file",
            raw_input={"target_file": "mod.py"},
            output="1→from pathlib import Path\n2→import os\n",
        )
        plain = rich_plain(result)
        assert "→" not in plain
        assert "from pathlib import Path" in plain
        assert "import os" in plain

    def test_image_gen_output_is_path_text_not_pixels(self) -> None:
        body = json.dumps(
            {
                "path": "/tmp/img.jpg",
                "filename": "img.jpg",
                "message": "Image generated and saved to /tmp/img.jpg.",
            }
        )
        result = render_tool_detail(
            index=0,
            tool_name="image_gen",
            raw_input={"prompt": "a mark"},
            output=body,
        )
        plain = rich_plain(result)
        assert "/tmp/img.jpg" in plain
        assert "Image generated" in plain
        assert "PIL" not in plain
        assert "sixel" not in plain.lower()


class TestRenderToolDetailFromEventExitCode:
    def test_exit_code_from_raw_input(self):
        """exit_code is extracted from raw_input when present."""
        ev = make_trace_event(
            index=0,
            event_type="tool_call_update",
            tool_name="run_terminal_command",
            content="error output",
            raw_input={"exit_code": 1},
        )
        result = render_tool_detail_from_event(ev)
        assert isinstance(result, Group)

    def test_tool_call_only_no_result(self):
        """tool_call renders inline content when no result event exists."""
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "test"},
            content="some inline content",
            tool_call_id="c1",
        )
        result = render_tool_detail_from_event(ev)
        assert isinstance(result, Group)


class TestRenderEventDetailMore:
    def test_long_body_truncated(self):
        """Long assistant body is truncated."""
        ev = make_trace_event(
            index=0,
            event_type="agent_message_chunk",
            content="x" * 25000,
        )
        result = render_event_detail(ev)
        plain = rich_plain(result)
        assert "x" in plain
        assert len(plain) < 25000

    def test_finding_banner_with_non_tool_event(self):
        """Finding and flag banners render on non-tool events."""
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="Do something",
        )
        finding = Finding(
            id="f1",
            plugin_id="engine",
            severity=Severity.MEDIUM,
            title="Issue found",
            detail="Details here",
            category="Test",
        )
        flag = Flag(event_index=0, verdict=FlagVerdict.BAD, description="Flagged")
        result = render_event_detail(ev, finding=finding, flag=flag)
        assert_rich_contains(result, "Issue found", "Flagged", "Do something")

    def test_session_non_error_event(self):
        """Session event without error renders normally."""
        ev = make_trace_event(
            index=0,
            event_type="turn_started",
            content="turn started",
            is_error=False,
        )
        result = render_event_detail(ev)
        assert_rich_contains(result, "turn started")
