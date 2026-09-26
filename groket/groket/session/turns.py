"""Segment a session timeline into harness / interactive turns."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .. import event_types as et
from ..models import JsonValue, TraceEvent
from .tagged_blocks import (  # noqa: F401 — re-export for session.turns callers
    harness_user_chrome_heading,
    is_harness_user_chrome,
    operator_prompt_text,
)

_TURN_NUM_RE = re.compile(r"turn_number\s*=\s*(\d+)", re.I)
_OUTCOME_RE = re.compile(r"outcome\s*=\s*(\S+)", re.I)


@dataclass
class TurnSegment:
    """One agent turn (between turn_started and turn_ended, or open-ended)."""

    turn_index: int  # 0-based index in this session
    turn_number: int | None  # from harness marker when present
    prompt_index: int | None = None  # source _meta.promptIndex from the operator message
    outcome: str = ""  # last turn_ended outcome for this segment ("" if open)
    open: bool = False  # no turn_ended yet
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def tool_calls(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type in et.TOOL_CALL_TYPES]

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_error_count(self) -> int:
        return sum(1 for e in self.tool_calls if e.is_error)

    @property
    def user_count(self) -> int:
        return sum(1 for e in self.events if e.event_type in et.USER_TYPES)

    @property
    def assistant_count(self) -> int:
        return sum(1 for e in self.events if e.event_type in et.AGENT_TYPES)

    @property
    def error_event_count(self) -> int:
        return sum(1 for e in self.events if e.is_error or e.event_type in et.ERROR_TYPES)

    @property
    def first_index(self) -> int | None:
        return self.events[0].index if self.events else None

    @property
    def last_index(self) -> int | None:
        return self.events[-1].index if self.events else None

    @property
    def label(self) -> str:
        # Operator-facing id is sequential turn_index after renumber (0-based).
        # Harness turn_number can skip/jump (subagents, background, restamps);
        # keep it on the wire as turnNumber, not in this label.
        n = int(self.turn_index)
        if self.open:
            return f"turn {n} (open)"
        if self.outcome:
            return f"turn {n} ({self.outcome})"
        return f"turn {n}"

    def duration_seconds(self, durations: dict[int, float] | None = None) -> float | None:
        """Span from first to last event timestamp (seconds), else sum of durations map."""
        ts = [int(e.timestamp) for e in self.events if e.timestamp is not None]
        if len(ts) >= 2:
            delta = max(ts) - min(ts)
            # Trace timestamps are typically unix seconds; large deltas in ms are rare for a turn.
            if delta > 86_400 * 365:  # absurd as seconds → treat as ms
                return delta / 1000.0
            return float(delta)
        if durations:
            total = 0.0
            any_d = False
            for e in self.events:
                d = durations.get(e.index)
                if d is not None:
                    total += float(d)
                    any_d = True
            return total if any_d else None
        return None


def _is_turn_started(ev: TraceEvent) -> bool:
    if ev.event_type == et.TURN_STARTED:
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn started" in (ev.content or "").lower()
    return False


def _is_turn_ended(ev: TraceEvent) -> bool:
    if ev.event_type == et.TURN_ENDED:
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn ended" in (ev.content or "").lower()
    return False


def _turn_number_from_event(ev: TraceEvent) -> int | None:
    m = _TURN_NUM_RE.search(ev.content or "")
    if not m:
        return None
    return int(m.group(1))


def _outcome_from_event(ev: TraceEvent) -> str:
    m = _OUTCOME_RE.search(ev.content or "")
    return m.group(1) if m else ("error" if ev.is_error else "unknown")


def is_session_level_timeline_event(ev: TraceEvent) -> bool:
    """Return True for timeline rows that are not part of any agent turn.

    The parser may inject session-scoped chrome (e.g. ``system_prompt.txt`` as
    ``event_type=system``) onto the timeline for display. Turn segmentation and
    per-turn stats use only harness / conversation events; these stay visible
    on the full timeline and when filtering by turn (see browser turn filter).
    """
    return ev.event_type == et.SYSTEM


# Back-compat alias used by older call sites / tests.
_user_is_background_task_completion = is_harness_user_chrome


def is_operator_user_event(ev: TraceEvent) -> bool:
    """True for a real host/operator user message (not harness chrome).

    Messages that are only harness tags (``system-reminder``, preamble, …) are
    false. Payloads that embed ``<user_query>`` are operator even if wrapped.
    """
    if ev.event_type not in et.USER_TYPES and ev.event_type != "user":
        return False
    return bool(operator_prompt_text(ev.content or ""))


def _segment_has_operator_user(seg: TurnSegment) -> bool:
    """True when the segment includes a real host/operator user prompt."""
    return any(is_operator_user_event(ev) for ev in seg.events)


def _segment_has_harness_chrome_user(seg: TurnSegment) -> bool:
    return any(
        (e.event_type in et.USER_TYPES or e.event_type == "user")
        and is_harness_user_chrome(e.content or "")
        for e in seg.events
    )


def _should_merge_background_tail(seg: TurnSegment, prev: TurnSegment) -> bool:
    """Merge *seg* into *prev* when it is harness chrome without an operator prompt.

    Covers background-task completions, bare ``<system-reminder>`` turns (rules /
    skills / MCP status), and empty companion harness turn_started/ended pairs.
    """
    if not _segment_has_operator_user(prev):
        return False
    if _segment_has_operator_user(seg):
        return False
    if _segment_has_harness_chrome_user(seg):
        return True
    # Companion harness turn with no operator user and no tools (e.g. empty
    # turn_started/ended after a background completion).
    has_user = any(e.event_type in et.USER_TYPES or e.event_type == "user" for e in seg.events)
    return not has_user and seg.tool_call_count == 0


def _renumber_segments(segments: list[TurnSegment]) -> list[TurnSegment]:
    for i, seg in enumerate(segments):
        seg.turn_index = i
        if seg.turn_number is None:
            seg.turn_number = i
    return segments


def _assign_prompt_indexes(segments: list[TurnSegment]) -> list[TurnSegment]:
    for seg in segments:
        # Prefer operator user rows; harness chrome may still carry a promptIndex.
        seg.prompt_index = next(
            (
                event.prompt_index
                for event in seg.events
                if is_operator_user_event(event) and event.prompt_index is not None
            ),
            next(
                (
                    event.prompt_index
                    for event in seg.events
                    if event.event_type in et.USER_TYPES and event.prompt_index is not None
                ),
                None,
            ),
        )
    return segments


def _merge_background_completion_segments(
    segments: list[TurnSegment],
) -> list[TurnSegment]:
    """Fold harness turns that only carry background-task completions into the parent.

    Grok emits extra ``turn_started`` / ``turn_ended`` pairs when backgrounded
    subagents/tasks finish. Those are not operator interactive turns; attach
    their events to the previous segment that had a real user prompt.
    """
    if len(segments) < 2:
        return segments
    out: list[TurnSegment] = []
    for seg in segments:
        if out and _should_merge_background_tail(seg, out[-1]):
            prev = out[-1]
            prev.events.extend(seg.events)
            # Keep parent interactive outcome; reflect open state from the tail.
            if seg.open:
                prev.open = True
            continue
        out.append(seg)
    return out


def turn_index_for_event(segments: list[TurnSegment], event_index: int) -> int | None:
    """Return the turn_index of the segment that contains *event_index*, if any."""
    for seg in segments:
        for ev in seg.events:
            if ev.index == event_index:
                return int(seg.turn_index)
    return None


def display_turn_number(seg: TurnSegment) -> int:
    """Sequential operator-facing turn id (0-based ``turn_index`` after renumber).

    Prefer this for UI labels and event maps. Harness ``turn_number`` is still
    available on :attr:`TurnSegment.turn_number` / wire ``turnNumber`` for
    correlation with Grok markers, but must not drive the visible turn list —
    those values skip and reorder when background/subagent turns are merged.
    """
    return int(seg.turn_index)


def event_display_turn_map(segments: list[TurnSegment]) -> dict[int, int]:
    """Map timeline event index → sequential operator turn id.

    :param segments: Output of :func:`segment_timeline_turns`.
    :returns: ``event.index`` → 0-based ``turn_index``.
    """
    out: dict[int, int] = {}
    for seg in segments:
        tn = display_turn_number(seg)
        for ev in seg.events:
            out[int(ev.index)] = tn
    return out


def segment_timeline_turns(timeline: list[TraceEvent]) -> list[TurnSegment]:
    """Split *timeline* into turns using session turn_started / turn_ended markers.

    Session-level timeline events (see :func:`is_session_level_timeline_event`)
    are omitted from segments entirely — they are not a turn and must not
    create an extra segment before the first ``turn started``.

    Remaining events before the first turn_started form turn 0 when present
    (e.g. user messages). Multiple markers produce multiple segments for
    interactive multi-turn.

    Grok harness turns with no operator user prompt (background-task
    completions, bare ``<system-reminder>`` injections) are merged into the
    preceding interactive segment so Turn lists match host follow-ups, not
    harness bookkeeping.
    """
    turn_events = [e for e in timeline if not is_session_level_timeline_event(e)]
    if not turn_events:
        return []

    has_markers = any(_is_turn_started(e) or _is_turn_ended(e) for e in turn_events)
    if not has_markers:
        return _assign_prompt_indexes(
            [
                TurnSegment(
                    turn_index=0,
                    turn_number=None,
                    outcome="",
                    open=True,
                    events=list(turn_events),
                )
            ]
        )

    segments: list[TurnSegment] = []
    current: TurnSegment | None = None
    display_i = -1

    def _close(seg: TurnSegment, outcome: str = "") -> None:
        if outcome:
            seg.outcome = outcome
        seg.open = False

    for ev in turn_events:
        if _is_turn_started(ev):
            tn = _turn_number_from_event(ev)
            # Follow-up user message(s) often land *before* the next turn_started.
            # Keep that open segment and add the harness marker — do not split.
            if (
                current is not None
                and current.events
                and current.open
                and not any(_is_turn_started(e) for e in current.events)
            ):
                current.events.append(ev)
                if tn is not None:
                    current.turn_number = tn
                continue
            if current is not None and current.events:
                # Previous turn had no explicit end — close as open=False unknown
                if current.open and not current.outcome:
                    current.outcome = "unknown"
                current.open = False
                segments.append(current)
            display_i += 1
            current = TurnSegment(
                turn_index=display_i,
                turn_number=tn,
                open=True,
                events=[ev],
            )
            continue

        if _is_turn_ended(ev):
            outcome = _outcome_from_event(ev)
            if current is None:
                display_i += 1
                current = TurnSegment(
                    turn_index=display_i,
                    turn_number=display_i,
                    open=False,
                    outcome=outcome,
                    events=[ev],
                )
                segments.append(current)
                current = None
            else:
                current.events.append(ev)
                _close(current, outcome)
                segments.append(current)
                current = None
            continue

        if current is None:
            if segments:
                # Between turns: late *agent* stream chunks belong to the prior
                # turn; a new *operator* user message starts the next interactive
                # turn (often arrives before the next turn_started marker).
                # Harness user chrome (system-reminder / background completion)
                # is not an operator follow-up — attach to the previous segment.
                if ev.event_type in et.USER_TYPES or ev.event_type == "user":
                    if is_harness_user_chrome(ev.content or ""):
                        segments[-1].events.append(ev)
                    else:
                        display_i = len(segments)
                        current = TurnSegment(
                            turn_index=display_i,
                            turn_number=None,
                            open=True,
                            events=[ev],
                        )
                else:
                    segments[-1].events.append(ev)
            else:
                # True preamble before the first turn_started
                display_i = 0
                current = TurnSegment(
                    turn_index=0,
                    turn_number=None,
                    open=True,
                    events=[ev],
                )
        else:
            current.events.append(ev)

    if current is not None and current.events:
        segments.append(current)

    segments = _merge_background_completion_segments(segments)
    return _assign_prompt_indexes(_renumber_segments(segments))


def turn_summary_rows(
    segments: list[TurnSegment],
    *,
    durations: dict[int, float] | None = None,
    session_context_compact: str = "",
    context_by_turn: dict[int, str] | None = None,
) -> list[dict[str, JsonValue]]:
    """Tabular rows for the Summary turns table (newest first).

    Grok writes context fill only as a session snapshot in ``signals.json``.
    *context_by_turn* holds read-only samples observed during live refresh.
    When absent, *session_context_compact* is shown on the latest segment only.

    Row order is reverse chronological so the open / most recent turn is first;
    ``turn`` indices stay chronological (0…N−1).
    """
    rows: list[dict[str, JsonValue]] = []
    last_idx = len(segments) - 1
    fallback = (session_context_compact or "").strip()
    samples = context_by_turn or {}
    for i, seg in enumerate(segments):
        dur = seg.duration_seconds(durations)
        tools = Counter(e.tool_name for e in seg.tool_calls if e.tool_name)
        top_tools = ", ".join(f"{n}×{c}" for n, c in tools.most_common(3)) or "—"
        ctx = (samples.get(seg.turn_index) or "").strip()
        if not ctx and fallback and i == last_idx:
            ctx = fallback
        rows.append(
            {
                "turn": seg.turn_index,
                "label": seg.label,
                "outcome": seg.outcome or ("open" if seg.open else "—"),
                "open": seg.open,
                "events": seg.event_count,
                "tools": seg.tool_call_count,
                "tool_errors": seg.tool_error_count,
                "users": seg.user_count,
                "assistants": seg.assistant_count,
                "errors": seg.error_event_count,
                "duration_s": dur,
                "context": ctx,
                "top_tools": top_tools,
                "first_index": seg.first_index,
                "last_index": seg.last_index,
            }
        )
    rows.reverse()
    return rows


def format_turns_plain(
    segments: list[TurnSegment], *, durations: dict[int, float] | None = None
) -> str:
    """Plain multi-line turn breakdown (CLI / debug)."""
    if not segments:
        return "(no turns)"
    lines = [f"Turns: {len(segments)}"]
    for row in turn_summary_rows(segments, durations=durations):
        dur = row["duration_s"]
        dur_s = f"{dur:.1f}s" if isinstance(dur, (int, float)) else "—"
        lines.append(
            f"  {row['label']}: events={row['events']} tools={row['tools']} "
            f"errs={row['tool_errors']} dur={dur_s}  [{row['top_tools']}]"
        )
    return "\n".join(lines)
