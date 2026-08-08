# groket-hud

Sol-style session **command palette** for the local groket control plane
(JSON-RPC over Unix socket). Not a second session owner — talk to
``groket serve``.

## Features

- Floating, frameless, always-on-top palette (centered)
- Typeahead over ``session/list``
- Detail: overview, turns, **live timeline**, findings, notes
- **Live refresh** while the palette is open: selected running/awaiting turns
  re-fetch ``session/overview`` and the timeline tail about every 2s (idle
  sessions slower). Scroll stays put unless you are near the bottom (then
  auto-follow new events).
- Global hotkey: **⌘⇧G** (macOS) / **Ctrl+Shift+G** (Linux/Windows) by default;
  override with ``~/.groket/config.json`` ``hud.global_shortcut`` (e.g.
  ``"Cmd+Shift+Space"``) or env ``GROKET_HUD_SHORTCUT``
- **Agent process** (Sol-like): ``groket hud`` detaches; macOS accessory policy
  so the HUD is **not** in the Dock or **⌘Tab**
- ``groket hud --restart`` stops any running agent, then starts a new one
- ``groket hud SESSION --show`` selects a session and reveals the palette
  through the shared control daemon, including when the HUD is already running
- Starts hidden; on show: window + search field focus so typing works immediately
- Hides on **Esc** or window blur; hotkey re-shows

## Prerequisites

- Rust (stable)
- Node.js + npm
- Running control owner: ``groket serve -d`` (or auto-start via ``groket hud``)
- **Linux (build only):** system packages for Tauri’s WebKitGTK webview.
  Runtime shared libraries are not enough — cargo needs the ``-dev`` packages
  (headers + pkg-config ``.pc`` files). On Ubuntu/Debian 24.04+:

  ```bash
  sudo apt install libwebkit2gtk-4.1-dev \
    libjavascriptcoregtk-4.1-dev \
    libsoup-3.0-dev
  ```

  Without these, ``cargo build`` fails with ``javascriptcoregtk-4.1`` /
  ``libsoup-3.0`` “package not found” from pkg-config. macOS and Windows use
  different webviews and do not need these packages.

## Develop

```bash
# terminal 1
groket serve -d

# terminal 2
cd groket-hud
npm install
npm run dev
```

## Release binary

```bash
cd groket-hud
npm install
npm run build
# binary: src-tauri/target/release/groket-hud
```

``groket hud`` prefers this release (then debug) binary when present.
From an editable checkout, ``groket hud`` also auto-runs ``cargo build``
(debug) when the binary is missing or sources are newer.

## Env

| Variable | Role |
|----------|------|
| ``GROKET_CONTROL_SOCKET`` | Override control Unix socket path |
