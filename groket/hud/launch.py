"""Locate, auto-build, and launch the Tauri groket-hud binary."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SOURCE_GLOBS = (
    "src/**/*",
    "src-tauri/src/**/*",
    "src-tauri/Cargo.toml",
    "src-tauri/Cargo.lock",
    "src-tauri/tauri.conf.json",
    "src-tauri/build.rs",
    "package.json",
    "package-lock.json",
)


def _repo_root() -> Path:
    # groket/hud/launch.py → parents[2] = repo root when editable checkout
    return Path(__file__).resolve().parents[2]


def hud_checkout_dir() -> Path | None:
    """Return the ``groket-hud`` package dir in an editable checkout, if present."""
    cand = _repo_root() / "groket-hud"
    if (cand / "src-tauri" / "Cargo.toml").is_file():
        return cand
    return None


def _debug_binary(checkout: Path) -> Path:
    return checkout / "src-tauri" / "target" / "debug" / "groket-hud"


def _release_binary(checkout: Path) -> Path:
    return checkout / "src-tauri" / "target" / "release" / "groket-hud"


def find_hud_binary() -> Path | None:
    """Return path to a built ``groket-hud`` binary, if any.

    Preference: ``GROKET_HUD_BIN``, then ``PATH``, then the newer of
    release/debug under an editable checkout.
    """
    env = os.environ.get("GROKET_HUD_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
    which = shutil.which("groket-hud")
    if which:
        return Path(which)
    checkout = hud_checkout_dir()
    if checkout is None:
        return None
    candidates = [
        p
        for p in (_release_binary(checkout), _debug_binary(checkout))
        if p.is_file() and os.access(p, os.X_OK)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _source_mtimes(checkout: Path) -> list[float]:
    times: list[float] = []
    for pattern in _SOURCE_GLOBS:
        for path in checkout.glob(pattern):
            if path.is_file():
                try:
                    times.append(path.stat().st_mtime)
                except OSError:
                    continue
    return times


def hud_binary_is_stale(binary: Path, checkout: Path) -> bool:
    """True when *binary* is older than any tracked HUD source file."""
    if not binary.is_file():
        return True
    try:
        bin_mtime = binary.stat().st_mtime
    except OSError:
        return True
    sources = _source_mtimes(checkout)
    if not sources:
        return False
    return max(sources) > bin_mtime


def build_hud_debug(checkout: Path | None = None) -> Path | None:
    """``cargo build`` debug ``groket-hud`` in the checkout; return the binary path.

    :returns: Path to the debug binary, or None when cargo is missing / build fails.
    """
    root = checkout or hud_checkout_dir()
    if root is None:
        return None
    cargo = shutil.which("cargo")
    if cargo is None:
        sys.stderr.write("error: cargo not found on PATH; install Rust to auto-build groket-hud\n")
        return None
    tauri_dir = root / "src-tauri"
    sys.stderr.write("groket hud: building debug groket-hud (cargo build)…\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run(
            [cargo, "build", "--manifest-path", str(tauri_dir / "Cargo.toml")],
            cwd=str(tauri_dir),
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"error: cargo build failed to start: {exc}\n")
        return None
    if proc.returncode != 0:
        sys.stderr.write(f"error: cargo build exited {proc.returncode}\n")
        return None
    binary = _debug_binary(root)
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary
    sys.stderr.write(f"error: build finished but binary missing: {binary}\n")
    return None


def ensure_hud_binary(*, rebuild: bool = False) -> Path | None:
    """Return a runnable HUD binary, rebuilding debug from the checkout if needed.

    Rebuild when *rebuild* is true, the binary is missing, or HUD sources are
    newer than the on-disk binary (editable checkout only).
    """
    env = os.environ.get("GROKET_HUD_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        sys.stderr.write(f"error: GROKET_HUD_BIN not executable: {p}\n")
        return None

    checkout = hud_checkout_dir()
    found = find_hud_binary()
    if checkout is None:
        return found
    if found is not None:
        try:
            found.resolve().relative_to(checkout.resolve())
        except ValueError:
            return found

    need_build = rebuild or found is None or hud_binary_is_stale(found, checkout)
    if not need_build:
        return found

    built = build_hud_debug(checkout)
    return built or find_hud_binary()


def launch_tauri_dev(
    *,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Run ``npm run dev`` / ``tauri dev`` in the checkout (hot reload).

    :returns: Process exit code, or 127 when the checkout or npm is unavailable.
    """
    checkout = hud_checkout_dir()
    if checkout is None:
        sys.stderr.write("error: groket-hud checkout not found (editable install only)\n")
        return 127
    npm = shutil.which("npm")
    if npm is None:
        sys.stderr.write("error: npm not found on PATH (needed for groket hud --dev)\n")
        return 127
    env = os.environ.copy()
    env["GROKET_CONTROL_SOCKET"] = str(socket_path)
    env.update(_hud_shortcut_env())
    if extra_env:
        env.update(extra_env)
    sys.stderr.write(f"groket hud: tauri dev in {checkout}\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run(
            [npm, "run", "dev"],
            cwd=str(checkout),
            env=env,
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"error: could not start npm run dev: {exc}\n")
        return 1
    return int(proc.returncode)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _hud_shortcut_env() -> dict[str, str]:
    """Pass config shortcut to the binary unless already set in the environment."""
    if os.environ.get("GROKET_HUD_SHORTCUT", "").strip():
        return {}
    try:
        from ..ui.prefs import hud_global_shortcut
    except Exception:
        return {}
    chord = hud_global_shortcut()
    if not chord:
        return {}
    return {"GROKET_HUD_SHORTCUT": chord}


def hud_process_running() -> bool:
    """True when a ``groket-hud`` process is already alive on this machine."""
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "groket-hud"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def stop_hud_processes(*, wait_s: float = 1.5) -> int:
    """SIGTERM then SIGKILL any ``groket-hud`` processes. Return how many were seen."""
    try:
        listed = subprocess.run(
            ["pgrep", "-x", "groket-hud"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 0
    pids = [p for p in (listed.stdout or "").split() if p.isdigit()]
    if not pids:
        return 0
    # Exact name only (-x): does not match the parent shell argv.
    subprocess.run(["kill"] + pids, check=False, capture_output=True)
    deadline = time.monotonic() + max(0.1, wait_s)
    while time.monotonic() < deadline and hud_process_running():
        time.sleep(0.05)
    if hud_process_running():
        subprocess.run(["kill", "-9"] + pids, check=False, capture_output=True)
        time.sleep(0.05)
    return len(pids)


def launch_tauri_hud(
    *,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
    dev: bool = False,
    rebuild: bool = False,
    foreground: bool | None = None,
    restart: bool = False,
) -> int:
    """Launch the Tauri palette (built binary, or ``tauri dev`` when *dev*).

    When not *dev*, ensures a debug/release binary exists for an editable
    checkout (auto ``cargo build`` if missing or sources are newer).

    By default the binary is **detached** (Sol-style agent): ``groket hud``
    returns after spawn; the palette stays out of the Dock / ⌘Tab via macOS
    accessory activation. Use *foreground* / ``GROKET_HUD_FOREGROUND=1`` to
    attach the terminal to the process (useful for debugging).

    *restart* stops any existing ``groket-hud`` first, then starts a new one.

    :returns: Process exit code when the child exits (or 0 after detach),
        or 127 if unavailable.
    """
    if dev or _truthy_env("GROKET_HUD_DEV"):
        return launch_tauri_dev(socket_path=socket_path, extra_env=extra_env)

    binary = ensure_hud_binary(rebuild=rebuild)
    if binary is None:
        return 127
    env = os.environ.copy()
    env["GROKET_CONTROL_SOCKET"] = str(socket_path)
    env.update(_hud_shortcut_env())
    if extra_env:
        env.update(extra_env)

    attach = bool(foreground) if foreground is not None else _truthy_env("GROKET_HUD_FOREGROUND")
    chord_hint = env.get("GROKET_HUD_SHORTCUT", "").strip() or "⌘⇧G / Ctrl+Shift+G"

    if restart:
        n = stop_hud_processes()
        if n:
            sys.stderr.write(f"groket hud: stopped {n} running process(es)\n")
    elif not attach and hud_process_running():
        sys.stderr.write(
            "groket hud: already running in the background "
            f"(summon with {chord_hint}; use --restart to replace)\n"
        )
        return 0

    logger.info("launching HUD binary %s (foreground=%s)", binary, attach)
    sys.stderr.write(f"groket hud: {binary}\n")
    if env.get("GROKET_HUD_SHORTCUT"):
        sys.stderr.write(f"groket hud: GROKET_HUD_SHORTCUT={env['GROKET_HUD_SHORTCUT']}\n")
    sys.stderr.flush()

    if attach:
        try:
            proc = subprocess.run([str(binary)], env=env, check=False)
        except OSError as exc:
            sys.stderr.write(f"error: could not launch {binary}: {exc}\n")
            return 1
        return int(proc.returncode)

    # Detach: new session, no inherited stdio — agent stays until killed.
    try:
        child = subprocess.Popen(
            [str(binary)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        sys.stderr.write(f"error: could not launch {binary}: {exc}\n")
        return 1
    sys.stderr.write(
        f"groket hud: background pid {child.pid} (summon: {chord_hint}; not in Dock or ⌘Tab)\n"
    )
    return 0


__all__ = [
    "build_hud_debug",
    "ensure_hud_binary",
    "find_hud_binary",
    "hud_binary_is_stale",
    "hud_checkout_dir",
    "hud_process_running",
    "launch_tauri_dev",
    "launch_tauri_hud",
    "stop_hud_processes",
]
