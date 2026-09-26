"""LogBuffer append, snapshot, listeners, and capacity behaviour."""

from __future__ import annotations

from groket.runs.services import LogBuffer, LogLine


def test_log_buffer_lifecycle():
    buf = LogBuffer(maxlen=3)
    assert len(buf) == 0
    seen: list[tuple[str, str]] = []

    def bad_listener(s, t):
        raise RuntimeError("listener boom")

    def ok_listener(s, t):
        seen.append((s, t))

    buf.add_listener(ok_listener)
    buf.add_listener(ok_listener)  # dedupe
    buf.add_listener(bad_listener)
    buf.enable_live_notify(True)
    buf.append("stdout", "line1")
    buf.extend([("stderr", "e1"), ("stdout", "line2")])
    assert len(buf) == 3
    buf.append("stdout", "line3")  # ring overflow
    assert len(buf) == 3
    snap = buf.snapshot()
    assert all(isinstance(x, LogLine) for x in snap)
    assert buf.snapshot(max_lines=1)
    text = buf.snapshot_text(include_source=True)
    assert "line" in text or "e1" in text
    assert buf.snapshot_text(include_source=False)
    buf.remove_listener(ok_listener)
    buf.remove_listener(ok_listener)  # missing ok
    buf.enable_live_notify(False)
    buf.append("x", "y")
    buf.clear()
    assert len(buf) == 0
