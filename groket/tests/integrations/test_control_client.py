"""ControlClient against a live ControlServer."""

from __future__ import annotations

import errno
import json
import tempfile
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import pytest


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="groket-client-"))
    return root / name


def test_is_transient_unix_connect_error_eagain() -> None:
    client_mod = import_module("groket.integrations.control_client")
    eagain = OSError(errno.EAGAIN, "Resource temporarily unavailable")
    assert client_mod.is_transient_unix_connect_error(eagain) is True
    assert client_mod.is_transient_unix_connect_error(ConnectionRefusedError()) is True
    assert client_mod.is_transient_unix_connect_error(FileNotFoundError()) is True
    assert client_mod.is_transient_unix_connect_error(OSError(errno.EPERM, "denied")) is False
    assert client_mod.is_transient_unix_connect_error(TimeoutError()) is False
    # macOS-style message when errno mapping is odd
    other = OSError("Resource temporarily unavailable (os error 35)")
    assert client_mod.is_transient_unix_connect_error(other) is True


@pytest.mark.asyncio
async def test_open_unix_connection_retries_eagain(tmp_path: Path) -> None:
    """Transient EAGAIN on connect is retried until success."""
    client_mod = import_module("groket.integrations.control_client")
    control = import_module("groket.integrations.control")
    sock = _short_sock("retry.sock")
    server = control.ControlServer(socket_path=sock)
    await server.start()
    try:
        calls = {"n": 0}
        real_open = client_mod.asyncio.open_unix_connection

        async def flaky_open(path: object, *args: object, **kwargs: object):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
            return await real_open(path, *args, **kwargs)

        with patch.object(client_mod.asyncio, "open_unix_connection", side_effect=flaky_open):
            reader, writer = await client_mod.open_unix_connection_retrying(
                sock,
                timeout=2.0,
                budget=2.0,
            )
            writer.close()
            await writer.wait_closed()
        assert calls["n"] >= 3
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_client_initialize_and_list(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")
    catalog = [
        {
            "sessionId": "s1",
            "path": str(tmp_path / "s1"),
            "title": "One",
            "label": "One",
            "model": "grok",
            "status": "complete",
            "outcome": "success",
            "origin": "work",
        }
    ]
    sock = _short_sock("client.sock")
    server = control.ControlServer(socket_path=sock, list_sessions=lambda: catalog)
    await server.start()
    try:
        client = client_mod.ControlClient(sock, client_name="unit")
        init = await client.initialize()
        assert init["protocolVersion"] == 1
        listed = await client.session_list(query="One")
        assert listed["matched"] == 1
        assert listed["sessions"][0]["sessionId"] == "s1"
        assert await client_mod.control_socket_is_live(sock) is True
    finally:
        await server.close()
    assert await client_mod.control_socket_is_live(sock) is False


@pytest.mark.asyncio
async def test_control_client_session_list_beyond_default_stream_limit(
    tmp_path: Path,
) -> None:
    """TUI attach uses limit≈500 rich rows — must not hit 64KiB readline limit.

    Historical gap: unit tests used 1–40 tiny rows so the default asyncio
    StreamReader limit never fired. This builds a wire body past 64KiB.
    """
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")

    def _row(i: int) -> dict:
        pad = "x" * 80
        return {
            "sessionId": f"sess-{i:04d}-{pad}",
            "path": str(tmp_path / "deep" / ("seg/" * 6) / f"sess-{i:04d}"),
            "title": f"Session title {i} " + ("word " * 24),
            "label": f"Label {i} " + ("lab " * 12),
            "model": "grok-4-heavy-model-name",
            "status": "complete",
            "outcome": "success",
            "origin": "host" if i % 3 == 0 else "work",
            "taskId": f"task-{i:04d}",
            "durationSeconds": float(i),
            "numEvents": i * 3,
            "contextUsageCompact": f"{(i % 90) + 5}% · {1000 + i}k/128k",
            "contextWindowUsagePct": (i % 90) + 5,
            "contextTokensUsed": 10_000 + i,
            "contextWindowTokens": 128_000,
            "toolCallCount": i % 20,
            "errorCount": i % 3,
        }

    catalog = [_row(i) for i in range(280)]
    # Prove the RPC line would exceed the default 64 KiB StreamReader limit.
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "sessions": catalog,
            "total": len(catalog),
            "matched": len(catalog),
        },
    }
    wire_bytes = len(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))
    assert wire_bytes > 65_536, f"fixture too small: {wire_bytes} bytes"

    sock = _short_sock("biglist.sock")
    server = control.ControlServer(socket_path=sock, list_sessions=lambda: catalog)
    await server.start()
    try:
        client = client_mod.ControlClient(sock, client_name="tui-attach", timeout=30.0)
        listed = await client.session_list(limit=500)
        assert listed["matched"] == 280
        assert len(listed["sessions"]) == 280
        assert listed["sessions"][0]["sessionId"].startswith("sess-0000-")
        assert listed["sessions"][-1]["sessionId"].startswith("sess-0279-")
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_client_session_list_all_drains_pages(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")
    catalog = [
        {
            "sessionId": f"s{i}",
            "path": str(tmp_path / f"s{i}"),
            "title": f"Title {i}",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "success",
            "origin": "work",
        }
        for i in range(5)
    ]
    sock = _short_sock("paged.sock")
    server = control.ControlServer(socket_path=sock, list_sessions=lambda: catalog)
    await server.start()
    try:
        client = client_mod.ControlClient(sock, client_name="drain")
        listed = await client.session_list_all(page=2)
        assert listed["matched"] == 5
        assert listed["total"] == 5
        assert [row["sessionId"] for row in listed["sessions"]] == [
            "s0",
            "s1",
            "s2",
            "s3",
            "s4",
        ]
        first = await client.session_list(limit=2)
        assert [row["sessionId"] for row in first["sessions"]] == ["s0", "s1"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_client_raises_on_rpc_error() -> None:
    control = import_module("groket.integrations.control")
    client_mod = import_module("groket.integrations.control_client")
    sock = _short_sock("err.sock")
    server = control.ControlServer(socket_path=sock)
    await server.start()
    try:
        client = client_mod.ControlClient(sock)
        with pytest.raises(control.ControlError) as exc_info:
            await client.request("missing/method")
        assert exc_info.value.code == -32601
    finally:
        await server.close()


def test_client_module_exports() -> None:
    client_mod = import_module("groket.integrations.control_client")
    assert hasattr(client_mod, "ControlClient")
    assert hasattr(client_mod, "control_socket_is_live")
    _ = json  # keep import used if ruff cares elsewhere
