"""Tests for Grok CLI client parsing (no live grok)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from groket.analysis.llm.client import (
    GrokCliClient,
    extract_structured_payload,
    find_grok_bin,
)


def test_extract_structured_output_key() -> None:
    raw = '{"text":"{}","structuredOutput":{"summary":"ok","all_clear":true,"findings":[]}}'
    got = extract_structured_payload(raw)
    assert got is not None
    assert got.get("all_clear") is True


def test_extract_fenced_json() -> None:
    raw = '```json\n{"summary":"s","all_clear":true,"findings":[]}\n```'
    got = extract_structured_payload(raw)
    assert got is not None
    assert got["summary"] == "s"


def test_extract_empty() -> None:
    assert extract_structured_payload("") is None
    assert extract_structured_payload("not json") is None


def test_find_grok_bin_missing() -> None:
    with patch("groket.analysis.llm.client.shutil.which", return_value=None):
        with patch("groket.analysis.llm.client.Path.is_file", return_value=False):
            assert find_grok_bin() is None


def test_complete_structured_no_binary() -> None:
    with patch("groket.analysis.llm.client.find_grok_bin", return_value=None):
        r = GrokCliClient().complete_structured("hi")
        assert r.payload is None
        assert r.raw is None


def test_complete_structured_success() -> None:
    payload = {"summary": "s", "all_clear": True, "findings": []}
    import json

    stdout = json.dumps({"structuredOutput": payload})
    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout=stdout, stderr=""))
    with patch("groket.analysis.llm.client.find_grok_bin", return_value="/bin/grok"):
        with patch("groket.analysis.llm.client.subprocess.run", mock_run):
            r = GrokCliClient().complete_structured("prompt", effort="low")
    assert r.payload is not None
    assert r.payload["summary"] == "s"
    # Isolated HOME so user chrome-devtools / firecrawl plugins never load.
    assert mock_run.call_args is not None
    kwargs = mock_run.call_args.kwargs
    env = kwargs.get("env") or {}
    assert "HOME" in env
    assert env["HOME"] != str(__import__("pathlib").Path.home())
    cmd = mock_run.call_args.args[0]
    assert "--disable-web-search" in cmd
    assert "--no-subagents" in cmd
    assert "--yolo" not in cmd  # prefer always-approve without tool zoo
    # Large prompt-files are offloaded; allow a short tool loop to read + answer.
    mt = cmd.index("--max-turns")
    assert int(cmd[mt + 1]) >= 2


def test_build_isolated_review_home_disables_plugins() -> None:
    import shutil

    from groket.analysis.llm.client import build_isolated_review_home

    home = build_isolated_review_home()
    try:
        cfg = (home / ".grok" / "config.toml").read_text(encoding="utf-8")
        assert "enabled = []" in cfg
        assert "chrome" not in cfg.lower()
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_complete_structured_nonzero_with_payload() -> None:
    import json

    payload = {"summary": "s", "all_clear": True, "findings": []}
    stdout = json.dumps({"structuredOutput": payload})
    mock_run = MagicMock(return_value=MagicMock(returncode=1, stdout=stdout, stderr="warn"))
    with patch("groket.analysis.llm.client.find_grok_bin", return_value="/bin/grok"):
        with patch("groket.analysis.llm.client.subprocess.run", mock_run):
            r = GrokCliClient().complete_structured("p", model="m")
    assert r.payload is not None


def test_complete_structured_timeout() -> None:
    import subprocess

    with patch("groket.analysis.llm.client.find_grok_bin", return_value="/bin/grok"):
        with patch(
            "groket.analysis.llm.client.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="grok", timeout=1),
        ):
            r = GrokCliClient().complete_structured("p")
    assert r.payload is None
