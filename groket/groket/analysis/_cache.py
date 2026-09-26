"""On-disk analysis result cache.

Cache layout::

    <cache_root>/analysis/<session_id>/<analyzer_id>.json

Each file stores the serialised :class:`AnalysisResult` plus metadata used
for invalidation (trace mtime, plugin version).

Invalidation rules:
- Only completed sessions are cached (caller decides).
- Cache is valid when *trace_mtime* and *plugin_version* both match.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import JsonObject
from ..notes import notes_mtime
from .base import AnalysisResult

logger = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = 1


def _cache_path(cache_root: Path, session_id: str, analyzer_id: str) -> Path:
    return cache_root / "analysis" / session_id / f"{analyzer_id}.json"


def _trace_mtime(session_dir: Path) -> float:
    """Newest mtime across the main trace artifacts and operator notes."""
    newest = 0.0
    for name in ("events.jsonl", "chat_history.jsonl", "updates.jsonl", "summary.json"):
        fp = session_dir / name
        try:
            if fp.is_file():
                newest = max(newest, fp.stat().st_mtime)
        except OSError:
            continue
    # Deferred LLM reviews include operator notes in the prompt; note edits
    # must invalidate the same way as timeline changes.
    return max(newest, notes_mtime(session_dir))


def load_cached_result(
    cache_root: Path,
    session_dir: Path,
    analyzer_id: str,
    plugin_version: str,
    *,
    allow_stale: bool = False,
    ignore_trace_mtime: bool = False,
) -> AnalysisResult | None:
    """Return cached result if valid, else ``None``.

    :param allow_stale: When True, accept a cache entry whose
        ``_plugin_version`` differs from *plugin_version* (still requires
        matching schema). Used to paint deferred LLM results after a plugin
        bump without auto-re-running the model.
    :param ignore_trace_mtime: When True, skip the trace mtime check (serve
        last known review even if the trace was touched).
    """
    fp = _cache_path(cache_root, session_dir.name, analyzer_id)
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.debug("Corrupt cache file %s — ignoring", fp)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("_schema") != _CACHE_SCHEMA_VERSION:
        return None
    if data.get("_plugin_version") != plugin_version:
        if not allow_stale:
            logger.debug("Cache version mismatch for %s/%s", session_dir.name, analyzer_id)
            return None
        logger.debug(
            "Serving stale cache for %s/%s (disk v%s, want v%s)",
            session_dir.name,
            analyzer_id,
            data.get("_plugin_version"),
            plugin_version,
        )
    if not ignore_trace_mtime:
        cached_mtime = data.get("_trace_mtime", 0.0)
        if abs(_trace_mtime(session_dir) - cached_mtime) > 0.5:
            logger.debug("Trace mtime changed for %s/%s", session_dir.name, analyzer_id)
            return None
    result_data = data.get("result")
    if not isinstance(result_data, dict):
        return None
    return AnalysisResult.from_dict(result_data)


def cache_file_path(cache_root: Path, session_dir: Path, analyzer_id: str) -> Path:
    """Path to the on-disk cache JSON for *analyzer_id*."""
    return _cache_path(cache_root, session_dir.name, analyzer_id)


def read_cached_plugin_version(
    cache_root: Path,
    session_dir: Path,
    analyzer_id: str,
) -> str | None:
    """Return stored ``_plugin_version`` for *analyzer_id*, or ``None`` if absent."""
    fp = _cache_path(cache_root, session_dir.name, analyzer_id)
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    ver = data.get("_plugin_version")
    return str(ver) if ver is not None else None


def save_cached_result(
    cache_root: Path,
    session_dir: Path,
    analyzer_id: str,
    plugin_version: str,
    result: AnalysisResult,
) -> None:
    """Persist an analysis result to the cache."""
    fp = _cache_path(cache_root, session_dir.name, analyzer_id)
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {
        "_schema": _CACHE_SCHEMA_VERSION,
        "_plugin_version": plugin_version,
        "_trace_mtime": _trace_mtime(session_dir),
        "result": result.to_dict(),
    }
    try:
        fp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("Failed to write cache file %s", fp, exc_info=True)
