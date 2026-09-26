"""Harness tagged-block dialect (observed, not an official schema)."""

from __future__ import annotations

from groket.session.tagged_blocks import (
    extract_user_query,
    harness_user_chrome_heading,
    is_harness_user_chrome,
    operator_prompt_text,
    parse_outer_tagged_block,
)


def test_parse_outer_system_reminder() -> None:
    body = (
        '<system-reminder>\nBackground task "call-1" completed (exit code: 0).\n</system-reminder>'
    )
    block = parse_outer_tagged_block(body)
    assert block is not None
    assert block.tag == "system-reminder"
    assert block.outer is True
    assert block.role == "chrome"
    assert is_harness_user_chrome(body)
    assert harness_user_chrome_heading(body) == "Background task"


def test_user_query_is_operator_not_chrome() -> None:
    text = "<user_query>\nfix the flaky test\n</user_query>"
    assert not is_harness_user_chrome(text)
    assert extract_user_query(text) == "fix the flaky test"
    assert operator_prompt_text(text) == "fix the flaky test"


def test_user_query_nested_in_composite_user_payload() -> None:
    text = "<user_info>\nOS Version: macos\n</user_info>\n\n<user_query>\nsay meow\n</user_query>"
    # Whole message is not a single outer block → not chrome-only.
    assert not is_harness_user_chrome(text)
    assert extract_user_query(text) == "say meow"
    assert operator_prompt_text(text) == "say meow"


def test_preamble_outer_is_chrome() -> None:
    text = "<user_info>\nOS Version: macos\nShell: /bin/zsh\n</user_info>"
    assert is_harness_user_chrome(text)
    assert harness_user_chrome_heading(text) == "User info"
    assert operator_prompt_text(text) == ""


def test_workspace_result_is_tool_chrome() -> None:
    text = "<workspace_result>\nFound 3 matching lines\n</workspace_result>"
    assert is_harness_user_chrome(text)
    assert harness_user_chrome_heading(text) == "Workspace result"


def test_plain_operator_text() -> None:
    assert not is_harness_user_chrome("please fix the lint")
    assert operator_prompt_text("please fix the lint") == "please fix the lint"
    assert harness_user_chrome_heading("please fix the lint") is None


def test_angle_brackets_in_code_not_outer_harness() -> None:
    # Rust generics / prose must not become chrome just for containing <>.
    text = "Use Vec<String> and Result<T, E> carefully."
    assert parse_outer_tagged_block(text) is None
    assert not is_harness_user_chrome(text)


def test_unwrap_strips_system_reminder_tags() -> None:
    from groket.session.tagged_blocks import unwrap_for_display

    raw = (
        "<system-reminder>\n"
        'Background task "call-8d0f91ab" completed (exit code: 0).\n'
        "Command: echo hello | Duration: 23.6s\n"
        "</system-reminder>"
    )
    out = unwrap_for_display(raw)
    assert "<system-reminder>" not in out
    assert "</system-reminder>" not in out
    assert "Background task" in out
    assert "echo hello" in out
