"""Locate / stale-detect Tauri HUD binary (no GUI, no cargo)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

from groket.hud import launch as launch_mod
from groket.hud.launch import (
    find_hud_binary,
    hud_binary_is_stale,
    hud_checkout_dir,
)


def test_find_hud_binary_release_or_none() -> None:
    """When the Tauri release binary is built, it is discoverable."""
    found = find_hud_binary()
    release = (
        Path(__file__).resolve().parents[2]
        / "groket-hud"
        / "src-tauri"
        / "target"
        / "release"
        / "groket-hud"
    )
    if release.is_file():
        assert found is not None
        assert found.name == "groket-hud"
        assert found.is_file()
    else:
        # CI without a Rust build: acceptable absence.
        assert found is None or found.is_file()


def test_hud_checkout_dir_in_repo() -> None:
    checkout = hud_checkout_dir()
    assert checkout is not None
    assert (checkout / "src-tauri" / "Cargo.toml").is_file()


def test_hud_binary_is_stale_when_source_newer(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src-tauri" / "src"
    src.mkdir(parents=True)
    (src / "lib.rs").write_text("// old\n", encoding="utf-8")
    binary = checkout / "src-tauri" / "target" / "debug" / "groket-hud"
    binary.parent.mkdir(parents=True)
    binary.write_text("bin", encoding="utf-8")
    binary.chmod(0o755)
    # Source newer than binary
    time.sleep(0.05)
    (src / "lib.rs").write_text("// new\n", encoding="utf-8")
    assert hud_binary_is_stale(binary, checkout) is True
    # Touch binary so it is fresher
    time.sleep(0.05)
    binary.write_text("bin2", encoding="utf-8")
    assert hud_binary_is_stale(binary, checkout) is False


def test_ensure_hud_binary_rebuilds_when_stale(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src-tauri" / "src"
    src.mkdir(parents=True)
    (checkout / "src-tauri" / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (src / "lib.rs").write_text("x", encoding="utf-8")
    built = checkout / "src-tauri" / "target" / "debug" / "groket-hud"
    built.parent.mkdir(parents=True)

    def fake_build(root: Path | None = None) -> Path | None:
        built.write_text("fresh", encoding="utf-8")
        built.chmod(0o755)
        return built

    with (
        patch.object(launch_mod, "hud_checkout_dir", return_value=checkout),
        patch.object(launch_mod, "find_hud_binary", return_value=None),
        patch.object(launch_mod, "build_hud_debug", side_effect=fake_build) as mock_build,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("GROKET_HUD_BIN", None)
        out = launch_mod.ensure_hud_binary()
    assert out == built
    mock_build.assert_called_once()


def test_launch_tauri_hud_detaches_by_default(tmp_path: Path) -> None:
    """Default path spawns the binary in a new session and returns without waiting."""
    binary = tmp_path / "groket-hud"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    sock = tmp_path / "control.sock"

    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4242
        code = launch_mod.launch_tauri_hud(socket_path=sock)
    assert code == 0
    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("start_new_session") is True


def test_launch_tauri_hud_passes_initial_selection_environment(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    sock = tmp_path / "control.sock"

    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4242
        code = launch_mod.launch_tauri_hud(
            socket_path=sock,
            extra_env={
                "GROKET_HUD_INITIAL_SESSION": "/tmp/session-a",
                "GROKET_HUD_INITIAL_PROMPT_INDEX": "7",
            },
        )
    assert code == 0
    env = mock_popen.call_args.kwargs["env"]
    assert env["GROKET_HUD_INITIAL_SESSION"] == "/tmp/session-a"
    assert env["GROKET_HUD_INITIAL_PROMPT_INDEX"] == "7"


def test_launch_tauri_hud_skips_when_already_running(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=True),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
        patch.object(launch_mod.subprocess, "run") as mock_run,
    ):
        code = launch_mod.launch_tauri_hud(socket_path=tmp_path / "c.sock")
    assert code == 0
    mock_popen.assert_not_called()
    mock_run.assert_not_called()


def test_launch_tauri_hud_foreground_waits(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(
            launch_mod.subprocess, "run", return_value=type("R", (), {"returncode": 0})()
        ) as mock_run,
    ):
        code = launch_mod.launch_tauri_hud(
            socket_path=tmp_path / "c.sock",
            foreground=True,
        )
    assert code == 0
    mock_run.assert_called_once()


def test_launch_tauri_hud_restart_stops_then_spawns(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "stop_hud_processes", return_value=1) as mock_stop,
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 99
        code = launch_mod.launch_tauri_hud(
            socket_path=tmp_path / "c.sock",
            restart=True,
        )
    assert code == 0
    mock_stop.assert_called_once()
    mock_popen.assert_called_once()
