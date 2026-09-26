"""Shared text, time, path, and JSON helpers."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

_BLANK_RUNS = re.compile(r"\n{3,}")
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_C0_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x1b]")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_WIDGET_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def fmt_duration(seconds: float) -> str:
    """Format *seconds* as a short human-readable duration.

    Thresholds: ``<1s``, then ``Ns``, ``NmNNs``, ``NhNNm``.

    :param seconds: Elapsed time in seconds (non-negative).
    :returns: Compact duration string for UI chrome.
    """
    s = int(seconds)
    if s < 1:
        return "<1s"
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def fmt_token_count(tokens: int) -> str:
    """Compact token count for narrow UI columns (``179k``, ``1.2M``)."""
    n = max(0, int(tokens))
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        k = n / 1000.0
        text = f"{k:.0f}k" if k >= 10 or abs(k - round(k)) < 0.05 else f"{k:.1f}k"
        return text.replace(".0k", "k")
    m = n / 1_000_000.0
    text = f"{m:.1f}M"
    return text.replace(".0M", "M")


def fmt_context_usage(
    usage_pct: int | None,
    tokens_used: int | None = None,
    window_tokens: int | None = None,
    *,
    compact: bool = False,
) -> str:
    """Format session context fill from ``signals.json`` fields.

    :param usage_pct: ``contextWindowUsage`` percent, or ``None`` when unknown.
    :param tokens_used: ``contextTokensUsed``.
    :param window_tokens: ``contextWindowTokens``.
    :param compact: When true, prefer ``35%`` / ``179k/500k`` for narrow columns.
    :returns: Display string, or empty when no context telemetry is present.
    """
    pct = usage_pct if usage_pct is not None and usage_pct >= 0 else None
    used = tokens_used if tokens_used is not None and tokens_used >= 0 else None
    window = window_tokens if window_tokens is not None and window_tokens > 0 else None
    if pct is None and used is None:
        return ""
    if compact:
        if pct is not None and used is not None and window is not None:
            return f"{pct}% {fmt_token_count(used)}/{fmt_token_count(window)}"
        if pct is not None:
            return f"{pct}%"
        if used is not None and window is not None:
            return f"{fmt_token_count(used)}/{fmt_token_count(window)}"
        if used is not None:
            return fmt_token_count(used)
        return ""
    if pct is not None and used is not None and window is not None:
        return f"{pct}% ({used:,} / {window:,})"
    if pct is not None:
        return f"{pct}%"
    if used is not None and window is not None:
        return f"{used:,} / {window:,}"
    if used is not None:
        return f"{used:,}"
    return ""


def collapse_blank_lines(text: str) -> str:
    """Collapse runs of three or more blank lines to a double blank line.

    :param text: Multi-line text.
    :returns: Text with long blank runs shortened.
    """
    return _BLANK_RUNS.sub("\n\n", text)


def strip_control_chars(text: str) -> str:
    """Remove ANSI escapes and C0 controls for one-line display.

    Tab and newline are preserved. Suitable for timeline snippets and
    similar UI chrome.

    :param text: Raw terminal or tool output.
    :returns: Printable text without CSI/OSC/C0 control sequences.
    """
    if not text:
        return ""
    s = _ANSI_CSI.sub("", text)
    s = _ANSI_OSC.sub("", s)
    return _C0_CONTROLS.sub("", s)


def utc_now_iso() -> str:
    """UTC timestamp without microseconds."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def slug_text(text: str, max_len: int = 40, *, fallback: str = "item") -> str:
    """Filesystem-safe slug from *text* (letters, digits, ``._-``)."""
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")[:max_len].strip("-")
    return s or fallback


def widget_id(text: str, max_len: int = 64, *, fallback: str = "id") -> str:
    """Textual / CSS identifier from *text*.

    Only letters, digits, underscores, and hyphens; must not start with a digit
    (Textual ``BadIdentifier`` rules).
    """
    s = _WIDGET_ID_RE.sub("-", (text or "").strip()).strip("-")[:max_len].strip("-")
    if not s:
        return fallback
    if s[0].isdigit():
        s = f"n-{s}"[:max_len]
    return s or fallback


def read_json_dict(path: Path | str) -> dict | None:
    """Load a JSON object from *path*, or ``None`` on missing/invalid."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_json(path: Path | str, data: object, *, indent: int = 2, sort_keys: bool = False) -> None:
    """Write *data* as UTF-8 JSON with trailing newline."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=indent, sort_keys=sort_keys) + "\n", encoding="utf-8")


def path_key(path: Path | str) -> str:
    """Stable string key for *path* (resolved when possible)."""
    p = Path(path)
    try:
        return str(p.expanduser().resolve())
    except OSError:
        return str(p)


def dedupe_str(items: list[str]) -> list[str]:
    """Preserve order, drop empty and duplicate strings."""
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def str_list_from_json(raw: object) -> list[str]:
    """Deduped non-empty strings from a JSON list value."""
    if not isinstance(raw, list):
        return []
    return dedupe_str([str(x or "").strip() for x in raw])
