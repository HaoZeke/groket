"""UI copy via Fluent message IDs (see ``locale/<lang>/main.ftl``).

Call sites use ``text.foo_bar()`` / ``text.cmd_x()``. Attribute access maps
``foo_bar`` → Fluent id ``foo-bar``. Command helpers return
``(title, help)`` for ``cmd-*`` / ``cmd-*-help`` pairs.

Positional arguments are bound to ``{$var}`` placeholders in order of
appearance in the English catalog (no hard-coded English strings here).
"""

from __future__ import annotations

import re

from .i18n import ngettext, t

# Message body: ``id = ...`` (value may continue on following indented lines).
_MSG_START = re.compile(r"^([a-z0-9][a-z0-9_-]*)\s*=\s*(.*)$", re.IGNORECASE)
_VAR = re.compile(r"\{\s*\$([A-Za-z_][\w-]*)")
_POS_KEYS_CACHE: dict[str, tuple[str, ...]] | None = None


def _positional_keys() -> dict[str, tuple[str, ...]]:
    """Map Fluent id → placeholder names in source order from ``en/main.ftl``."""
    global _POS_KEYS_CACHE
    if _POS_KEYS_CACHE is not None:
        return _POS_KEYS_CACHE

    from .i18n import locale_dir

    path = locale_dir() / "en" / "main.ftl"
    if not path.is_file():
        _POS_KEYS_CACHE = {}
        return _POS_KEYS_CACHE

    out: dict[str, tuple[str, ...]] = {}
    current_id: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal current_id, buf
        if current_id is None:
            return
        body = "\n".join(buf)
        # Preserve first-seen order; skip duplicates in one message.
        seen: list[str] = []
        for match in _VAR.finditer(body):
            name = match.group(1)
            if name not in seen:
                seen.append(name)
        if seen:
            out[current_id] = tuple(seen)
        current_id = None
        buf = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        start = _MSG_START.match(line)
        if start and not line.startswith((" ", "\t")):
            _flush()
            current_id = start.group(1)
            buf = [start.group(2)]
            continue
        if current_id is not None and (line.startswith(" ") or line.startswith("\t")):
            buf.append(line.strip())
    _flush()
    _POS_KEYS_CACHE = out
    return out


def help_markup() -> str:
    """Long Rich help panel from ``locale/<lang>/help.rich.txt``."""
    from .i18n import load_text_resource

    return load_text_resource("help.rich.txt")


def __getattr__(name: str):
    """Dynamic Fluent accessors (``U.flag_saved(n)`` / ``U.cmd_foo()``).

    Return type is intentionally unannotated: ``cmd_*`` returns
    ``tuple[str, str]`` and other names return ``str``. Annotating a single
    ``Callable`` return forces a union that breaks every ``notify(U.…())`` call.
    """
    if name.startswith("_") or name in {"t", "ngettext", "help_markup"}:
        raise AttributeError(name)

    fluent_id = name.replace("_", "-")

    if name.startswith("cmd_"):

        def _cmd() -> tuple[str, str]:
            return (t(fluent_id), t(fluent_id + "-help"))

        _cmd.__name__ = name
        _cmd.__qualname__ = f"text.{name}"
        return _cmd

    keys = _positional_keys().get(fluent_id, ())

    def _msg(*args: str | int | float, **kwargs: str | int | float) -> str:
        if args and keys:
            for key, arg in zip(keys, args, strict=False):
                kwargs.setdefault(key, arg)
        return t(fluent_id, **kwargs)

    _msg.__name__ = name
    _msg.__qualname__ = f"text.{name}"
    return _msg


__all__ = ["t", "ngettext", "help_markup"]
