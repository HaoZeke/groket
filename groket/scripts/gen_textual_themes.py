#!/usr/bin/env python3
"""Write groket-hud/assets/textual-themes.json from Textual built-in themes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from textual.theme import BUILTIN_THEMES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "groket-hud" / "assets" / "textual-themes.json"


def theme_payload() -> dict[str, dict[str, object]]:
    """Map theme name → {dark, colors} using Textual ColorSystem hex tokens."""
    out: dict[str, dict[str, object]] = {}
    for name, theme in sorted(BUILTIN_THEMES.items()):
        if theme.ansi:
            continue
        colors = theme.to_color_system().generate()
        hex_colors = {key: val for key, val in colors.items() if isinstance(val, str)}
        out[name] = {"dark": bool(theme.dark), "colors": hex_colors}
    return out


def emit(path: Path | None = None) -> Path:
    """Write pretty JSON (stable key order) to *path* or the committed HUD file."""
    dest = path or OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = theme_payload()
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def main() -> int:
    dest = emit(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"wrote {dest} ({len(theme_payload())} themes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
