#!/usr/bin/env python3
"""Pick the operator primary Grok session under a sessions mount.

Used by the eval entrypoint for multi-turn ``--resume`` (batch scripted turns
and interactive follow-ups). Must never return a subagent session id.

Layout (typical)::

    <sessions_root>/%2Fworkspace/<session-id>/
    <sessions_root>/%2Fworkspace/<parent-id>/subagents/<sub-id>/   # nested
    <sessions_root>/%2Fworkspace/<sub-id>/                        # sibling mirror

Exit codes:
  0 — printed a session id (basename only) or full path with ``--path``
  1 — no primary session found
  2 — usage / invalid args
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def is_resume_seed_path(path: Path) -> bool:
    if ".groket-resume-seed" in path.parts:
        return True
    try:
        return ".groket-resume-seed" in path.resolve().parts
    except OSError:
        return False


def is_subagent_session_dir(path: Path) -> bool:
    """Match host :func:`groket.parser._is_subagent_session_dir` rules."""
    if "subagents" in path.parts:
        return True
    sj = path / "summary.json"
    if sj.is_file():
        try:
            kind = str(json.loads(sj.read_text(encoding="utf-8")).get("session_kind") or "")
            if kind.strip().lower() == "subagent":
                return True
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    name = path.name
    parent = path.parent
    try:
        for sib in parent.iterdir():
            if not sib.is_dir() or sib.name == name:
                continue
            if (sib / "subagents" / name).exists():
                return True
    except OSError:
        pass
    return False


def looks_like_session_dir(path: Path) -> bool:
    return any(
        (path / name).is_file() for name in ("chat_history.jsonl", "updates.jsonl", "summary.json")
    )


def session_trace_mtime(path: Path) -> float:
    best = 0.0
    for name in (
        "summary.json",
        "chat_history.jsonl",
        "updates.jsonl",
        "events.jsonl",
        "signals.json",
    ):
        fp = path / name
        try:
            best = max(best, fp.stat().st_mtime)
        except OSError:
            pass
    return best


def iter_session_dirs(sessions_root: Path) -> list[Path]:
    """Candidate session dirs (depth: token / session-id), not filtered."""
    root = Path(sessions_root)
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        tokens = list(root.iterdir())
    except OSError:
        return []
    for token in tokens:
        if not token.is_dir():
            continue
        name = token.name
        if name.startswith(".groket-turn") or name == ".groket-resume-seed":
            continue
        try:
            children = list(token.iterdir())
        except OSError:
            continue
        for d in children:
            if not (d.is_dir() or d.is_symlink()):
                continue
            if d.name in ("compaction",) or d.name.startswith("."):
                continue
            out.append(d)
    return out


def list_primary_session_dirs(sessions_root: Path) -> list[Path]:
    """Primary (non-subagent, non-seed) session directories."""
    primaries: list[Path] = []
    for d in iter_session_dirs(sessions_root):
        if is_resume_seed_path(d):
            continue
        if is_subagent_session_dir(d):
            continue
        if not looks_like_session_dir(d):
            continue
        primaries.append(d)
    return primaries


def first_prompt_history_session_id(sessions_root: Path) -> str:
    """Session id from the chronologically first prompt_history row, if any."""
    root = Path(sessions_root)
    if not root.is_dir():
        return ""
    histories: list[Path] = []
    try:
        for token in sorted(root.iterdir(), key=lambda p: p.name):
            if not token.is_dir() or token.name.startswith("."):
                continue
            ph = token / "prompt_history.jsonl"
            if ph.is_file():
                histories.append(ph)
    except OSError:
        return ""
    best_sid = ""
    best_ts: str | None = None
    for ph in histories:
        try:
            lines = ph.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            sid = str(row.get("session_id") or "").strip()
            if not sid:
                continue
            ts = str(row.get("timestamp") or "").strip() or None
            if not best_sid:
                best_sid, best_ts = sid, ts
                continue
            if ts and best_ts and ts < best_ts:
                best_sid, best_ts = sid, ts
            elif ts and best_ts is None:
                best_sid, best_ts = sid, ts
    return best_sid


def resolve_primary_session_id(
    sessions_root: Path,
    *,
    preferred_id: str = "",
) -> str:
    """Return basename of the primary session to resume, or \"\".

    Preference order:
      1. *preferred_id* if it still names a primary session dir
      2. First prompt_history session id if that dir is primary
      3. Newest primary by trace mtime
    """
    primaries = list_primary_session_dirs(sessions_root)
    by_name = {p.name: p for p in primaries}
    pref = (preferred_id or "").strip()
    if pref and pref in by_name:
        return pref
    hist = first_prompt_history_session_id(sessions_root)
    if hist and hist in by_name:
        return hist
    if not primaries:
        return ""
    primaries.sort(key=session_trace_mtime)
    return primaries[-1].name


def resolve_primary_session_path(
    sessions_root: Path,
    *,
    preferred_id: str = "",
) -> Path | None:
    sid = resolve_primary_session_id(sessions_root, preferred_id=preferred_id)
    if not sid:
        return None
    for p in list_primary_session_dirs(sessions_root):
        if p.name == sid:
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sessions_root",
        nargs="?",
        default="/root/.grok/sessions",
        help="Host/container sessions mount (default: /root/.grok/sessions)",
    )
    parser.add_argument(
        "--preferred",
        default="",
        help="Sticky session id to keep if still a primary",
    )
    parser.add_argument(
        "--path",
        action="store_true",
        help="Print full directory path instead of basename id",
    )
    parser.add_argument(
        "--check",
        default="",
        metavar="SESSION_ID",
        help="Exit 0 if SESSION_ID is a primary under sessions_root, else 1",
    )
    args = parser.parse_args(argv)
    root = Path(args.sessions_root)
    if args.check:
        primaries = {p.name for p in list_primary_session_dirs(root)}
        return 0 if args.check.strip() in primaries else 1
    if args.path:
        path = resolve_primary_session_path(root, preferred_id=args.preferred)
        if path is None:
            return 1
        print(path)
        return 0
    sid = resolve_primary_session_id(root, preferred_id=args.preferred)
    if not sid:
        return 1
    print(sid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
