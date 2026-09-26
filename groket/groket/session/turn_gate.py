"""Locate interactive multi-turn control files for a session on disk."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..constants import INCOMPLETE_STALE_SECONDS
from ..models import JsonObject, json_as_int, json_as_object, json_as_str

logger = logging.getLogger(__name__)

# Single gate directory name under each container traces volume (entrypoint TURN_DIR).
TURN_GATE_NAME = ".groket-turn"
# Authoritative multi-turn resume id written by entrypoint (and host follow-up).
PRIMARY_SESSION_ID_FILE = "primary-session-id"

# Host-side queue when the agent is mid-turn or a follow-up is already staged.
_PENDING_QUEUE = "pending-prompts.jsonl"
_GATE_CONTROL_NAMES = (
    "status.json",
    "command",
    "next-prompt.txt",
    "final_turn",
    PRIMARY_SESSION_ID_FILE,
    _PENDING_QUEUE,
)
_TRACE_ARTIFACT_NAMES = (
    "events.jsonl",
    "chat_history.jsonl",
    "updates.jsonl",
    "summary.json",
    "signals.json",
)


def traces_volume_for_session(session_dir: Path) -> Path | None:
    """Container traces volume for *session_dir* (bind mount root on the host).

    Typical layout: ``…/traces/<container_name>/<cwd-token>/<session_id>``.
    """
    p = Path(session_dir).expanduser().resolve()
    for base in (p.parent.parent, p.parent, p.parent.parent.parent):
        try:
            if base.is_dir() and (
                (base / TURN_GATE_NAME).is_dir() or base.name.startswith("groket-")
            ):
                return base
        except OSError:
            continue
    try:
        gp = p.parent.parent
        return gp if gp.is_dir() else None
    except OSError:
        return None


def turn_gate_dir_for_session(session_dir: Path) -> Path | None:
    """The single turn-gate directory for *session_dir*, or ``None`` if unknown volume."""
    base = traces_volume_for_session(session_dir)
    if base is None:
        return None
    return base / TURN_GATE_NAME


def turn_gate_dirs_for_session(session_dir: Path) -> list[Path]:
    """Existing turn-gate dir(s) for *session_dir* (0 or 1 entry)."""
    gate = turn_gate_dir_for_session(session_dir)
    if gate is None or not gate.is_dir():
        return []
    return [gate]


def write_gate_dirs_for_session(session_dir: Path) -> list[Path]:
    """Gate path the host should write for follow-up / done (creates if needed)."""
    gate = turn_gate_dir_for_session(session_dir)
    if gate is None:
        return []
    return [gate]


def _gate_command(gate: Path) -> str:
    cp = gate / "command"
    if not cp.is_file():
        return ""
    try:
        return cp.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _read_status_file(gate: Path) -> JsonObject:
    sp = gate / "status.json"
    if not sp.is_file():
        return {}
    try:
        return json_as_object(json.loads(sp.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _write_status(gate: Path, *, state: str, session_id: str = "", turn: int = 0) -> None:
    gate.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {"state": state}
    if session_id:
        payload["session_id"] = session_id
    if turn:
        payload["turn"] = turn
    try:
        (gate / "status.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def read_turn_gate_status(session_dir: Path) -> JsonObject:
    """Return turn-gate status for *session_dir*.

    Host ``command=done`` is *not* forced into ``state`` here: the entrypoint
    rewrites status when it finishes. Until then the UI treats the session as
    still live (running / finishing) even though no more follow-ups are accepted.
    """
    gate = turn_gate_dir_for_session(session_dir)
    if gate is None or not gate.is_dir():
        return {}
    data = _read_status_file(gate)
    if not data.get("state"):
        return {}
    return data


def host_requested_done(session_dir: Path) -> bool:
    """True when the host wrote ``command=done`` (session finishing, not idle yet)."""
    gate = turn_gate_dir_for_session(session_dir)
    if gate is None:
        return False
    return _gate_command(gate) == "done"


def final_turn_requested(session_dir: Path) -> bool:
    """True when the host staged a last-turn follow-up (``final_turn`` on the gate)."""
    gate = turn_gate_dir_for_session(session_dir)
    if gate is None:
        return False
    return (gate / "final_turn").is_file()


def read_staged_follow_up(session_dir: Path) -> tuple[str, bool] | None:
    """Return ``(prompt, final)`` when a follow-up is staged on the entrypoint gate.

    Staging writes ``next-prompt.txt`` + ``command=follow_up`` (and optionally
    ``final_turn``). The entrypoint deletes those when it starts the turn; until
    then the operator should still see the prompt in the TUI even though it is
    not yet in ``updates.jsonl`` / chat history.
    """
    for gate in turn_gate_dirs_for_session(session_dir):
        if not _follow_up_already_staged(gate):
            continue
        try:
            text = (gate / "next-prompt.txt").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        return text, (gate / "final_turn").is_file()
    return None


def session_awaits_follow_up(session_dir: Path) -> bool:
    """True only when the gate is waiting and the host has not sent done."""
    settle_stale_session_gates(session_dir)
    for gate in turn_gate_dirs_for_session(session_dir):
        if _gate_command(gate) == "done":
            return False
    st = read_turn_gate_status(session_dir)
    if json_as_str(st.get("state")) == "done":
        return False
    return json_as_str(st.get("state")) == "awaiting_follow_up"


def _ensure_gate_dirs(session_dir: Path) -> list[Path]:
    dirs = write_gate_dirs_for_session(session_dir)
    if dirs:
        return dirs
    base = traces_volume_for_session(session_dir)
    if base is None:
        raise RuntimeError("could not locate traces volume for session")
    return [base / ".groket-turn"]


def _primary_gate(session_dir: Path) -> Path:
    return _ensure_gate_dirs(session_dir)[0]


def _path_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def session_activity_mtime(session_dir: Path) -> float:
    """Newest mtime among session traces and turn-gate control files."""
    newest = 0.0
    sd = Path(session_dir)
    for name in _TRACE_ARTIFACT_NAMES:
        newest = max(newest, _path_mtime(sd / name))
    for gate in turn_gate_dirs_for_session(sd):
        for name in _GATE_CONTROL_NAMES:
            newest = max(newest, _path_mtime(gate / name))
    return newest


def session_activity_stale(
    session_dir: Path,
    *,
    max_age_seconds: float = INCOMPLETE_STALE_SECONDS,
) -> bool:
    """True when traces and gate controls are older than *max_age_seconds*."""
    mtime = session_activity_mtime(session_dir)
    if mtime <= 0:
        return True
    return (time.time() - mtime) > float(max_age_seconds)


def _clear_gate_control_files(gate: Path) -> None:
    """Remove host/entrypoint handoff files after settle or finalize."""
    for name in ("command", "next-prompt.txt", "final_turn", _PENDING_QUEUE):
        try:
            (gate / name).unlink(missing_ok=True)
        except OSError:
            logger.debug("could not clear %s under %s", name, gate, exc_info=True)


def finalize_gate_dir(gate: Path, *, session_id: str = "") -> None:
    """Write ``status.json`` ``state=done`` and clear control files for one gate.

    Host ownership when the eval container is no longer running (stop/remove
    or worker finished). Idempotent.
    """
    gate = Path(gate)
    if not gate.is_dir():
        try:
            gate.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
    prev = _read_status_file(gate)
    sid = (session_id or json_as_str(prev.get("session_id"))).strip()
    turn = json_as_int(prev.get("turn"), 0)
    _clear_gate_control_files(gate)
    _write_status(gate, state="done", session_id=sid, turn=turn)


def finalize_session_gate(session_dir: Path) -> None:
    """Mark all turn gates for *session_dir* as ``done`` after the container exits."""
    sid = Path(session_dir).name
    for gate in turn_gate_dirs_for_session(session_dir):
        finalize_gate_dir(gate, session_id=sid)


def settle_stale_session_gates(session_dir: Path) -> bool:
    """Finalize zombie gates when the agent is gone and nothing is fresh.

    A session is settled when:

    * there is no open harness turn (or the open turn's traces are stale), and
    * session + gate control mtimes are older than :data:`INCOMPLETE_STALE_SECONDS`,
    * and the gate still looks live (``running`` / staged follow-up / ``final_turn`` /
      host ``done`` without finalize).

    :returns: True when gates were finalized this call.
    """
    sd = Path(session_dir)
    st = read_turn_gate_status(sd)
    state = json_as_str(st.get("state"))
    if state == "done":
        # Still clear leftover final_turn / next-prompt from dual-gate bugs.
        dirty = False
        for gate in turn_gate_dirs_for_session(sd):
            for name in ("final_turn", "next-prompt.txt", "command", _PENDING_QUEUE):
                if (gate / name).is_file():
                    dirty = True
                    break
            if dirty:
                break
        if not dirty:
            return False
        finalize_session_gate(sd)
        return True

    open_turn = events_have_open_turn(sd)
    stale = session_activity_stale(sd)
    if open_turn and not stale:
        return False
    if not stale:
        return False

    looks_live = (
        state in ("running", "awaiting_follow_up")
        or host_requested_done(sd)
        or final_turn_requested(sd)
        or read_staged_follow_up(sd) is not None
    )
    if not looks_live:
        return False

    logger.info(
        "Settling stale turn gates for %s (state=%s open_turn=%s)",
        sd.name,
        state or "?",
        open_turn,
    )
    finalize_session_gate(sd)
    return True


def session_needs_live_timeline(session_dir: Path) -> bool:
    """True when the browser should keep polling/re-parsing traces.

    Requires recent activity (or an open harness turn that is still fresh).
    Orphan ``final_turn`` / ``status=running`` alone must **not** keep the UI
    in a live refresh loop after the container dies.
    """
    from ..constants import LIVE_UPDATES_FRESH_SECONDS

    settle_stale_session_gates(session_dir)
    if session_activity_stale(session_dir):
        return False
    life = lifecycle_state(session_dir)
    if life in ("running", "ending"):
        return True
    if events_have_open_turn(session_dir):
        return True
    # Fresh staging of a follow-up (agent may pick it up soon).
    if read_staged_follow_up(session_dir) is not None:
        return True
    # Clean interactive wait / finished session: no agent writing traces.
    if life in ("awaiting_follow_up", "done"):
        return False
    # Gate status can lag (empty/unknown) while Grok still appends updates.jsonl
    # — keep snapshotting while the trace file is fresh so new rows appear.
    try:
        upd = Path(session_dir) / "updates.jsonl"
        if upd.is_file():
            age = time.time() - float(upd.stat().st_mtime)
            if 0 <= age < float(LIVE_UPDATES_FRESH_SECONDS):
                return True
    except OSError:
        pass
    return False


def _queue_path(gate: Path) -> Path:
    return gate / _PENDING_QUEUE


def list_queued_follow_up_items(session_dir: Path) -> list[tuple[str, bool]]:
    """Queued follow-ups as ``(prompt, final_turn)``."""
    try:
        gate = _primary_gate(session_dir)
    except RuntimeError:
        return []
    qp = _queue_path(gate)
    if not qp.is_file():
        return []
    out: list[tuple[str, bool]] = []
    try:
        for line in qp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("prompt") is not None:
                    text = str(obj["prompt"]).strip()
                    if text:
                        out.append((text, bool(obj.get("final"))))
                    continue
            except json.JSONDecodeError:
                pass
            out.append((line, False))
    except OSError:
        return []
    return out


def list_queued_follow_ups(session_dir: Path) -> list[str]:
    """Prompt texts queued on the host (display / length)."""
    return [p for p, _final in list_queued_follow_up_items(session_dir)]


def _write_queue(gate: Path, items: list[tuple[str, bool]]) -> None:
    qp = _queue_path(gate)
    if not items:
        try:
            qp.unlink(missing_ok=True)
        except OSError:
            pass
        return
    gate.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps({"prompt": p, "final": bool(fin)}, ensure_ascii=False) + "\n" for p, fin in items
    )
    qp.write_text(body, encoding="utf-8")


def enqueue_follow_up(session_dir: Path, prompt: str, *, final: bool = False) -> int:
    """Append a follow-up to the host queue; returns new queue length."""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("follow-up prompt is empty")
    gate = _primary_gate(session_dir)
    gate.mkdir(parents=True, exist_ok=True)
    q = list_queued_follow_up_items(session_dir)
    q.append((text, bool(final)))
    _write_queue(gate, q)
    return len(q)


def write_primary_session_id(gate: Path, session_id: str) -> None:
    """Persist the multi-turn resume session id on the gate (host or entrypoint)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    gate.mkdir(parents=True, exist_ok=True)
    (gate / PRIMARY_SESSION_ID_FILE).write_text(sid + "\n", encoding="utf-8")


def read_primary_session_id(gate: Path) -> str:
    """Return the gate's primary-session-id, or empty."""
    fp = gate / PRIMARY_SESSION_ID_FILE
    if not fp.is_file():
        return ""
    try:
        return fp.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _stage_follow_up_on_gate(
    gate: Path, text: str, *, session_id: str, final: bool = False
) -> None:
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "next-prompt.txt").write_text(text, encoding="utf-8")
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    sid = (session_id or "").strip()
    if sid:
        write_primary_session_id(gate, sid)
    final_path = gate / "final_turn"
    if final:
        final_path.write_text("1\n", encoding="utf-8")
    else:
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            pass
    prev = _read_status_file(gate)
    turn = json_as_int(prev.get("turn"), 0)
    _write_status(
        gate,
        state="running",
        session_id=json_as_str(prev.get("session_id")) or sid,
        turn=turn,
    )


def _follow_up_already_staged(gate: Path) -> bool:
    if _gate_command(gate) != "follow_up":
        return False
    np = gate / "next-prompt.txt"
    try:
        return np.is_file() and bool(np.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def write_follow_up_for_session(session_dir: Path, prompt: str, *, final: bool = False) -> str:
    """Stage a follow-up for the entrypoint, or queue it if the gate is busy.

    When *final* is true, the entrypoint runs this turn then exits without
    awaiting another follow-up (``final_turn`` on the gate).
    """
    text = (prompt or "").strip()
    if not text:
        raise ValueError("follow-up prompt is empty")
    dirs = _ensure_gate_dirs(session_dir)
    gate = dirs[0]
    sid = Path(session_dir).name

    if _gate_command(gate) == "done":
        raise RuntimeError("session already marked done")

    awaits = session_awaits_follow_up(session_dir)
    staged = _follow_up_already_staged(gate)
    if awaits and not staged:
        _stage_follow_up_on_gate(gate, text, session_id=sid, final=final)
        return "sent"

    enqueue_follow_up(session_dir, text, final=final)
    return "queued"


def drain_queued_follow_up(session_dir: Path) -> str | None:
    """If gate is awaiting and queue non-empty, stage the next prompt. Returns it."""
    if not session_awaits_follow_up(session_dir):
        return None
    try:
        gate = _primary_gate(session_dir)
    except RuntimeError:
        return None
    if _follow_up_already_staged(gate):
        return None
    items = list_queued_follow_up_items(session_dir)
    if not items:
        return None
    (text, final), rest = items[0], items[1:]
    _write_queue(gate, rest)
    sid = Path(session_dir).name
    _stage_follow_up_on_gate(gate, text, session_id=sid, final=final)
    return text


def write_done_for_session(session_dir: Path) -> None:
    """Ask a live entrypoint to stop (``command=done``).

    Does **not** rewrite ``status.json``. While the agent is still mid-turn,
    :func:`lifecycle_state` reports ``ending``. After the container stops, the
    host must call :func:`finalize_session_gate` (see
    :meth:`~groket.runs.run_manager.RunManager.stop_session_container`).
    """
    dirs = _ensure_gate_dirs(session_dir)
    for gate in dirs:
        gate.mkdir(parents=True, exist_ok=True)
        (gate / "command").write_text("done\n", encoding="utf-8")
        try:
            (gate / "final_turn").unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _write_queue(gate, [])
        except OSError:
            logger.debug("clear pending queue on done failed", exc_info=True)


# path -> (mtime_ns, size, has_open_turn). Avoid re-parsing multi‑MB events.jsonl
# on every Textual check_action / binding refresh (hot path during key nav).
_OPEN_TURN_CACHE: dict[str, tuple[int, int, bool]] = {}


def events_have_open_turn(session_dir: Path) -> bool:
    """True when ``events.jsonl`` ends on an unmatched ``turn_started``.

    Used by :func:`lifecycle_state` so "ending" is tied to the harness turn
    contract, not file mtimes.

    Result is cached by ``(mtime_ns, size)`` so footer/binding checks and
    gate probes do not re-scan the whole file on every keypress.
    """
    events_file = Path(session_dir) / "events.jsonl"
    try:
        if not events_file.is_file():
            return False
        st = events_file.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
        cache_key = str(events_file.resolve())
    except OSError:
        return False

    cached = _OPEN_TURN_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2]

    open_starts = 0
    last_turn = ""
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                et = raw.get("type")
                if et == "turn_started":
                    open_starts += 1
                    last_turn = "turn_started"
                elif et == "turn_ended":
                    open_starts = max(0, open_starts - 1)
                    last_turn = "turn_ended"
    except OSError:
        return False
    result = open_starts > 0 and last_turn == "turn_started"
    _OPEN_TURN_CACHE[cache_key] = (mtime_ns, size, result)
    return result


def _events_have_any_turn_ended(session_dir: Path) -> bool:
    """True when ``events.jsonl`` contains at least one ``turn_ended``."""
    events_file = Path(session_dir) / "events.jsonl"
    if not events_file.is_file():
        return False
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict) and raw.get("type") == "turn_ended":
                    return True
    except OSError:
        return False
    return False


def lifecycle_state(session_dir: Path) -> str:
    """Authoritative interactive lifecycle for list/UI.

    :returns:
        ``done`` | ``awaiting_follow_up`` | ``running`` | ``ending`` |
        ``timeout`` | other status string | ``""``.

    Contract:

    - ``status.json`` is the durable state while the entrypoint is alive.
    - Stale gates (no fresh traces/control files for
      :data:`~groket.constants.INCOMPLETE_STALE_SECONDS`) are finalized to
      ``done`` so dead containers cannot look live forever.
    - Host ``command=done``: open harness turn → ``ending``; else ``done``.
    - Host ``final_turn``: staged prompt → ``running``; open harness turn →
      ``ending``; harness closed (or leftover flag after the agent finished) →
      ``done``. Must **not** stay ``running`` for the full stale window after
      ``turn_ended`` — that made Last turn look like it never closed.
    - Otherwise return the gate ``state`` as written.
    """
    settle_stale_session_gates(session_dir)

    st = read_turn_gate_status(session_dir)
    state = json_as_str(st.get("state"))
    if state == "done":
        return "done"

    open_turn = events_have_open_turn(session_dir)
    stale = session_activity_stale(session_dir)
    final_turn = final_turn_requested(session_dir)

    # Trust a live awaiting status before orphan host-done probes. Do **not**
    # ignore a real last-turn request on the entrypoint gate (fall through).
    if state == "awaiting_follow_up" and not host_requested_done(session_dir) and not final_turn:
        return "awaiting_follow_up"

    if host_requested_done(session_dir):
        if open_turn and not stale:
            return "ending"
        return "done"

    if final_turn:
        staged = read_staged_follow_up(session_dir) is not None
        if open_turn and not stale:
            return "ending"
        if staged and not stale:
            return "running"
        # Prompt consumed, turn not open yet: handoff before turn_started —
        # only when the harness has never closed a turn (empty / brand-new).
        # Once turn_ended exists, settle done even if final_turn remains while
        # the entrypoint is still doing share/cleanup.
        if state == "running" and not stale and not _events_have_any_turn_ended(session_dir):
            return "running"
        return "done"

    if state == "running":
        if stale and not open_turn:
            return "done"
        if open_turn and not stale:
            return "running"
        if not stale:
            return "running"
        return "done"

    if state in ("awaiting_follow_up", "timeout"):
        return state
    return state


def session_pending_label(session_dir: Path, *, turn_in_progress: bool = False) -> str:
    """Short status for UI: interactive wait, agent running, or empty if settled."""
    life = lifecycle_state(session_dir)
    if life == "done":
        return ""
    if life == "ending":
        return "ending_done" if host_requested_done(session_dir) else "ending_last_turn"

    st = read_turn_gate_status(session_dir)
    turn = json_as_int(st.get("turn"), 0)
    queued = 0
    try:
        queued = len(list_queued_follow_ups(session_dir))
    except Exception:
        queued = 0
    qbit = f", {queued} queued" if queued else ""
    if life == "awaiting_follow_up":
        base = f"awaiting follow-up (turn {turn})" if turn else "awaiting follow-up"
        return base + qbit
    if life == "running":
        base = f"agent running (turn {turn})" if turn else "agent running"
        return base + qbit
    if turn_in_progress:
        return "turn in progress" + qbit
    if life and life not in ("unknown",):
        return life + qbit
    if queued:
        return f"{queued} follow-up(s) queued"
    return ""
