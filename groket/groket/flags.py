"""User flags (annotations) on sessions — core concern, not analysis."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Flag
from .paths import flags_fallback_dir

logger = logging.getLogger(__name__)


def load_flags(session_dir: Path) -> list[Flag]:
    """Load user flags from a session directory or config-home fallback."""
    candidates = [
        session_dir / "flags.json",
        flags_fallback_dir(session_dir.name) / "flags.json",
    ]
    for flags_file in candidates:
        if not flags_file.exists():
            continue
        try:
            with open(flags_file) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return [Flag.model_validate({**v, "event_index": int(k)}) for k, v in data.items()]
            elif isinstance(data, list):
                return [Flag.model_validate(d) for d in data]
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return []


def save_flags(session_dir: Path, flags: list[Flag]) -> None:
    """Save user flags beside the session; fall back under ``~/.groket/flags``."""
    flags_file = session_dir / "flags.json"
    try:
        with open(flags_file, "w") as f:
            json.dump([fl.model_dump() for fl in flags], f, indent=2)
    except PermissionError:
        fallback_file = flags_fallback_dir(session_dir.name) / "flags.json"
        with open(fallback_file, "w") as f:
            json.dump([fl.model_dump() for fl in flags], f, indent=2)
