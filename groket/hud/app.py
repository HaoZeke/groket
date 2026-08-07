"""Launch the Tauri Sol-style session palette (control-plane client only)."""

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


async def _select(socket_path: Path, session: Path, prompt_index: int | None) -> None:
    client = ControlClient(socket_path, client_name="groket-hud-launcher")
    await client.session_open(str(session), prompt_index=prompt_index)


def run_hud(
    *,
    socket_path: Path | None = None,
    work_dir: Path | None = None,
    auto_serve: bool = True,
    dev: bool = False,
    rebuild: bool = False,
    foreground: bool = False,
    restart: bool = False,
    initial_session: Path | None = None,
    initial_prompt_index: int | None = None,
) -> int:
    """Ensure control owner is live, then launch the Tauri ``groket-hud`` binary.

    In an editable checkout, missing/stale binaries are rebuilt with
    ``cargo build`` (debug) unless *dev* runs ``npm run dev`` instead.

    By default the HUD is detached in the background (Sol-style agent: no Dock
    / ⌘Tab on macOS). Pass *foreground* to attach the terminal to the process.

    The HUD is always a **client**. A live TUI or ``groket serve`` already
    holding the socket is success (attach), not an error.

    :returns: Process exit code (0 normal, 1 failure, 127 binary missing).
    """
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

    initial = Path(initial_session).expanduser() if initial_session is not None else None
    extra_env: dict[str, str] = {}
    if initial is not None:
        extra_env["GROKET_HUD_INITIAL_SESSION"] = str(initial)
        if initial_prompt_index is not None:
            extra_env["GROKET_HUD_INITIAL_PROMPT_INDEX"] = str(initial_prompt_index)

    code = launch_tauri_hud(
        socket_path=sock,
        extra_env=extra_env or None,
        dev=dev,
        rebuild=rebuild,
        foreground=foreground,
        restart=restart,
    )
    if code == 0 and initial is not None:
        try:
            asyncio.run(_select(sock, initial, initial_prompt_index))
        except Exception as exc:
            sys.stderr.write(f"error: HUD session selection failed: {exc}\n")
            return 1
    if code == 127:
        sys.stderr.write(
            "error: groket-hud binary not found.\n"
            "From a checkout with Rust installed, ``groket hud`` auto-builds debug.\n"
            "Or:\n"
            "  cd groket-hud && npm install && npm run build\n"
            "Hot reload:\n"
            "  groket hud --dev\n"
            "Override path with GROKET_HUD_BIN.\n"
        )
        return 127
    return code


__all__ = ["run_hud"]
