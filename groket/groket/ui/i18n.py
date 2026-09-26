"""Fluent localization for groket (Project Fluent).

Catalogs: ``groket/locale/<lang>/main.ftl`` (fallback chain ends at ``en``).

Use :func:`t` for message IDs (``t("save")``) and named variables
(``t("model-filter-notify", label="x")``). Prefer :mod:`groket.ui_text` helpers
in screens. Call :func:`setup_i18n` at process start (CLI / app).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fluent.runtime import FluentLocalization, FluentResourceLoader

logger = logging.getLogger(__name__)

_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
_RESOURCES = ("main.ftl",)

_current_lang: str = "en"
_l10n: FluentLocalization | None = None


def locale_dir() -> Path:
    return _LOCALE_DIR


def join_ui(*parts: object, sep: str = " ") -> str:
    """Join UI fragments with *sep*, dropping empties.

    Fluent strips leading/trailing spaces on message values, so callers must
    not rely on fragment-edge whitespace when concatenating. Use this (or
    explicit separators in the format string) whenever combining ``t()`` pieces
    with values.
    """
    bits = [str(p).strip() for p in parts if p is not None and str(p).strip()]
    return sep.join(bits)


def current_language() -> str:
    return _current_lang


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return "en"
    lang = lang.replace("-", "_").split(".")[0].strip()
    if not lang or lang.upper() in ("C", "POSIX"):
        return "en"
    return lang.split("_")[0].lower()


def _locales_for(lang: str) -> list[str]:
    """Fluent locale preference list (requested → en)."""
    short = _normalize_lang(lang)
    out: list[str] = []
    for code in (lang.replace("-", "_").split(".")[0], short, "en"):
        c = _normalize_lang(code)
        if c and c not in out:
            out.append(c)
    if "en" not in out:
        out.append("en")
    return out


def _build_l10n(lang: str) -> FluentLocalization:
    loader = FluentResourceLoader(str(_LOCALE_DIR / "{locale}"))
    locales = _locales_for(lang)
    # Only keep locales that have at least one resource file
    existing = [
        loc for loc in locales if any((_LOCALE_DIR / loc / name).is_file() for name in _RESOURCES)
    ]
    if not existing:
        existing = ["en"]
    return FluentLocalization(existing, list(_RESOURCES), loader)


def setup_i18n(lang: str | None = None) -> str:
    """Load Fluent bundles for *lang* (default ``en``; pass explicitly to override)."""
    global _current_lang, _l10n, _, ngettext  # noqa: PLW0603

    if lang is None:
        lang = "en"
    _current_lang = _normalize_lang(str(lang))
    try:
        _l10n = _build_l10n(_current_lang)
    except Exception:
        logger.exception("Fluent setup failed; using identity fallback")
        _l10n = None

    def _t(message_id: str, **kwargs: str | int | float) -> str:
        return t(message_id, **kwargs)

    def _ngettext(singular_id: str, plural_id: str, n: int, **kwargs: str | int | float) -> str:
        # Prefer explicit plural message; Fluent selectors can live in one FTL entry later.
        mid = singular_id if n == 1 else plural_id
        return t(mid, count=n, **kwargs)

    # Identity ``_`` kept for rare direct English literals (prefer ``t`` / ui_text).
    def _identity(message: str) -> str:
        return message

    _ = _identity
    ngettext = _ngettext

    return _current_lang


def t(message_id: str, **kwargs: str | int | float | object) -> str:
    """Format Fluent message *message_id* with keyword args as ``$variables``."""
    if not message_id:
        return ""
    if _l10n is None:
        # Before setup or on failure — return id as last resort (should not show in UI)
        setup_i18n(_current_lang)
    assert _l10n is not None
    # Fluent accepts scalars; coerce other objects (e.g. Exception, Path) for display.
    kwargs = {k: (v if isinstance(v, (str, int, float)) else str(v)) for k, v in kwargs.items()}
    try:
        # Fluent wants plain values; stringify paths/objects for display vars
        args = {
            k: (str(v) if not isinstance(v, (int, float, bool, type(None))) else v)
            for k, v in kwargs.items()
        }
        # Remove None — Fluent may not like them
        args = {k: v for k, v in args.items() if v is not None}
        text = _l10n.format_value(message_id, args)
        if text is None or text == message_id:
            # Missing translation: try en-only rebuild once is already in chain;
            # return a readable fallback for known helpers (id with dashes → spaces)
            if text == message_id and not (_LOCALE_DIR / "en" / "main.ftl").is_file():
                return message_id
        return text if text is not None else message_id
    except Exception:
        logger.debug("Fluent format failed for %s", message_id, exc_info=True)
        return message_id


def load_text_resource(filename: str) -> str:
    """Load a non-Fluent text asset (e.g. ``help.rich.txt``) with locale fallback."""
    for loc in _locales_for(_current_lang):
        path = _LOCALE_DIR / loc / filename
        if path.is_file():
            return path.read_text(encoding="utf-8")
    path = _LOCALE_DIR / "en" / filename
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def gettext_lazy(message_id: str) -> str:
    """Alias of :func:`t` without variables (name kept for older tests)."""
    return t(message_id)


# Identity stubs until first setup_i18n()
_ = lambda s: s  # noqa: E731
ngettext = lambda s, p, n: s if n == 1 else p  # noqa: E731

setup_i18n()
