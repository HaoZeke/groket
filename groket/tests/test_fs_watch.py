"""TraceTreeWatch relevance and debounce (no long-lived Observer in CI)."""

from __future__ import annotations

import time
from pathlib import Path

from groket.fs_watch import TraceTreeWatch, _path_looks_relevant


def test_path_looks_relevant() -> None:
    assert _path_looks_relevant("/x/updates.jsonl")
    assert _path_looks_relevant("/x/events.jsonl")
    assert _path_looks_relevant("/runs/traces/groket-abc")
    assert not _path_looks_relevant("/x/random.bin")


def test_watch_start_stop(tmp_path: Path) -> None:
    hits: list[int] = []

    def on_change() -> None:
        hits.append(1)

    w = TraceTreeWatch(tmp_path, on_change, debounce_s=0.05)
    assert w.start() is True
    (tmp_path / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    # Wait for debounce + inotify
    for _ in range(40):
        if hits:
            break
        time.sleep(0.05)
    w.stop()
    assert hits, "expected FS callback after writing updates.jsonl"
