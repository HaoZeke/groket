"""Workspace diff extraction from session traces.

Prefers ``rewind_points.jsonl`` before/after snapshots; falls back to
reconstructing approximate diffs from ``search_replace`` tool calls in
``updates.jsonl``.
"""

from __future__ import annotations

import difflib
import json
import logging
from collections.abc import Iterator, Mapping
from pathlib import Path

from ..models import JsonObject, JsonValue, json_as_int

logger = logging.getLogger(__name__)


def _snap_map(block: Mapping[str, JsonValue] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (block or {}).items():
        if isinstance(v, dict):
            content = v.get("content")
            if content is None:
                continue
            path = str(v.get("path") or k).lstrip("/")
            out[path] = content if isinstance(content, str) else str(content)
        elif isinstance(v, str):
            out[str(k).lstrip("/")] = v
    return out


def _unified_diff(old: str | None, new: str | None, path: str) -> tuple[str, int, int]:
    if old is None:
        a, b = "/dev/null", f"b/{path}"
    elif new is None:
        a, b = f"a/{path}", "/dev/null"
    else:
        a, b = f"a/{path}", f"b/{path}"
    ud = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile=a,
            tofile=b,
            lineterm="",
        )
    )
    added = sum(1 for ln in ud if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in ud if ln.startswith("-") and not ln.startswith("---"))
    return ("\n".join(ud) if ud else f"(no textual diff for {path})"), added, removed


def _iter_updates(session_dir: Path) -> Iterator[JsonObject]:
    p = session_dir / "updates.jsonl"
    if not p.exists():
        return
    try:
        with open(p) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _meta_dict(
    *,
    source: str | None,
    files_changed: int,
    lines_added: int,
    lines_removed: int,
) -> JsonObject:
    return {
        "source": source,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }


def load_workspace_diff(session_dir: Path) -> tuple[str, JsonObject]:
    """Return ``(markdown_body, meta)`` for the session's workspace changes.

    Meta keys: ``source`` (``rewind_points`` | ``search_replace`` | ``None``),
    ``files_changed``, ``lines_added``, ``lines_removed``.
    """
    source: str | None = None
    files_changed = 0
    lines_added = 0
    lines_removed = 0
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    rp_path = session_dir / "rewind_points.jsonl"
    if rp_path.exists():
        try:
            lines = [ln for ln in rp_path.read_text().splitlines() if ln.strip()]
            if lines:
                rp = json.loads(lines[-1])
                if isinstance(rp, dict):
                    before = _snap_map(
                        rp.get("file_snapshots")
                        if isinstance(rp.get("file_snapshots"), dict)
                        else None
                    )
                    after = _snap_map(
                        rp.get("after_snapshots")
                        if isinstance(rp.get("after_snapshots"), dict)
                        else None
                    )
                    if after or before:
                        source = "rewind_points"
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("rewind_points parse failed for %s: %s", session_dir, exc)

    if source == "rewind_points":
        blocks: list[str] = []
        for path in sorted(set(before) | set(after)):
            old, new = before.get(path), after.get(path)
            if old == new:
                continue
            files_changed += 1
            diff_text, add_n, del_n = _unified_diff(old, new, path)
            lines_added += add_n
            lines_removed += del_n
            blocks.append(f"### `{path}`\n\n```diff\n{diff_text}\n```\n")
        meta = _meta_dict(
            source=source,
            files_changed=files_changed,
            lines_added=lines_added,
            lines_removed=lines_removed,
        )
        if not blocks:
            return (
                "# Workspace diff\n\n_No content differences in rewind snapshots._\n",
                meta,
            )
        head = (
            f"# Workspace diff\n\n"
            f"_From `rewind_points` snapshots (not live git)._\n\n"
            f"- **Files:** {files_changed}\n"
            f"- **Lines:** +{lines_added} / -{lines_removed}\n\n---\n\n"
        )
        return head + "\n".join(blocks), meta

    patches: list[str] = []
    for ev in _iter_updates(session_dir):
        params = ev.get("params")
        if not isinstance(params, dict):
            continue
        upd = params.get("update")
        if not isinstance(upd, dict):
            continue
        if upd.get("sessionUpdate") != "tool_call" or (upd.get("title") or "") != "search_replace":
            continue
        ri = upd.get("rawInput") or {}
        if not isinstance(ri, dict):
            continue
        fp = ri.get("file_path") or ri.get("target_file") or "?"
        old_s = ri.get("old_string") or ""
        new_s = ri.get("new_string") or ""
        if not old_s and not new_s:
            continue
        files_changed += 1
        diff_text, add_n, del_n = _unified_diff(str(old_s), str(new_s), str(fp))
        lines_added += add_n
        lines_removed += del_n
        patches.append(f"### edit `{fp}`\n\n```diff\n{diff_text}\n```\n")

    meta = _meta_dict(
        source="search_replace" if patches else None,
        files_changed=files_changed,
        lines_added=lines_added,
        lines_removed=lines_removed,
    )
    if patches:
        body = (
            f"# Workspace diff (approximate)\n\n"
            f"_Per-edit `search_replace` patches — may not equal final files._\n\n"
            f"- **Edits:** {len(patches)}\n"
            f"- **Lines:** +{lines_added} / -{lines_removed}\n\n---\n\n" + "\n".join(patches)
        )
        return body, meta

    return (
        "# Workspace diff\n\n_No rewind snapshots or `search_replace` edits in this session._\n",
        meta,
    )


def format_diff_meta_line(meta: JsonObject) -> str:
    """One-line status for titles / stats."""
    src = meta.get("source")
    if src == "rewind_points":
        return (
            f"rewind: {json_as_int(meta.get('files_changed'), 0)} files "
            f"+{json_as_int(meta.get('lines_added'), 0)}/-{json_as_int(meta.get('lines_removed'), 0)}"
        )
    if src == "search_replace":
        return (
            f"~{json_as_int(meta.get('files_changed'), 0)} search_replace edits "
            f"+{json_as_int(meta.get('lines_added'), 0)}/-{json_as_int(meta.get('lines_removed'), 0)}"
        )
    return "no diff data"
