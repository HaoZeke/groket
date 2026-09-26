"""Seed an ended Grok session into a new eval traces volume for ``--resume``."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Eval containers use cwd=/workspace → sessions under this token.
_DEFAULT_CWD_TOKEN = "%2Fworkspace"
# Staging tree under the container traces volume. Operator session listing
# never treats paths under this name as eval rows (same class of convention as
# ``*.stage`` / turn-gate dirs). Grok still sees the parent at the live
# ``<cwd-token>/<session_id>`` path via a symlink into this tree.
RESUME_SEED_DIRNAME = ".groket-resume-seed"
# Legacy name kept so find_sessions still skips old full-tree seeds on disk.
WORKSPACE_SEED_DIRNAME = ".groket-workspace-seed"


def resume_session_id(session_dir: Path) -> str:
    """Grok session id (directory name) for *session_dir*."""
    return Path(session_dir).name.strip()


def resume_cwd_token(session_dir: Path) -> str:
    """Cwd-encoded parent segment under the container sessions mount.

    Typical layout: ``traces/<container>/%2Fworkspace/<session_id>/``.
    Falls back to ``%2Fworkspace`` when the parent is not an encoded path.
    """
    parent = Path(session_dir).resolve().parent
    name = parent.name
    if name.startswith("%") or "%2F" in name or name == "workspace":
        return name
    return _DEFAULT_CWD_TOKEN


def can_resume_session(session_dir: Path) -> bool:
    """True when *session_dir* has artifacts Grok can resume from."""
    p = Path(session_dir)
    if not p.is_dir():
        return False
    for name in ("chat_history.jsonl", "summary.json", "events.jsonl"):
        if (p / name).is_file():
            return True
    return False


def is_resume_seed_path(path: Path) -> bool:
    """True when *path* is resume history substrate, not an operator eval session.

    Substrate lives under :data:`RESUME_SEED_DIRNAME`, or is a live path that
    resolves into that tree (symlink placed for ``grok --resume``).
    """
    p = Path(path)
    if RESUME_SEED_DIRNAME in p.parts:
        return True
    try:
        if RESUME_SEED_DIRNAME in p.resolve().parts:
            return True
    except OSError:
        return False
    return False


def fork_parent_session_dir(session_dir: Path) -> Path | None:
    """Return the seeded parent session for a forked child, or ``None``.

    Fork-resume launches write ``resume_parent_session_id`` /
    ``resume_fork_session_id`` into ``groket-launch.json``. Grok writes the
    child under a new id and often omits parent turn markers from the child's
    ``events.jsonl``. The host keeps the parent under
    ``.groket-resume-seed/<cwd-token>/<parent_id>/`` for ``--resume``.

    :param session_dir: Candidate forked child session directory.
    :returns: Parent seed path when *session_dir* is the forked child and the
        seed exists; otherwise ``None``.
    """
    from ..runs.launch_meta import find_launch_meta_file, read_launch_meta

    sd = Path(session_dir)
    launch = read_launch_meta(sd)
    if launch is None or not launch.is_fork_resume:
        return None
    parent_id = (launch.resume_parent_session_id or "").strip()
    child_id = (launch.resume_fork_session_id or "").strip()
    if not parent_id or not child_id or sd.name != child_id:
        return None

    meta_file = find_launch_meta_file(sd)
    if meta_file is None:
        return None
    traces_vol = meta_file.parent
    token = sd.parent.name
    if not token:
        token = _DEFAULT_CWD_TOKEN

    candidates = (
        traces_vol / RESUME_SEED_DIRNAME / token / parent_id,
        traces_vol / token / parent_id,
    )
    for cand in candidates:
        if cand.is_dir() and can_resume_session(cand):
            try:
                return cand.resolve() if cand.is_symlink() else cand
            except OSError:
                return cand
    return None


def collect_installed_plugin_dir_aliases(session_dir: Path, *, limit: int = 8) -> list[str]:
    """Return ``installed-plugins/<id>`` directory basenames referenced in *session_dir*.

    Parent sessions embed absolute paths like
    ``/root/.grok/installed-plugins/src-3b9c6c63/skills/...``. After a fork the
    container reinstalls plugins under a new hash, so those paths 404 unless
    the entrypoint recreates the aliases. Scans a few small/medium artifacts
    (not multi‑100MB ``updates.jsonl`` when avoidable).

    :param session_dir: Parent session to scan (usually the resume seed source).
    :param limit: Max distinct aliases to return.
    :returns: Unique directory names (e.g. ``src-3b9c6c63``), order preserved.
    """
    import re

    sd = Path(session_dir)
    if not sd.is_dir():
        return []
    pat = re.compile(r"installed-plugins/([A-Za-z0-9._-]+)/")
    found: list[str] = []
    seen: set[str] = set()
    # Prefer small files first; updates.jsonl only if still empty.
    candidates = (
        "system_prompt.txt",
        "chat_history.jsonl",
        "summary.json",
        "events.jsonl",
        "updates.jsonl",
    )
    max_bytes = 4 * 1024 * 1024
    for name in candidates:
        if len(found) >= limit:
            break
        fp = sd / name
        if not fp.is_file():
            continue
        try:
            size = fp.stat().st_size
        except OSError:
            continue
        try:
            if size > max_bytes and name == "updates.jsonl":
                # Tail only for huge traces.
                with fp.open("rb") as fh:
                    fh.seek(max(0, size - max_bytes))
                    raw = fh.read().decode("utf-8", errors="replace")
            else:
                raw = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in pat.finditer(raw):
            alias = m.group(1)
            if not alias or alias in seen or alias in ("registry.json",):
                continue
            if alias.endswith(".json"):
                continue
            seen.add(alias)
            found.append(alias)
            if len(found) >= limit:
                break
    return found


def seed_resume_into_traces_vol(traces_vol: Path, source_session_dir: Path) -> str:
    """Copy *source_session_dir* into *traces_vol* for a new container.

    Layout (filesystem contract):

    * **Substrate** (canonical copy):
      ``<traces_vol>/.groket-resume-seed/<cwd-token>/<session_id>/``
    * **Live path for Grok** (symlink into substrate):
      ``<traces_vol>/<cwd-token>/<session_id>`` → substrate

    :func:`~groket.parser.find_sessions` does not list substrate (or live
    paths that resolve into it). The forked child session is a normal directory
    under ``<cwd-token>/`` and is listed as usual.

    :param traces_vol: Host path bound to ``/root/.grok/sessions`` for the new run.
    :param source_session_dir: Ended session directory on the host.
    :returns: Session id string for ``RESUME_SESSION_ID``.
    :raises FileNotFoundError: When the source directory is missing.
    :raises ValueError: When the source has no resumeable artifacts.
    """
    source = Path(source_session_dir).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"resume source not found: {source}")
    if not can_resume_session(source):
        raise ValueError(f"session has no chat/events/summary to resume: {source}")

    sid = resume_session_id(source)
    if not sid:
        raise ValueError(f"empty session id for {source}")

    token = resume_cwd_token(source)
    traces = Path(traces_vol)
    seed_dest = traces / RESUME_SEED_DIRNAME / token / sid
    live_dest = traces / token / sid

    seed_dest.parent.mkdir(parents=True, exist_ok=True)
    if seed_dest.exists():
        shutil.rmtree(seed_dest)
    shutil.copytree(source, seed_dest)

    for lock in seed_dest.glob("*.lock"):
        try:
            lock.unlink()
        except OSError:
            logger.debug("could not remove lock %s", lock, exc_info=True)

    # Drop container-local / host-eval noise that would mislead the fork
    # container (parent share URLs, search DB, locks). Resume only needs
    # chat/events/summary/history for ``grok --resume``.
    for noise in (
        "groket-share.json",
        "session_search.sqlite",
        "session_search.sqlite-wal",
        "session_search.sqlite-shm",
    ):
        try:
            (seed_dest / noise).unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove seed noise %s", noise, exc_info=True)

    hist = source.parent / "prompt_history.jsonl"
    if hist.is_file():
        try:
            shutil.copy2(hist, seed_dest.parent / "prompt_history.jsonl")
        except OSError:
            logger.debug("could not copy prompt_history.jsonl", exc_info=True)

    # Live path Grok expects: symlink into substrate (not a second full copy).
    live_dest.parent.mkdir(parents=True, exist_ok=True)
    if live_dest.is_symlink() or live_dest.exists():
        if live_dest.is_dir() and not live_dest.is_symlink():
            shutil.rmtree(live_dest)
        else:
            live_dest.unlink()
    # Relative link so the seed stays portable if the traces volume moves.
    rel = seed_dest.resolve().relative_to(live_dest.parent.resolve(), walk_up=True)
    live_dest.symlink_to(rel, target_is_directory=True)

    logger.info("Seeded resume session %s → %s (live link %s)", sid, seed_dest, live_dest)
    return sid
