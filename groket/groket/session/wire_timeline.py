"""Rebuild domain timeline types from control wire payloads (TUI attach path).

Clients that must not parse session disk (TUI when attached, HUD) consume
``session/timeline`` / ``session/overview`` JSON and hydrate
:class:`~groket.models.TraceEvent` / :class:`~groket.models.SessionMeta` here.
"""

from __future__ import annotations

from pathlib import Path

from ..models import JsonObject, JsonValue, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from .catalog import session_meta_from_catalog_row
from .control_views import MAX_CONTENT_CHARS, MAX_TIMELINE_LIMIT

# One RPC must finish inside HEAVY_RPC_TIMEOUT. HUD uses 200 × 12k; the TUI
# used MAX_TIMELINE_LIMIT × MAX_CONTENT_CHARS and hit the 5s client timeout.
TIMELINE_RPC_LIMIT = 200
TIMELINE_RPC_CHARS = 12_000


def trace_event_from_wire(row: JsonObject) -> TraceEvent:
    """Hydrate one :class:`TraceEvent` from a ``session/timeline`` event object."""
    raw_in = row.get("rawInput")
    raw: dict[str, JsonValue] = {}
    if isinstance(raw_in, dict):
        raw = {str(k): v for k, v in raw_in.items()}

    ts = row.get("timestamp")
    timestamp: int | None
    if isinstance(ts, bool):
        timestamp = None
    elif isinstance(ts, int):
        timestamp = ts
    elif isinstance(ts, float):
        timestamp = int(ts)
    elif isinstance(ts, str) and ts.strip().isdigit():
        timestamp = int(ts.strip())
    else:
        timestamp = None

    pi = row.get("promptIndex")
    prompt_index: int | None
    if isinstance(pi, bool) or pi is None or pi == "":
        prompt_index = None
    elif isinstance(pi, int):
        prompt_index = pi
    elif isinstance(pi, float):
        prompt_index = int(pi)
    elif isinstance(pi, str):
        try:
            prompt_index = int(pi)
        except ValueError:
            prompt_index = None
    else:
        prompt_index = None

    idx = row.get("index")
    index = int(idx) if isinstance(idx, (int, float)) and not isinstance(idx, bool) else 0
    upd = row.get("updateIndex")
    update_index = int(upd) if isinstance(upd, (int, float)) and not isinstance(upd, bool) else 0

    return TraceEvent(
        index=index,
        event_type=str(row.get("type") or "").strip(),
        timestamp=timestamp,
        content=str(row.get("content") or ""),
        tool_name=str(row.get("toolName") or "").strip(),
        tool_call_id=str(row.get("toolCallId") or "").strip(),
        raw_input=ToolInputBag(raw),
        is_error=bool(row.get("isError")),
        update_index=update_index,
        prompt_index=prompt_index,
    )


def session_meta_from_overview(overview: JsonObject, *, fallback_dir: Path) -> SessionMeta:
    """Build :class:`SessionMeta` from a ``session/overview`` payload."""
    meta_raw = overview.get("meta")
    row = as_json_object(meta_raw) if isinstance(meta_raw, dict) else {}
    if not row.get("path") and not row.get("sessionId"):
        row = {
            **row,
            "path": str(fallback_dir),
            "sessionId": fallback_dir.name,
        }
    meta = session_meta_from_catalog_row(row)
    if meta is None:
        meta = SessionMeta(session_id=fallback_dir.name, session_dir=fallback_dir)
    # Prefer resolved path from wire when present.
    path_raw = str(row.get("path") or "").strip()
    if path_raw:
        try:
            meta.session_dir = Path(path_raw).expanduser()
        except OSError:
            pass
    summary = overview.get("summary")
    if isinstance(summary, str) and summary.strip():
        meta.summary_text = summary.strip()
    tl = overview.get("timeline")
    if isinstance(tl, dict) and tl.get("total") is not None:
        try:
            meta.num_events = int(tl["total"])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    return meta


async def fetch_timeline_events(
    access: object,
    session_ref: str,
    *,
    content_chars: int = TIMELINE_RPC_CHARS,
    page_limit: int = TIMELINE_RPC_LIMIT,
) -> list[TraceEvent]:
    """Page ``session/timeline`` until complete; return domain events.

    :param access: Object with async ``session_timeline`` (RemoteSessionAccess).
    :param session_ref: Session id or path accepted by control.
    """
    session_timeline = getattr(access, "session_timeline", None)
    if not callable(session_timeline):
        raise TypeError("access must provide session_timeline")
    out: list[TraceEvent] = []
    offset = 0
    lim = max(1, min(int(page_limit), MAX_TIMELINE_LIMIT))
    chars = max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    while True:
        page = await session_timeline(
            session_ref,
            offset=offset,
            limit=lim,
            content_chars=chars,
        )
        if not isinstance(page, dict):
            break
        batch = page.get("events")
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            if isinstance(item, dict):
                out.append(trace_event_from_wire(as_json_object(item)))
        total = page.get("total")
        try:
            total_n = int(total) if total is not None else len(out)
        except (TypeError, ValueError):
            total_n = len(out)
        offset += len(batch)
        if offset >= total_n or len(batch) < lim:
            break
    return out


async def fetch_session_browser_bundle(
    access: object,
    session_ref: str,
    *,
    fallback_dir: Path,
) -> tuple[SessionMeta, list[TraceEvent], JsonObject]:
    """Load meta + full timeline for the session browser via control.

    :returns: ``(meta, timeline, overview_payload)``.
    """
    session_overview = getattr(access, "session_overview", None)
    if not callable(session_overview):
        raise TypeError("access must provide session_overview")
    overview = await session_overview(session_ref)
    if not isinstance(overview, dict):
        overview = {}
    ov = as_json_object(overview)
    meta = session_meta_from_overview(ov, fallback_dir=fallback_dir)
    events = await fetch_timeline_events(access, session_ref)
    meta.num_events = len(events)
    return meta, events, ov


__all__ = [
    "TIMELINE_RPC_CHARS",
    "TIMELINE_RPC_LIMIT",
    "fetch_session_browser_bundle",
    "fetch_timeline_events",
    "session_meta_from_overview",
    "trace_event_from_wire",
]
