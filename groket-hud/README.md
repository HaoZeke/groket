# groket-hud

Sol-style session **command palette** for the local groket control plane
(JSON-RPC over Unix socket). Not a second session owner — talk to
``groket serve``. Drawn with **iced** (Rust, no JavaScript).

## Features

- Always opens as a centered, always-on-top overlay (780x560) on the
  display that has the pointer. The pop-out icon in the search bar opens
  a decorated desktop window. Close that window to leave the HUD running;
  the summon hotkey brings the overlay back. There is no switch back to
  palette from the window.
- Colors follow the TUI ``theme`` name in ``config.json`` (baked Textual
  tokens in ``groket-hud/assets/textual-themes.json``; regenerate with
  ``make hud-themes``). Dock and window icon use the light 1024 app icon.
  The search bar uses the colour mark on a light ``$surface`` and the reverse
  mark on a dark one (gruvbox, nord) at 32px.
- Turn and timeline cards show quiet pills for findings, notes, and errors;
  **Add note** opens the Notes tab with turn (and event) filled in.
  Notes tab **Edit** / **Delete** match the TUI (delete is two presses).
  Schema fields from overview are the form (same as TUI).
  The HUD does not launch runs, recipes, or Docker.
- Typeahead over the drained ``session/list`` catalog
- Detail: overview, turns, live timeline, findings, notes
- Live refresh while the palette is open: selected running/awaiting turns
  re-fetch overview and the timeline tail about every 3s (idle sessions slower).
- Global hotkey: **Cmd+Shift+G** (macOS) / **Ctrl+Shift+G** (Linux/Windows) by default;
  override with ``~/.groket/config.json`` ``hud.global_shortcut`` or env
  ``GROKET_HUD_SHORTCUT``
- ``groket hud`` detaches; ``groket hud --restart`` replaces a running agent
- Linux StatusNotifier tray (Swaybar, Waybar, and other SNI hosts): left-click
  or **Show HUD** reveals and focuses the palette. **Quit Groket HUD** exits
  the HUD process only; ``groket serve`` stays up. Escape hides the overlay
  and leaves the tray item in place. A missing tray host fails Linux startup.
- Overlay: hides on **Esc** or the summon hotkey. It is a floating card
  (macOS system dialog / Linux override-redirect) so a tiler does not
  insert it. Decorated window: stays open until you close it; the summon
  hotkey focuses it. That window is a normal desktop client, so a tiler
  (yabai, i3, sway) tiles it and a stacking desktop just shows it.
  Closing the window does not stop the HUD process. Tiling shells unfocus
  a new overlay on map, so blur does not hide it.

## Prerequisites

- Rust (stable)
- Running control owner: ``groket serve -d`` (or auto-start via ``groket hud``)
- **Linux:** graphics packages for iced (``libxkbcommon-dev``, Wayland/X11 as
  your session uses). No WebKitGTK.

``uv run groket hud`` builds with cargo when sources are newer.

## Develop

```bash
uv run groket hud             # release binary; rebuilds when sources are newer
uv run groket hud --restart   # stop the running HUD and start again
uv run groket hud --rebuild   # force cargo rebuild
uv run groket hud --dev       # cargo run (debug)
uv run groket hud --debug     # unoptimized cargo binary
```

``make hud-check`` (from the repo root) checks the Textual theme map, rustfmt,
clippy (``-D warnings``), and ``cargo test``. When ``cargo llvm-cov`` is
installed it also applies a line fail-under on non-paint HUD logic (view,
window loop, and the Unix socket client are omitted from that floor).

## Env

| Variable | Role |
|----------|------|
| ``GROKET_CONTROL_SOCKET`` | Override control Unix socket path |
| ``GROKET_HUD_BIN`` | Use this binary instead of building |
| ``GROKET_HUD_SHORTCUT`` | Override global summon chord |
| ``GROKET_HUD_FOREGROUND`` | Attach the HUD to this terminal |
| ``GROKET_HUD_DEV`` | Same as ``--dev`` |
| ``GROKET_HUD_DEBUG`` | Same as ``--debug`` |
| ``GROKET_HUD_LOG`` | Append-only error log path (default ``~/.groket/hud.log``) |
| ``GROKET_HUD_SHOW_ON_START`` | Show and focus the palette when the process starts (``1`` / ``true`` / ``yes``) |
