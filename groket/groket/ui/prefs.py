"""App-global UI preferences (``~/.groket/config.json``).

Lightweight keys that are not analysis-pipeline specific. Screens and
``panel_render`` read these — do not scatter ad-hoc config.json I/O.
"""

from __future__ import annotations

import json
import logging

from ..models import JsonObject, JsonValue
from ..paths import app_config_path
from .i18n import t

logger = logging.getLogger(__name__)
_CACHE: JsonObject | None = None
_DEFAULTS: JsonObject = {
    "show_tips": True,
    "show_host_sessions": False,
    # Detach-start control owner when launching the TUI if the socket is free.
    "auto_serve": True,
}


def _read_file() -> JsonObject:
    fp = app_config_path()
    if not fp.is_file():
        return {}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.debug(t("ui-failed-to-read-prefs-from-s"), fp, exc_info=True)
        return {}


def _write_file(data: JsonObject) -> None:
    fp = app_config_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def invalidate_prefs_cache() -> None:
    global _CACHE
    _CACHE = None


def get_pref(key: str, default: JsonValue | None = None) -> JsonValue | None:
    """Return one preference; *default* overrides built-in defaults when given."""
    global _CACHE
    if _CACHE is None:
        _CACHE = {**_DEFAULTS, **_read_file()}
    if key in _CACHE:
        return _CACHE[key]
    if default is not None:
        return default
    return _DEFAULTS.get(key)


def set_pref(key: str, value: JsonValue) -> None:
    """Persist one preference and update the in-process cache."""
    global _CACHE
    data = _read_file()
    data[key] = value
    try:
        _write_file(data)
    except OSError:
        logger.warning(t("ui-failed-to-write-prefs-to-s"), app_config_path(), exc_info=True)
    if _CACHE is None:
        _CACHE = {**_DEFAULTS}
    _CACHE[key] = value


def show_tips_enabled() -> bool:
    """Whether framed admonitions (tips/info/…) should render."""
    return bool(get_pref("show_tips", True))


def set_show_tips(enabled: bool) -> None:
    set_pref("show_tips", bool(enabled))


def toggle_show_tips() -> bool:
    """Flip ``show_tips``; return the new value."""
    new = not show_tips_enabled()
    set_show_tips(new)
    return new


def show_host_sessions_enabled() -> bool:
    """Whether the sessions home list includes native ``~/.grok/sessions``."""
    return bool(get_pref("show_host_sessions", False))


def set_show_host_sessions(enabled: bool) -> None:
    set_pref("show_host_sessions", bool(enabled))


def auto_serve_enabled() -> bool:
    """Whether the TUI should detach-start a control owner when the socket is free."""
    return bool(get_pref("auto_serve", True))


def set_auto_serve(enabled: bool) -> None:
    set_pref("auto_serve", bool(enabled))


def hud_global_shortcut() -> str:
    """Optional HUD summon chord from config (empty = binary default).

    Prefer nested::

        { "hud": { "global_shortcut": "Cmd+Shift+G" } }

    Flat key ``hud_global_shortcut`` is also accepted. Format is ``+``-separated
    modifiers and one key (see HUD README). Env ``GROKET_HUD_SHORTCUT`` wins
    when the launcher sets it from this value.
    """
    nested = get_pref("hud", None)
    if isinstance(nested, dict):
        raw = nested.get("global_shortcut")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    flat = get_pref("hud_global_shortcut", None)
    if isinstance(flat, str) and flat.strip():
        return flat.strip()
    return ""
