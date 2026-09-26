"""Tests for live_share — share URL reading & display helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.runs import live_share as ls
from groket.runs.live_share import (
    _REGISTRY,
    SHARE_FILENAME,
    ShareResult,
    clear_share_capability_cache,
    format_share_stats_line,
    format_share_summary_markdown,
    get_share_display,
    get_share_url,
    is_share_not_ready_error,
    is_share_unavailable_error,
    load_cached_share,
    probe_host_share_capability,
    refresh_share_from_disk,
    share_path_for,
)


@pytest.fixture(autouse=True)
def _clear_registry():
    """Reset the module-level registry before every test."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def _write_share(session_dir: Path, data: dict) -> Path:
    """Helper — write groket-share.json and return the file path."""
    p = session_dir / SHARE_FILENAME
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ── ShareResult dataclass ─────────────────────────────────────────────────


class TestShareResult:
    def test_to_dict_includes_populated_omits_empty(self):
        sr = ShareResult(
            session_id="abc",
            session_dir="/tmp/abc",
            share_url="https://share.example/abc",
            error="",
            created_at="2026-06-25T00:00:00Z",
            source="incontainer",
            method="cli",
            snapshot_n=3,
            snapshot_at="2026-06-25T01:00:00Z",
        )
        d = sr.to_dict()
        # Always-present keys
        assert d["session_id"] == "abc"
        assert d["share_url"] == "https://share.example/abc"
        assert d["source"] == "incontainer"
        # Optional populated keys included
        assert d["method"] == "cli"
        assert d["snapshot_n"] == 3
        assert d["snapshot_at"] == "2026-06-25T01:00:00Z"
        # Empty optional keys omitted
        assert "updated_at" not in d
        assert "note" not in d

    def test_from_dict_roundtrip(self):
        original = ShareResult(
            session_id="sess-1",
            session_dir="/traces/sess-1",
            share_url="https://share.example/sess-1",
            error="",
            created_at="2026-06-25T00:00:00Z",
            source="incontainer",
            method="cli",
            snapshot_n=2,
            snapshot_at="2026-06-25T01:00:00Z",
            updated_at="2026-06-25T02:00:00Z",
            note="first snapshot",
        )
        d = original.to_dict()
        restored = ShareResult.from_dict(d)
        assert restored.session_id == original.session_id
        assert restored.share_url == original.share_url
        assert restored.snapshot_n == original.snapshot_n
        assert restored.method == original.method
        assert restored.note == original.note

    def test_from_dict_bad_snapshot_n(self):
        data = {
            "session_id": "x",
            "session_dir": "/tmp/x",
            "snapshot_n": "not-a-number",
        }
        sr = ShareResult.from_dict(data)
        assert sr.snapshot_n == 0

    def test_from_dict_missing_snapshot_n(self):
        data = {"session_id": "y", "session_dir": "/tmp/y"}
        sr = ShareResult.from_dict(data)
        assert sr.snapshot_n == 0


# ── share_path_for ────────────────────────────────────────────────────────


def test_share_path_for(tmp_path: Path):
    result = share_path_for(tmp_path / "my-session")
    assert result == tmp_path / "my-session" / SHARE_FILENAME
    assert result.name == "groket-share.json"


# ── is_share_not_ready_error ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        ("no messages to share", True),
        ("Error: no messages to share yet", True),
        ("nothing to share", True),
        ("NOTHING TO SHARE", True),
        ("connection refused", False),
        ("timeout exceeded", False),
        ("", False),
    ],
)
def test_is_share_not_ready_error(err: str, expected: bool):
    assert is_share_not_ready_error(err) is expected


# ── is_share_unavailable_error ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        ('Error: Invalid params: "Session sharing is not available for your account."', True),
        ("Session sharing is not available for your account.", True),
        ("sharing is not available", True),
        ('Error: Resource not found: { "uri": "Session not found" }', False),
        ("Session not found", False),
        ("connection refused", False),
        ("", False),
    ],
)
def test_is_share_unavailable_error(err: str, expected: bool):
    assert is_share_unavailable_error(err) is expected


# ── probe_host_share_capability ───────────────────────────────────────────


def test_probe_host_share_capability_account_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cache = tmp_path / "cap.json"
    clear_share_capability_cache(cache_path=cache)

    def _fake_share(session_id: str, *, timeout: float) -> tuple[int, str]:
        return 1, 'Error: Invalid params: "Session sharing is not available for your account."'

    monkeypatch.setattr(ls, "_run_grok_share", _fake_share)
    cap = probe_host_share_capability(force=True, cache_path=cache)
    assert cap.available is False
    assert cap.reason == "account_disabled"


def test_probe_host_share_capability_entitled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "cap.json"
    clear_share_capability_cache(cache_path=cache)

    def _fake_share(session_id: str, *, timeout: float) -> tuple[int, str]:
        return 1, 'Error: Resource not found: { "uri": "Session not found" }'

    monkeypatch.setattr(ls, "_run_grok_share", _fake_share)
    cap = probe_host_share_capability(force=True, cache_path=cache)
    assert cap.available is True
    assert cap.reason == "entitled"
    assert cache.is_file()

    # Process-memory hit (no second CLI call)
    calls = {"n": 0}

    def _boom(session_id: str, *, timeout: float) -> tuple[int, str]:
        calls["n"] += 1
        raise AssertionError("should not re-probe")

    monkeypatch.setattr(ls, "_run_grok_share", _boom)
    cap2 = probe_host_share_capability(force=False, cache_path=cache)
    assert cap2.available is True
    assert calls["n"] == 0


# ── load_cached_share ─────────────────────────────────────────────────────


class TestLoadCachedShare:
    def test_returns_none_when_no_file(self, tmp_path: Path):
        sd = tmp_path / "no-share"
        sd.mkdir()
        assert load_cached_share(sd) is None

    def test_returns_none_when_empty_url(self, tmp_path: Path):
        sd = tmp_path / "empty-url"
        sd.mkdir()
        _write_share(sd, {"session_id": "empty-url", "share_url": ""})
        assert load_cached_share(sd) is None

    def test_returns_result_when_valid(self, tmp_path: Path):
        sd = tmp_path / "valid"
        sd.mkdir()
        _write_share(
            sd,
            {
                "session_id": "valid",
                "share_url": "https://share.example/valid",
                "source": "incontainer",
            },
        )
        res = load_cached_share(sd)
        assert res is not None
        assert res.share_url == "https://share.example/valid"
        assert res.source == "incontainer"


# ── get_share_url ─────────────────────────────────────────────────────────


def test_get_share_url_returns_url(tmp_path: Path):
    sd = tmp_path / "has-url"
    sd.mkdir()
    _write_share(sd, {"session_id": "has-url", "share_url": "https://share.example/1"})
    assert get_share_url(sd) == "https://share.example/1"


def test_get_share_url_empty_when_missing(tmp_path: Path):
    sd = tmp_path / "missing"
    sd.mkdir()
    assert get_share_url(sd) == ""


# ── get_share_display ─────────────────────────────────────────────────────


class TestGetShareDisplay:
    def test_pending_when_no_file(self, tmp_path: Path):
        sd = tmp_path / "no-file"
        sd.mkdir()
        d = get_share_display(sd)
        assert d["pending"] is True
        assert d["ready"] is False
        assert d["share_url"] == ""

    def test_ready_when_url_present(self, tmp_path: Path):
        sd = tmp_path / "ready"
        sd.mkdir()
        _write_share(
            sd,
            {
                "session_id": "ready",
                "share_url": "https://share.example/ready",
                "snapshot_n": 5,
            },
        )
        d = get_share_display(sd)
        assert d["ready"] is True
        assert d["pending"] is False
        assert d["share_url"] == "https://share.example/ready"
        assert d["snapshot_n"] == 5

    def test_error_with_url_not_ready(self, tmp_path: Path):
        """Ready only when last write has a URL and empty error."""
        sd = tmp_path / "denied"
        sd.mkdir()
        err = 'Error: Invalid params: "Session sharing is not available for your account."'
        _write_share(
            sd,
            {
                "session_id": "denied",
                "share_url": "https://share.example/stale",
                "error": err,
                "snapshot_n": 52,
            },
        )
        assert load_cached_share(sd) is None
        assert get_share_url(sd) == ""
        d = get_share_display(sd)
        assert d["ready"] is False
        assert d["share_url"] == ""
        assert "not available" in str(d["error"]).lower()
        md = format_share_summary_markdown(sd)
        assert "not available" in md.lower() or "error" in md.lower()
        assert "share.example/stale" not in md


# ── format_share_summary_markdown ─────────────────────────────────────────


class TestFormatShareSummaryMarkdown:
    def test_with_url(self, tmp_path: Path):
        sd = tmp_path / "md-url"
        sd.mkdir()
        _write_share(
            sd,
            {
                "session_id": "md-url",
                "share_url": "https://share.example/md",
                "method": "cli",
            },
        )
        md = format_share_summary_markdown(sd)
        assert "https://share.example/md" in md
        assert "**URL:**" in md
        assert "## Grok share" in md

    def test_pending_state(self, tmp_path: Path):
        sd = tmp_path / "md-pending"
        sd.mkdir()
        md = format_share_summary_markdown(sd)
        assert "_pending_" in md
        assert "F5" in md

    def test_error_state(self, tmp_path: Path):
        sd = tmp_path / "md-err"
        sd.mkdir()
        _write_share(
            sd,
            {
                "session_id": "md-err",
                "share_url": "",
                "error": "connection refused",
            },
        )
        md = format_share_summary_markdown(sd)
        assert "connection refused" in md
        assert "_not available_" in md


# ── format_share_stats_line ───────────────────────────────────────────────


def test_format_share_stats_line_with_url(tmp_path: Path):
    sd = tmp_path / "stats-url"
    sd.mkdir()
    _write_share(
        sd,
        {
            "session_id": "stats-url",
            "share_url": "https://share.example/stats",
            "snapshot_n": 2,
            "method": "cli",
        },
    )
    line = format_share_stats_line(sd)
    assert "https://share.example/stats" in line
    assert "#2" in line
    assert "cli" in line


def test_format_share_stats_line_pending(tmp_path: Path):
    sd = tmp_path / "stats-pending"
    sd.mkdir()
    line = format_share_stats_line(sd)
    assert "pending" in line


# ── refresh_share_from_disk ───────────────────────────────────────────────


def test_refresh_share_from_disk_reads_url(tmp_path: Path):
    sd = tmp_path / "refresh"
    sd.mkdir()
    _write_share(
        sd,
        {
            "session_id": "refresh",
            "share_url": "https://share.example/refresh",
        },
    )
    url = refresh_share_from_disk(sd)
    assert url == "https://share.example/refresh"


def test_refresh_share_from_disk_empty_when_missing(tmp_path: Path):
    sd = tmp_path / "refresh-empty"
    sd.mkdir()
    url = refresh_share_from_disk(sd)
    assert url == ""


def test_share_result_and_read(tmp_path: Path):
    r = ls.ShareResult(
        session_id="s",
        session_dir=str(tmp_path),
        share_url="http://share",
        method="cli",
        snapshot_n=1,
        snapshot_at="t",
        updated_at="u",
        note="n",
    )
    d = r.to_dict()
    assert d["share_url"] == "http://share"
    r2 = ls.ShareResult.from_dict(d)
    assert r2.session_id == "s"
    r3 = ls.ShareResult.from_dict({"session_id": "x", "snapshot_n": "bad"}, session_dir=tmp_path)
    assert r3.session_id == "x"

    assert ls.share_path_for(tmp_path).name == ls.SHARE_FILENAME
    assert ls._read_share_file(tmp_path) is None
    (tmp_path / ls.SHARE_FILENAME).write_text("not-json", encoding="utf-8")
    assert ls._read_share_file(tmp_path) is None

    payload = {
        "session_id": "s",
        "session_dir": str(tmp_path),
        "share_url": "http://u",
        "source": "incontainer",
        "method": "cli",
        "snapshot_n": 2,
        "snapshot_at": "now",
        "error": "",
    }
    (tmp_path / ls.SHARE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    data = ls._read_share_file(tmp_path)
    assert data and data["share_url"] == "http://u"
    assert ls.is_share_not_ready_error("No messages to share yet") is True
    assert ls.is_share_not_ready_error("other") is False

    loaded = ls.load_cached_share(tmp_path)
    assert loaded is not None and loaded.share_url == "http://u"
    assert ls.get_share_url(tmp_path) == "http://u"
    disp = ls.get_share_display(tmp_path)
    assert disp["ready"] is True
    assert "http://u" in ls.format_share_summary_markdown(tmp_path)
    assert "Share URL" in ls.format_share_stats_line(tmp_path)
    assert ls.refresh_share_from_disk(tmp_path) == "http://u"

    # pending / error paths
    empty = tmp_path / "empty"
    empty.mkdir()
    pend = ls.get_share_display(empty)
    assert pend["pending"] is True
    assert "pending" in ls.format_share_summary_markdown(empty).lower()
    assert "pending" in ls.format_share_stats_line(empty).lower()

    err_dir = tmp_path / "err"
    err_dir.mkdir()
    (err_dir / ls.SHARE_FILENAME).write_text(
        json.dumps({"share_url": "", "error": "fatal fail", "source": "incontainer"}),
        encoding="utf-8",
    )
    ed = ls.get_share_display(err_dir)
    assert ed["ready"] is False
    assert (
        "not available" in ls.format_share_summary_markdown(err_dir).lower()
        or "fail" in ls.format_share_summary_markdown(err_dir).lower()
    )
    assert "failed" in ls.format_share_stats_line(err_dir).lower()
    assert ls.refresh_share_from_disk(err_dir) == ""
    assert ls.load_cached_share(err_dir) is None
    assert ls.get_share_url(err_dir) == ""


class TestFromDictSnapshotNTypeError:
    """from_dict falls back to zero when snapshot_n has an invalid type."""

    def test_snapshot_n_list_falls_back_zero(self):
        data = {"session_id": "q", "session_dir": "/x", "snapshot_n": [1, 2, 3]}
        sr = ShareResult.from_dict(data)
        assert sr.snapshot_n == 0


class TestLoadCachedShareResolveFallback:
    """load_cached_share falls back to str path when resolve() raises."""

    def test_resolve_exception_uses_str(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        sd = tmp_path / "rslv"
        sd.mkdir()
        _write_share(sd, {"session_id": "rslv", "share_url": "https://u"})
        monkeypatch.setattr(Path, "resolve", lambda self: (_ for _ in ()).throw(OSError("bad")))
        res = load_cached_share(sd)
        assert res is not None
        assert res.share_url == "https://u"


class TestGetShareUrlResolveFallback:
    """get_share_url returns registry hit when resolve() raises."""

    def test_resolve_exception_and_registry_hit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        sd = tmp_path / "reg"
        sd.mkdir()
        # Pre-populate registry manually
        key = str(sd)
        _REGISTRY[key] = ShareResult(
            session_id="reg", session_dir=str(sd), share_url="https://cached"
        )
        monkeypatch.setattr(Path, "resolve", lambda self: (_ for _ in ()).throw(OSError("bad")))
        url = get_share_url(sd)
        assert url == "https://cached"


class TestGetShareDisplaySnapshotNError:
    """get_share_display treats non-integer snapshot_n as zero."""

    def test_bad_snapshot_n_in_share_file(self, tmp_path: Path):
        sd = tmp_path / "snap-err"
        sd.mkdir()
        _write_share(
            sd,
            {
                "session_id": "snap-err",
                "share_url": "https://u",
                "snapshot_n": {"nested": True},
            },
        )
        d = get_share_display(sd)
        assert d["ready"] is True
        assert d["snapshot_n"] == 0


class TestFormatShareSummaryErrorWithStaleUrl:
    """URL + error is not ready; summary shows the error, not the URL."""

    def test_url_with_error_not_ready(self, tmp_path: Path):
        sd = tmp_path / "nf"
        sd.mkdir()
        _write_share(
            sd,
            {
                "session_id": "nf",
                "share_url": "https://share/nf",
                "error": "connection reset temporarily",
            },
        )
        d = get_share_display(sd)
        assert d["ready"] is False
        assert d["share_url"] == ""
        md = format_share_summary_markdown(sd)
        assert "connection reset" in md.lower()
        assert "https://share/nf" not in md


class TestRefreshShareFromDiskResolveFallback:
    """refresh_share_from_disk returns empty string when resolve() raises."""

    def test_resolve_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        sd = tmp_path / "rfr"
        sd.mkdir()
        monkeypatch.setattr(Path, "resolve", lambda self: (_ for _ in ()).throw(OSError("bad")))
        url = refresh_share_from_disk(sd)
        assert url == ""
