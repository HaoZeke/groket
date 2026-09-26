"""Control socket singleton: second owner soft-fails instead of crashing."""

from __future__ import annotations

import asyncio
import socket
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
from groket.ui.app import TraceEvalApp


def _short_sock(name: str) -> Path:
    """Short unique AF_UNIX path (macOS path limit + multi-user / xdist safe)."""
    root = Path(tempfile.mkdtemp(prefix="groket-ctl-"))
    return root / name


@pytest.mark.asyncio
async def test_second_control_server_raises_socket_in_use() -> None:
    control = import_module("groket.integrations.control")
    daemon = import_module("groket.integrations.daemon")
    sock = _short_sock("singleton")
    first = control.ControlServer(socket_path=sock)
    second = control.ControlServer(socket_path=sock)
    await first.start()
    try:
        with pytest.raises(control.ControlSocketInUse) as exc_info:
            await second.start()
        assert exc_info.value.socket_path == sock
        # Owner pid is written into the lock file for serve stop discovery.
        assert daemon.read_control_lock_pid(sock) == __import__("os").getpid()
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_control_server_takes_over_stale_socket_file() -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("stale")
    holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    holder.bind(str(sock))
    holder.close()  # closed without listen/unlink: a crashed owner's leftover
    server = control.ControlServer(socket_path=sock)
    await server.start()
    try:
        assert sock.is_socket()
    finally:
        await server.close()
    assert not sock.exists()


@pytest.mark.asyncio
async def test_failed_starter_does_not_unlink_live_socket() -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("keep")
    first = control.ControlServer(socket_path=sock)
    second = control.ControlServer(socket_path=sock)
    await first.start()
    try:
        with pytest.raises(control.ControlSocketInUse):
            await second.start()
        # The loser's close() must not remove the winner's live socket.
        await second.close()
        assert sock.is_socket()
        reader, writer = await asyncio.open_unix_connection(sock)
        writer.close()
        await writer.wait_closed()
        _ = reader
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_tui_attaches_when_control_socket_already_owned(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("tui-singleton")
    owner = control.ControlServer(socket_path=sock)
    await owner.start()
    try:
        work = tmp_path / "work"
        traces = work / "runs" / "traces"
        traces.mkdir(parents=True)
        app = TraceEvalApp(
            work_dir=work,
            traces_path=traces,
            control_socket=sock,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(200):
                await pilot.pause()
                if app.is_control_client():
                    break
            assert app.is_running
            assert app.is_control_client()
            assert not app.is_control_owner()
            # Original owner still holds the socket.
            assert sock.exists()
    finally:
        await owner.close()
