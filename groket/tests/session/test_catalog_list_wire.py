"""session/list wire richness: real numEvents + structured context."""

from __future__ import annotations

import json
from pathlib import Path

from groket.session.catalog import session_catalog_row, session_meta_from_catalog_row


def _write_list_fixture(
    root: Path,
    name: str,
    *,
    num_messages: int,
    ctx_pct: int,
    tokens_used: int,
    window: int,
) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": f"Session {name}",
                "num_messages": num_messages,
                "task_id": f"task-{name}",
            }
        ),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text(
        json.dumps(
            {
                "toolCallCount": 4,
                "errorCount": 1,
                "sessionDurationSeconds": 99.5,
                "contextWindowUsage": ctx_pct,
                "contextTokensUsed": tokens_used,
                "contextWindowTokens": window,
            }
        ),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_list_session_catalog_newest_first(tmp_path: Path) -> None:
    """session/list catalog is always newest activity first."""
    import os
    import time

    from groket.session.catalog import list_session_catalog

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    old = _write_list_fixture(
        traces, "old-sess", num_messages=1, ctx_pct=10, tokens_used=1, window=100
    )
    time.sleep(0.05)
    new = _write_list_fixture(
        traces, "new-sess", num_messages=2, ctx_pct=20, tokens_used=2, window=100
    )
    # Bump new session mtime so sortEpoch prefers it even without ISO timestamps.
    now = time.time()
    os.utime(new / "updates.jsonl", (now + 10, now + 10))
    os.utime(old / "updates.jsonl", (now - 100, now - 100))
    rows = list_session_catalog(work, include_host=False)
    ids = [r["sessionId"] for r in rows]
    assert ids[0] == "new-sess"
    assert "old-sess" in ids
    assert rows[0]["sortEpoch"] >= rows[-1]["sortEpoch"]


def test_session_catalog_row_num_events_from_summary_messages(tmp_path: Path) -> None:
    """Work/eval list rows must not leave Events at 0 when summary has messages."""
    traces = tmp_path / "runs" / "traces"
    sd = _write_list_fixture(
        traces,
        "work-rich",
        num_messages=27,
        ctx_pct=40,
        tokens_used=1200,
        window=128_000,
    )
    row = session_catalog_row(sd, origin="work")
    assert row is not None
    assert row["numEvents"] == 27
    assert row["durationSeconds"] == 99.5
    assert row["toolCallCount"] == 4
    assert row["errorCount"] == 1
    assert row["contextWindowUsagePct"] == 40
    assert row["contextTokensUsed"] == 1200
    assert row["contextWindowTokens"] == 128_000
    assert "40" in str(row["contextUsageCompact"])
    assert (
        "128" in str(row["contextUsageCompact"]) or "k" in str(row["contextUsageCompact"]).lower()
    )


def test_session_meta_hydrate_preserves_context_fraction(tmp_path: Path) -> None:
    """Attach hydrate must rebuild full compact context, not pct-only."""
    traces = tmp_path / "runs" / "traces"
    sd = _write_list_fixture(
        traces,
        "attach-ctx",
        num_messages=12,
        ctx_pct=40,
        tokens_used=1200,
        window=128_000,
    )
    row = session_catalog_row(sd, origin="work")
    assert row is not None
    meta = session_meta_from_catalog_row(row)
    assert meta is not None
    assert meta.num_events == 12
    assert meta.context_window_usage_pct == 40
    assert meta.context_tokens_used == 1200
    assert meta.context_window_tokens == 128_000
    compact = meta.context_usage_compact
    assert "40" in compact
    # Token fraction must survive hydrate (owner-style column).
    assert "1.2" in compact or "1200" in compact or "1k" in compact.lower()


def test_host_catalog_row_also_uses_message_proxy(tmp_path: Path) -> None:
    host = tmp_path / "host"
    sd = _write_list_fixture(
        host,
        "host-rich",
        num_messages=9,
        ctx_pct=10,
        tokens_used=100,
        window=1000,
    )
    row = session_catalog_row(sd, origin="host")
    assert row is not None
    assert row["numEvents"] == 9
