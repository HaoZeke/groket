"""Thread-safe in-memory context samples."""

from __future__ import annotations

from pathlib import Path

from groket.models import SessionMeta
from groket.session.context_samples import ContextSampleStore, context_compact_from_meta


def test_record_dedupes_identical_compact() -> None:
    store = ContextSampleStore()
    meta = SessionMeta(
        session_id="s",
        session_dir=Path("/tmp/s"),
        context_window_usage_pct=35,
        context_tokens_used=178996,
        context_window_tokens=500000,
    )
    assert store.record(0, meta) is True
    assert store.record(0, meta) is False
    meta2 = SessionMeta(
        session_id="s",
        session_dir=Path("/tmp/s"),
        context_window_usage_pct=40,
        context_tokens_used=200000,
        context_window_tokens=500000,
    )
    assert store.record(0, meta2) is True
    assert store.compact_for_turn(0).startswith("40%")
    assert store.compact_for_turn(1) == ""
    assert 0 in store.compact_by_turn()
    store.clear()
    assert store.compact_by_turn() == {}


def test_context_compact_from_meta_empty() -> None:
    meta = SessionMeta(session_id="s", session_dir=Path("/tmp/s"))
    assert context_compact_from_meta(meta) == ""
    assert context_compact_from_meta(None) == ""


def test_record_skips_missing_meta() -> None:
    store = ContextSampleStore()
    assert store.record(0, None) is False
    meta = SessionMeta(session_id="s", session_dir=Path("/tmp/s"))
    assert store.record(0, meta) is False


def test_record_skips_empty_compact() -> None:
    store = ContextSampleStore()

    class _Meta:
        has_context_usage = True
        context_usage_compact = ""
        context_usage_str = ""
        context_window_usage_pct = 1
        context_tokens_used = 1
        context_window_tokens = 500000

    assert store.record(0, _Meta()) is False  # type: ignore[arg-type]


def test_context_compact_from_meta_falls_back_to_fmt() -> None:
    class _Meta:
        has_context_usage = True
        context_usage_compact = ""
        context_window_usage_pct = 35
        context_tokens_used = 178996
        context_window_tokens = 500000

    label = context_compact_from_meta(_Meta())  # type: ignore[arg-type]
    assert "35%" in label
    assert "179k" in label or "178" in label


def test_context_sample_store_thread_safe() -> None:
    import threading

    store = ContextSampleStore()
    meta = SessionMeta(
        session_id="s",
        session_dir=Path("/tmp/s"),
        context_window_usage_pct=35,
        context_tokens_used=178996,
        context_window_tokens=500000,
    )
    errors: list[BaseException] = []

    def _worker(turn: int) -> None:
        try:
            for i in range(50):
                m = SessionMeta(
                    session_id="s",
                    session_dir=Path("/tmp/s"),
                    context_window_usage_pct=min(99, 10 + turn + i),
                    context_tokens_used=1000 * (turn + 1) + i,
                    context_window_tokens=500000,
                )
                store.record(turn, m)
                store.compact_for_turn(turn)
                store.compact_by_turn()
        except BaseException as exc:  # noqa: BLE001 — collect worker failures
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert store.record(0, meta) in (True, False)
    assert store.compact_by_turn()
