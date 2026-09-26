"""Launch the iced Sol-style session palette (control-plane client only)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ..integrations.control import default_socket_path
from ..integrations.control_client import ControlClient
from ..integrations.daemon import (
    EnsureDaemonResult,
    control_socket_accepts,
    ensure_control_daemon,
    wait_until_control_accepts,
)
from ..paths import resolve_work_and_traces
from .launch import launch_tauri_hud


async def _probe(socket_path: Path) -> None:
    client = ControlClient(socket_path, client_name="groket-hud")
    await client.initialize()


def run_hud(
    *,
    socket_path: Path | None = None,
    work_dir: Path | None = None,
    auto_serve: bool = True,
    dev: bool = False,
    debug: bool = False,
    rebuild: bool = False,
    foreground: bool = False,
    restart: bool = False,
    install_desktop: bool = False,
) -> int:
    """Ensure control owner is live, then launch the iced ``groket-hud`` binary.

    In an editable checkout, missing/stale binaries rebuild with
    ``cargo build --release`` by default. Pass *debug* for an unoptimized
    binary, or *dev* for ``cargo run`` in the checkout.

    By default the HUD is detached in the background (macOS overlay starts as
    an accessory: no Dock / Cmd+Tab until pop-out). Pass *foreground* to attach
    the terminal to the process.

    *install_desktop* only writes user-local icons/launcher entries (no serve,
    no HUD process).

    The HUD is always a **client**. A live TUI or ``groket serve`` already
    holding the socket is success (attach), not an error.

    :returns: Process exit code (0 normal, 1 failure, 127 binary missing).
    """
    if install_desktop:
        from .launch import install_desktop as do_install

        code = do_install(rebuild=rebuild, debug=debug)
        if code == 127:
            sys.stderr.write(
                "error: groket-hud binary not found.\n"
                "From a checkout with Rust installed, ``groket hud`` auto-builds.\n"
                "  groket hud --rebuild --install-desktop\n"
                "Override path with GROKET_HUD_BIN.\n"
            )
        return code

    sock = Path(socket_path or default_socket_path()).expanduser()
    wd, tr = resolve_work_and_traces(work_dir)
    if auto_serve:
        result = ensure_control_daemon(
            socket_path=sock,
            work_dir=wd,
            traces_path=tr,
        )
        # Race: spawn lost the bind to a live TUI/serve — still attach if OK.
        if not result.ok and control_socket_accepts(sock):
            result = EnsureDaemonResult(
                ok=True,
                already_running=True,
                spawned=False,
                pid=result.pid,
                socket_path=sock,
                error="",
            )
        if not result.ok:
            sys.stderr.write(f"error: control owner unavailable: {result.error}\n")
            return 1
        if result.already_running:
            sys.stderr.write(f"groket hud: using existing control owner at {sock}\n")
        if not wait_until_control_accepts(sock, timeout=8.0):
            sys.stderr.write(f"error: control socket not accepting: {sock}\n")
            return 1
        try:
            asyncio.run(_probe(sock))
        except Exception as exc:
            sys.stderr.write(
                f"error: control initialize failed (is an old owner still bound?): {exc}\n"
            )
            return 1

    code = launch_tauri_hud(
        socket_path=sock,
        dev=dev,
        debug=debug,
        rebuild=rebuild,
        foreground=foreground,
        restart=restart,
    )
    if code == 127:
        sys.stderr.write(
            "error: groket-hud binary not found.\n"
            "From a checkout with Rust installed, ``groket hud`` auto-builds.\n"
            "  groket hud --rebuild\n"
            "Unoptimized binary: groket hud --debug\n"
            "Debug cargo run: groket hud --dev\n"
            "Override path with GROKET_HUD_BIN.\n"
        )
        return 127
    return code


__all__ = ["run_hud"]
