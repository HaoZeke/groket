"""Per-session analysis inflight lock."""

from __future__ import annotations

from pathlib import Path

from groket.analysis.inflight import (
    analysis_session_key,
    clear_session_analysis_inflight,
    end_session_analysis,
    session_analysis_inflight,
    session_analysis_inflight_count,
    try_begin_session_analysis,
)


def setup_function() -> None:
    clear_session_analysis_inflight()


def teardown_function() -> None:
    clear_session_analysis_inflight()


def test_try_begin_rejects_duplicate(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    assert try_begin_session_analysis(sd) is True
    assert try_begin_session_analysis(sd) is False
    assert session_analysis_inflight(sd) is True
    assert session_analysis_inflight_count() == 1
    end_session_analysis(sd)
    assert session_analysis_inflight(sd) is False
    assert try_begin_session_analysis(sd) is True
    end_session_analysis(sd)


def test_key_normalizes_resolve(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    k1 = analysis_session_key(sd)
    k2 = analysis_session_key(sd / ".")
    assert k1 == k2
    assert try_begin_session_analysis(sd) is True
    assert try_begin_session_analysis(sd / ".") is False
    end_session_analysis(sd)


def test_end_is_idempotent(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    end_session_analysis(sd)
    assert try_begin_session_analysis(sd) is True
    end_session_analysis(sd)
    end_session_analysis(sd)
    assert session_analysis_inflight_count() == 0
