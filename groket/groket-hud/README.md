# groket-hud

Sol-style session **command palette** for the local groket control plane
(JSON-RPC over Unix socket). Not a second session owner — talk to
``groket serve``. Drawn with **iced** and **icedtea** 0.4 (Rust, no JavaScript).

## Features

- Always opens as a centered, always-on-top overlay (780x560) on the
  display that has the pointer. The pop-out icon in the search bar opens
  a decorated desktop window. Close that window to leave the HUD running;
  the summon hotkey brings the overlay back. There is no switch back to
  palette from the window.
- Colors follow the TUI ``theme`` name in ``config.json`` (baked Textual
  tokens in ``groket-hud/assets/textual-themes.json``; regenerate with
  ``make hud-themes``). Window / tray / notify use the cream three-bar tray
  tile (128 window, 64 tray/notify). ``--install-desktop`` and the macOS Dock
  tile (when popped out to a normal app) use the square app icon. The search
  bar uses the colour mark on a light ``$surface`` and the reverse mark on a
  dark one (gruvbox, nord) at 32px.
- Turn and timeline cards show quiet pills for findings, notes, and errors;
  **Add note** opens the Notes tab with turn (and event) filled in.
  A go-to icon (arrow into a bar) loads **that turn’s events** only
  (``session/timeline`` with ``promptIndex``). Findings jump the same way
  when they name an event; otherwise they open Overview.
  Notes tab **Edit** / **Delete** match the TUI (delete is two presses).
  Schema fields from overview are the form (same as TUI).
  The HUD does not launch runs, recipes, or Docker.
- Spotlight session pick: every summon shows **Recent** + search (not the
  last open session). Type to filter; **↑/↓** move the highlight (also
  while typing); **Enter** / click opens browse. **Home/End** and
  **Page Up/Down** jump. No auto-open. After a pick, focus stays in
  browse (not yanked back into session search). Type in search again to
  switch sessions. No permanent left rail.
- **Keyboard in browse:** **Tab** / **Shift+Tab** (or **Ctrl+1…5**) cycle
  panes. **Enter** drills Overview → Turns → turn-scoped Timeline → event
  detail → next event. **↑/↓** move the list highlight; in event detail
  they step events and cross into the next/previous turn when scoped.
  **Esc** leaves event detail on the current row (footer ``Esc · timeline``),
  then hides the overlay.
- Browse defaults to **Overview**. Picking a session loads overview only —
  it does not fetch the session event list.
  **Turns** is a fixed list of prompt cards (status, tool counts, marks) with
  search, **Add note**, and **Go to Timeline** — click a card (or **Enter**)
  opens **Timeline** filtered to **that turn’s events** (not a single event
  drawer). Full assistant text and tools live on **Timeline**.
  **Timeline** (same name as the TUI tab) has a turn pick list (defaults to
  the turn you jumped from) plus type filter and search-all. Step turns with
  the dropdown or **]**. Overview is a session glance (status, context,
  summary, tools, last turn, path) aligned like the TUI Summary — not a
  lifecycle event dump. Event type labels use the TUI brand colors (cream /
  complete / running / failed / cancelled). Search hits show the matching
  field and a snippet. Timeline list rows open a **full-pane** event body
  (click or jump); quiet **‹ · n · ›** (or ↑↓ / Enter) steps events and
  crosses turns at the ends when turn-scoped; **Esc** returns to the list
  on that event. Only the event body scrolls (pager stays put). No in-list
  expanders. JSON/code uses the code block. Copy with **y** /
  **Ctrl+Shift+C**
  (or right-click Copy). Right-click also offers Copy path.
  Context fill is an Overview progress bar only (not on every rail card).
- Live refresh while the palette is open: selected running/awaiting turns
  re-fetch overview about every 3s (idle sessions slower). An open event
  drawer refreshes that turn’s events.
- Global hotkey: **Cmd+Shift+G** (macOS) / **Ctrl+Shift+G** (Linux/Windows) by default;
  override with ``~/.groket/config.json`` ``hud.global_shortcut`` or env
  ``GROKET_HUD_SHORTCUT``
- ``groket hud`` detaches; ``groket hud --restart`` replaces a running agent
- ``groket hud --install-desktop`` (or ``groket-hud --install-desktop``) writes
  **user-local** icons and a launcher — not a system package, DMG, or MSI:
  - **Linux:** ``~/.local/share/applications/dev.indynull.groket-hud.desktop``
    and hicolor PNGs under ``~/.local/share/icons/hicolor/*/apps/``
  - **macOS:** ``~/Applications/Groket HUD.app`` (shell wrapper to this binary;
    ``iconutil`` builds ``AppIcon.icns`` when Xcode CLT is present)
  - **Windows:** PNGs + ``.ico`` under ``%LOCALAPPDATA%\\Groket\\hud\\`` and a
    Start Menu ``.lnk`` via PowerShell
  The launcher targets the binary path used at install time. Re-run after
  moving or rebuilding it. Runtime tray and iced window icons still work
  without this step.
- Tray (when the host has one): StatusNotifier on Linux (Swaybar, Waybar),
  menu bar on macOS, notification area on Windows. Left-click or **Show HUD**
  reveals and focuses the palette. **Quit Groket HUD** exits the HUD process
  only; ``groket serve`` stays up. Escape hides the overlay and leaves the
  tray item in place. A missing tray host is logged; the HUD stays up
  (summon hotkey and pop-out still work).
- Desktop notifications (awaiting / complete / cancelled / failed, and
  analysis done or error) go to the host daemon: dunst, mako, fnott, or
  swaync on Linux (org.freedesktop.Notifications), Notification Center on
  macOS, toasts on Windows. The cream three-bar tray tile (64px) is written
  to ``~/.groket/hud-notify.png`` when possible. ``GROKET_HUD_NOTIFY=0`` or
  ``hud.desktop_notifications: false`` turns them off.
- Overlay: hides on **Esc** or the summon hotkey only (focus loss does not
  hide). It is a floating card (macOS system dialog / Linux override-redirect)
  so a tiler does not insert it. Decorated window: stays open until you close
  it; the summon hotkey focuses it. That window is a normal desktop client, so
  a tiler (yabai, i3, sway) tiles it and a stacking desktop just shows it.
  Closing the window does not stop the HUD process. On Wayland (Sway) the
  compositor owns focus: the HUD does not X11-grab or remap an already-visible
  overlay (tray Show is idempotent).

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
uv run groket hud --install-desktop  # user-local desktop icons + launcher
```

``make hud-check`` (from the repo root) checks the Textual theme map, rustfmt,
clippy (``-D warnings``), and ``cargo test``. When ``cargo llvm-cov`` is
installed it also applies a line fail-under on non-paint HUD logic (view,
window loop, and the Unix socket client are omitted from that floor), then
deletes the instrumented ``target/llvm-cov-target`` tree. ``make clean``
runs ``cargo clean`` on this crate. ``groket hud`` (release) drops
``target/debug`` and coverage leftovers; ``--dev`` / ``--debug`` keep
debug objects.

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
| ``GROKET_HUD_NOTIFY`` | ``0`` / ``false`` / ``no`` disables desktop notifications |
