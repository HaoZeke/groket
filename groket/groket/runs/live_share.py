"""Grok share URLs for eval sessions.

Share creation runs **in the eval container** via the entrypoint loop::

    grok share <session-id>

which writes ``groket-share.json`` next to the session (bind-mounted into traces).

The host:

- Probes whether session sharing is available for this account (``probe_host_share_capability``)
  so launches can set ``SHARE_DISABLE=1`` when the feature is off.
- Reads ``groket-share.json`` for Jobs / Summary / Stats / open-share.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..models import JsonObject, json_as_int

logger = logging.getLogger(__name__)

SHARE_FILENAME = "groket-share.json"
LIVE_SHARES_INDEX = "live_shares.jsonl"  # under work_dir/runs/ (optional operator index)
# Cached host probe (~/.groket/cache/share_capability.json).
SHARE_CAPABILITY_CACHE = "share_capability.json"
SHARE_CAPABILITY_TTL_SECS = 24 * 3600
# Dummy UUID for entitlement probe (expect "session not found", not account disable).
_PROBE_SESSION_ID = "00000000-0000-0000-0000-000000000000"

_LOCK = threading.Lock()
_REGISTRY: dict[str, ShareResult] = {}


@dataclass(frozen=True)
class ShareCapability:
    """Result of a host-side ``grok share`` entitlement probe."""

    available: bool
    reason: str
    detail: str = ""
    checked_at: str = ""
    from_cache: bool = False

    def to_dict(self) -> JsonObject:
        return {
            "available": self.available,
            "reason": self.reason,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


_CAPABILITY_MEM: ShareCapability | None = None


@dataclass
class ShareResult:
    session_id: str
    session_dir: str
    share_url: str = ""
    error: str = ""
    created_at: str = ""
    source: str = ""  # incontainer | pending | …
    method: str = ""  # cli
    snapshot_n: int = 0
    snapshot_at: str = ""
    updated_at: str = ""
    note: str = ""

    def to_dict(self) -> JsonObject:
        d: JsonObject = {
            "session_id": self.session_id,
            "session_dir": self.session_dir,
            "share_url": self.share_url,
            "error": self.error,
            "created_at": self.created_at,
            "source": self.source,
        }
        if self.method:
            d["method"] = self.method
        if self.snapshot_n:
            d["snapshot_n"] = self.snapshot_n
        if self.snapshot_at:
            d["snapshot_at"] = self.snapshot_at
        if self.updated_at:
            d["updated_at"] = self.updated_at
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, data: JsonObject, *, session_dir: Path | None = None) -> ShareResult:
        try:
            snap_n = json_as_int(data.get("snapshot_n"), 0)
        except (TypeError, ValueError):
            snap_n = 0
        return cls(
            session_id=str(data.get("session_id") or ""),
            session_dir=str(data.get("session_dir") or (session_dir or "")),
            share_url=str(data.get("share_url") or ""),
            error=str(data.get("error") or ""),
            created_at=str(data.get("created_at") or ""),
            source=str(data.get("source") or ""),
            method=str(data.get("method") or ""),
            snapshot_n=snap_n,
            snapshot_at=str(data.get("snapshot_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            note=str(data.get("note") or ""),
        )


def share_path_for(session_dir: Path | str) -> Path:
    return Path(session_dir) / SHARE_FILENAME


def _read_share_file(session_dir: Path | str) -> JsonObject | None:
    sp = share_path_for(session_dir)
    if not sp.is_file():
        return None
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def is_share_not_ready_error(err: str) -> bool:
    e = (err or "").lower()
    return "no messages to share" in e or "nothing to share" in e


def is_share_unavailable_error(err: str) -> bool:
    """True when sharing is permanently unavailable (do not retry).

    Matches account/plan entitlement failures from ``grok share``, e.g.
    ``Session sharing is not available for your account.``
    """
    e = (err or "").lower()
    if not e:
        return False
    # Must look like a share entitlement problem, not "session not found".
    if "session not found" in e and "shar" not in e:
        return False
    if "resource not found" in e and "shar" not in e:
        return False
    needles = (
        "sharing is not available",
        "share is not available",
        "session sharing is not available",
        "sharing not available for your account",
    )
    if any(n in e for n in needles):
        return True
    # Broader form only when the message also mentions share.
    return "not available for your account" in e and "shar" in e


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _capability_cache_path() -> Path:
    raw = (os.environ.get("GROKET_SHARE_CAPABILITY_CACHE") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".groket" / "cache" / SHARE_CAPABILITY_CACHE


def _load_capability_cache(path: Path, *, ttl_secs: int) -> ShareCapability | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    checked = str(data.get("checked_at") or "")
    try:
        # ISO-ish from _utc_now; fall back to mtime
        ts = path.stat().st_mtime
        if checked:
            # Prefer file mtime for TTL (simple, no parse edge cases).
            pass
        if time.time() - ts > ttl_secs:
            return None
    except OSError:
        return None
    return ShareCapability(
        available=bool(data.get("available")),
        reason=str(data.get("reason") or "cache"),
        detail=str(data.get("detail") or ""),
        checked_at=checked or _utc_now(),
        from_cache=True,
    )


def _save_capability_cache(path: Path, cap: ShareCapability) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cap.to_dict(), indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("Could not write share capability cache %s", path, exc_info=True)


def _run_grok_share(session_id: str, *, timeout: float) -> tuple[int, str]:
    """Run ``grok share <id>``; return (exit_code, combined output)."""
    grok = shutil.which("grok") or "grok"
    try:
        proc = subprocess.run(
            [grok, "share", session_id],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return int(proc.returncode), out
    except FileNotFoundError:
        return 127, "grok not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, f"grok share timed out after {int(timeout)}s"
    except OSError as exc:
        return 1, str(exc)


def _find_host_session_id() -> str | None:
    """Newest UUID-like session directory under ``~/.grok/sessions`` if any."""
    root = Path.home() / ".grok" / "sessions"
    if not root.is_dir():
        return None
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.I,
    )
    best: tuple[float, str] | None = None
    try:
        for path in root.rglob("*"):
            if not path.is_dir():
                continue
            name = path.name
            if not uuid_re.match(name):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, name)
    except OSError:
        return None
    return best[1] if best else None


def probe_host_share_capability(
    *,
    force: bool = False,
    timeout: float = 30.0,
    ttl_secs: int = SHARE_CAPABILITY_TTL_SECS,
    cache_path: Path | None = None,
) -> ShareCapability:
    """Probe whether this host account can use ``grok share``.

    Strategy (cheap → stronger):

    1. Return process / disk cache unless *force*.
    2. ``grok share`` on a nil UUID — expect "session not found" if the feature
       is entitled; permanent "not available for your account" → disabled.
    3. If still ambiguous, try the newest local session id under
       ``~/.grok/sessions`` (success ⇒ available).

    :param force: Ignore caches and re-run the CLI.
    :param timeout: Seconds for each ``grok share`` invocation.
    :param ttl_secs: Disk cache lifetime.
    :param cache_path: Override cache file (tests).
    :returns: Capability result (never raises for probe failures).
    """
    global _CAPABILITY_MEM
    path = cache_path if cache_path is not None else _capability_cache_path()

    if not force:
        with _LOCK:
            if _CAPABILITY_MEM is not None:
                return _CAPABILITY_MEM
        cached = _load_capability_cache(path, ttl_secs=ttl_secs)
        if cached is not None:
            with _LOCK:
                _CAPABILITY_MEM = cached
            return cached

    # --- live probe ---
    code, out = _run_grok_share(_PROBE_SESSION_ID, timeout=timeout)
    if is_share_unavailable_error(out):
        cap = ShareCapability(
            available=False,
            reason="account_disabled",
            detail=out[:500],
            checked_at=_utc_now(),
        )
    elif code == 0 and "https://" in out.lower():
        cap = ShareCapability(
            available=True,
            reason="probe_ok",
            detail="dummy session returned a share URL",
            checked_at=_utc_now(),
        )
    elif "session not found" in out.lower() or "resource not found" in out.lower():
        # Feature reachable; session missing is expected for nil UUID.
        cap = ShareCapability(
            available=True,
            reason="entitled",
            detail="dummy session rejected as not found (sharing endpoint active)",
            checked_at=_utc_now(),
        )
    elif code == 127:
        cap = ShareCapability(
            available=False,
            reason="no_cli",
            detail=out[:500],
            checked_at=_utc_now(),
        )
    else:
        # Ambiguous — try a real host session if present.
        real = _find_host_session_id()
        if real:
            code2, out2 = _run_grok_share(real, timeout=timeout)
            if is_share_unavailable_error(out2):
                cap = ShareCapability(
                    available=False,
                    reason="account_disabled",
                    detail=out2[:500],
                    checked_at=_utc_now(),
                )
            elif code2 == 0 and "https://" in out2.lower():
                cap = ShareCapability(
                    available=True,
                    reason="host_session_ok",
                    detail=f"shared host session {real[:8]}…",
                    checked_at=_utc_now(),
                )
            else:
                cap = ShareCapability(
                    available=True,
                    reason="assume_ok",
                    detail=(out2 or out)[:500] or f"exit {code2}",
                    checked_at=_utc_now(),
                )
        else:
            # No proof of disable; allow loop (container stops on permanent error).
            cap = ShareCapability(
                available=True,
                reason="assume_ok",
                detail=(out or f"exit {code}")[:500],
                checked_at=_utc_now(),
            )

    _save_capability_cache(path, cap)
    with _LOCK:
        _CAPABILITY_MEM = cap
    logger.info(
        "Host share capability: available=%s reason=%s",
        cap.available,
        cap.reason,
    )
    return cap


def clear_share_capability_cache(*, cache_path: Path | None = None) -> None:
    """Drop in-memory and disk share-capability cache (tests / re-probe)."""
    global _CAPABILITY_MEM
    with _LOCK:
        _CAPABILITY_MEM = None
    path = cache_path if cache_path is not None else _capability_cache_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def load_cached_share(session_dir: Path | str) -> ShareResult | None:
    """Return share when last write has a URL and no error (usable snapshot)."""
    sd = Path(session_dir)
    data = _read_share_file(sd)
    if not data:
        return None
    res = ShareResult.from_dict(data, session_dir=sd)
    if not res.share_url or res.error:
        return None
    try:
        key = str(sd.resolve())
    except Exception:
        key = str(sd)
    with _LOCK:
        _REGISTRY[key] = res
    return res


def get_share_url(session_dir: Path | str) -> str:
    """Latest share URL from disk (empty if container has not written one yet)."""
    sd = Path(session_dir)
    res = load_cached_share(sd)
    if res and res.share_url:
        return res.share_url
    try:
        key = str(sd.resolve())
    except Exception:
        key = str(sd)
    with _LOCK:
        reg = _REGISTRY.get(key)
        if reg and reg.share_url and not reg.error:
            return reg.share_url
    return ""


def get_share_display(session_dir: Path | str) -> JsonObject:
    """Fields for Summary / Stats (always re-reads groket-share.json).

    Ready means the last share write recorded a URL with an empty error.
    """
    out: JsonObject = {
        "share_url": "",
        "error": "",
        "source": "",
        "method": "",
        "snapshot_n": 0,
        "snapshot_at": "",
        "updated_at": "",
        "note": "",
        "pending": False,
        "ready": False,
    }
    data = _read_share_file(session_dir)
    if not data:
        out["pending"] = True
        out["note"] = "Waiting for container entrypoint to run: grok share <session-id>"
        return out

    try:
        snap_n = json_as_int(data.get("snapshot_n"), 0)
    except (TypeError, ValueError):
        snap_n = 0
    err = str(data.get("error") or "")
    url = str(data.get("share_url") or "")
    src = str(data.get("source") or "")
    # One rule: ready ⇔ non-empty URL and no error field.
    ready = bool(url) and not err
    pending = not ready and (src == "pending" or is_share_not_ready_error(err) or not err)
    if not ready and err and not is_share_not_ready_error(err):
        pending = False

    out.update(
        {
            "share_url": url if ready else "",
            "error": err,
            "source": src,
            "method": str(data.get("method") or ""),
            "snapshot_n": snap_n,
            "snapshot_at": str(data.get("snapshot_at") or ""),
            "updated_at": str(data.get("updated_at") or ""),
            "note": str(data.get("note") or ""),
            "pending": pending,
            "ready": ready,
        }
    )
    return out


def format_share_summary_markdown(session_dir: Path | str) -> str:
    """Markdown block for Summary tab."""
    info = get_share_display(session_dir)
    lines = ["## Grok share", ""]
    url = info.get("share_url") or ""

    if url:
        lines.append(f"- **URL:** {url}")
        lines.append("")
        lines.append(f"  `{url}`")
    elif info.get("pending"):
        lines.append("- **URL:** _pending_ (container has not produced a share yet)")
        lines.append(
            "- **How it works:** eval entrypoint runs `grok share <session-id>` "
            "periodically and writes `groket-share.json`. Press **F5** to refresh."
        )
    else:
        lines.append("- **URL:** _not available_")

    if info.get("snapshot_n"):
        lines.append(f"- **Snapshot #:** {info['snapshot_n']}")
    if info.get("snapshot_at"):
        lines.append(f"- **Snapshot at:** `{info['snapshot_at']}`")
    if info.get("method"):
        lines.append(f"- **Method:** `{info['method']}`")
    if info.get("source"):
        lines.append(f"- **Source:** `{info['source']}`")
    if info.get("error"):
        lines.append(f"- **Error:** {str(info['error'])[:500]}")

    lines.append(
        "- **Tip:** Share is created in the container only. Reload the share page "
        "after a new snapshot; the TUI only displays `groket-share.json` (key **s** opens URL)."
    )
    if info.get("note") and not url:
        lines.append(f"- **Detail:** {info['note']}")
    return "\n".join(lines)


def format_share_stats_line(session_dir: Path | str) -> str:
    """Plain-text share line for summary-style displays."""
    info = get_share_display(session_dir)
    url = info.get("share_url") or ""
    if url:
        lines = [f"  Share URL: {url}\n"]
        meta = []
        if info.get("snapshot_n"):
            meta.append(f"#{info['snapshot_n']}")
        if info.get("method"):
            meta.append(str(info["method"]))
        if info.get("snapshot_at"):
            meta.append(str(info["snapshot_at"]))
        if meta:
            lines.append(f"  Share meta: {', '.join(meta)}\n")
        lines.append("  Share tip:  reload page for latest snapshot; s opens URL\n")
        return "".join(lines)
    if info.get("error") and not info.get("pending"):
        err = str(info["error"]).replace("\n", " ")[:160]
        return (
            f"  Share:     failed — {err}\n  Share tip:  needs working `grok share` in container\n"
        )
    return (
        "  Share:     pending (container `grok share <session-id>` → groket-share.json)\n"
        "  Share tip:  F5 refresh; requires image with entrypoint share loop\n"
    )


def refresh_share_from_disk(session_dir: Path | str) -> str:
    """Re-read disk and update in-memory registry; return URL or empty."""
    sd = Path(session_dir)
    try:
        key = str(sd.resolve())
    except Exception:
        key = str(sd)
    res = load_cached_share(sd)
    if res:
        return res.share_url
    with _LOCK:
        _REGISTRY.pop(key, None)
    return ""


__all__ = [
    "LIVE_SHARES_INDEX",
    "SHARE_CAPABILITY_CACHE",
    "SHARE_CAPABILITY_TTL_SECS",
    "SHARE_FILENAME",
    "ShareCapability",
    "ShareResult",
    "clear_share_capability_cache",
    "format_share_stats_line",
    "format_share_summary_markdown",
    "get_share_display",
    "get_share_url",
    "is_share_not_ready_error",
    "is_share_unavailable_error",
    "load_cached_share",
    "probe_host_share_capability",
    "refresh_share_from_disk",
    "share_path_for",
]
