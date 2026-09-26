"""Map structured LLM review JSON to findings and report artifacts."""

from __future__ import annotations

import re

from ...models import JsonObject, JsonValue, Severity, TraceEvent
from ..base import Finding
from ..order import sort_findings_by_turn
from .context import SessionContextPack

_SEVERITY_MAP = {
    "critical": Severity.HIGH,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.LOW,
}

_INCOMPLETE_SUMMARY_RE = re.compile(
    r"\b("
    r"offloaded\s+prompt|reading\s+the\s+full|before\s+producing|"
    r"let\s+me\s+read|i\s+will\s+review|inspecting\s+the|"
    r"need\s+to\s+read|loading\s+the\s+timeline|cannot\s+access|"
    r"drafting\s+ranked\s+findings|complete\s+timeline\s+evidence|"
    r"truncated\s+in\s+the\s+chat"
    r")\b",
    re.I,
)


def _one_line(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return text[: limit - 1] + "…"


def is_incomplete_review(payload: JsonObject | None) -> bool:
    """True when the model returned a schema shell without a real review."""
    if payload is None:
        return True
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return True
    summary = str(payload.get("summary") or "").strip()
    all_clear = bool(payload.get("all_clear"))
    if findings:
        for item in findings:
            if not isinstance(item, dict):
                continue
            what = str(item.get("what_model_did") or "").strip()
            should = str(item.get("what_should_have_done") or "").strip()
            if what or should:
                return False
        return True
    if all_clear and summary and not _INCOMPLETE_SUMMARY_RE.search(summary):
        return False
    if _INCOMPLETE_SUMMARY_RE.search(summary):
        return True
    if not all_clear:
        return True
    return not summary


def _coerce_severity(value: JsonValue | None) -> Severity:
    key = str(value or "medium").strip().lower()
    return _SEVERITY_MAP.get(key, Severity.MEDIUM)


def map_review_findings(
    payload: JsonObject,
    timeline: list[TraceEvent],
    *,
    plugin_id: str,
    detail_mode: str = "one_line",
) -> list[Finding]:
    """Convert structured review JSON into timeline-linked :class:`Finding` rows.

    :param payload: Parsed model JSON (summary / findings / all_clear).
    :param timeline: Session timeline for validating evidence indices.
    :param plugin_id: Analyzer id for Finding.plugin_id and Finding.id prefix.
    :param detail_mode: ``one_line`` for Findings tab; ``full_fields`` for multi-line.
    :returns: Findings suitable for the Findings tab (with event links).
    """
    by_index = {ev.index: ev for ev in timeline}
    by_tcid: dict[str, TraceEvent] = {}
    for ev in timeline:
        if ev.tool_call_id and ev.tool_call_id not in by_tcid:
            by_tcid[ev.tool_call_id] = ev

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return []

    out: list[Finding] = []
    for i, item in enumerate(raw_findings):
        if not isinstance(item, dict):
            continue
        title = _one_line(str(item.get("title") or f"Issue {i + 1}"), 120)
        fid = str(item.get("id") or f"finding-{i + 1}").strip()
        fid = re.sub(r"[^a-zA-Z0-9._-]+", "-", fid)[:80] or f"finding-{i + 1}"
        what = str(item.get("what_model_did") or "").strip()
        should = str(item.get("what_should_have_done") or "").strip()
        why = str(item.get("why_mistake") or "").strip()
        category = str(item.get("category") or "Feedback").strip() or "Feedback"
        severity = _coerce_severity(item.get("severity"))

        event_indices: list[int] = []
        update_indices: list[int] = []
        tool_call_ids: list[str] = []
        evidence_notes: list[str] = []
        ev_raw = item.get("evidence")
        if isinstance(ev_raw, list):
            for ev_item in ev_raw:
                if not isinstance(ev_item, dict):
                    continue
                try:
                    idx = int(ev_item.get("event_index"))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                tev = by_index.get(idx)
                if tev is None:
                    continue
                if tev.index not in event_indices:
                    event_indices.append(tev.index)
                if tev.update_index and tev.update_index not in update_indices:
                    update_indices.append(tev.update_index)
                tcid = str(ev_item.get("tool_call_id") or tev.tool_call_id or "").strip()
                if tcid and tcid in by_tcid and tcid not in tool_call_ids:
                    tool_call_ids.append(tcid)
                elif tev.tool_call_id and tev.tool_call_id not in tool_call_ids:
                    tool_call_ids.append(tev.tool_call_id)
                note = str(ev_item.get("note") or "").strip()
                if note:
                    evidence_notes.append(f"#{tev.index}: {note}")
                else:
                    evidence_notes.append(f"#{tev.index} {tev.type_label}")

        if detail_mode == "full_fields":
            parts = [
                f"What the model did: {what}" if what else "",
                f"What it should have done: {should}" if should else "",
                f"Why we think it made the mistake: {why}" if why else "",
            ]
            if evidence_notes:
                parts.append("Evidence: " + "; ".join(evidence_notes))
            detail = "\n".join(p for p in parts if p)
        else:
            detail = _one_line(what or title, 220)
            if evidence_notes:
                ev_bit = "; ".join(evidence_notes[:4])
                if len(detail) + len(ev_bit) < 260:
                    detail = _one_line(f"{detail} [{ev_bit}]", 280)

        extras: JsonObject = {
            "what_model_did": what,
            "what_should_have_done": should,
            "why_mistake": why,
            "should_have": should,
            "evidence_event_indices": list(event_indices),
        }
        out.append(
            Finding(
                id=f"{plugin_id}-{fid}",
                plugin_id=plugin_id,
                severity=severity,
                title=title,
                detail=detail,
                category=category,
                tool_call_ids=tool_call_ids,
                update_indices=sorted(set(update_indices)),
                event_indices=sorted(set(event_indices)),
                extras=extras,
            )
        )
    return sort_findings_by_turn(out, timeline)


def render_review_report(
    payload: JsonObject,
    findings: list[Finding],
    pack: SessionContextPack,
    *,
    title_prefix: str = "Feedback review",
) -> str:
    """Render the Report-tab markdown artifact (full did/should/why + evidence)."""
    summary = str(payload.get("summary") or "").strip()
    all_clear = bool(payload.get("all_clear"))
    lines = [
        f"# {title_prefix} — {pack.session_id}",
        "",
    ]
    meta_bits: list[str] = []
    if pack.meta.model_id:
        meta_bits.append(f"model `{pack.meta.model_id}`")
    meta_bits.append(f"{pack.turn_count} turn(s)")
    meta_bits.append(f"{pack.event_count} event(s)")
    if pack.meta.turn_outcome:
        meta_bits.append(f"outcome `{pack.meta.turn_outcome}`")
    if meta_bits:
        lines.append("_" + " · ".join(meta_bits) + "_")
        lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    if all_clear and not findings:
        lines.append("No material issues found in this session.")
        return "\n".join(lines).strip() + "\n"
    if not findings:
        lines.append("Review completed but produced no structured findings.")
        return "\n".join(lines).strip() + "\n"

    by_index = {e.index: e for e in pack.timeline}
    for n, f in enumerate(findings, start=1):
        sev = f.severity.value.upper()
        lines.append(f"## {n}. {f.title} ({sev})")
        lines.append("")
        what = str(f.extras.get("what_model_did") or "")
        should = str(f.extras.get("what_should_have_done") or "")
        why = str(f.extras.get("why_mistake") or "")
        if what:
            lines.append(f"- **What the model did:** {what}")
        if should:
            lines.append(f"- **What it should have done:** {should}")
        if why:
            lines.append(f"- **Why we think it made the mistake:** {why}")
        ev_bits: list[str] = []
        for i in f.event_indices:
            tev = by_index.get(i)
            if tev is None:
                continue
            bit = f"#{i}"
            if tev.tool_name:
                bit += f" `{tev.tool_name}`"
            if tev.tool_call_id:
                bit += f" (`{tev.tool_call_id}`)"
            ev_bits.append(bit)
        if not ev_bits and f.tool_call_ids:
            for tcid in f.tool_call_ids:
                ev_bits.append(f"`{tcid}`")
        if ev_bits:
            lines.append(f"- **Evidence:** {', '.join(ev_bits)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_prompt_envelope(
    pack: SessionContextPack,
    instructions: str,
) -> str:
    """Assemble the default review prompt from pack formatters + instructions."""
    lines = [
        "You are reviewing a coding-agent evaluation TRACE (multi-turn session).",
        "CRITICAL: All evidence for the review is in this prompt (or the single",
        "offloaded prompt file Grok may attach for length). Do NOT open the host",
        "workspace, search the web, spawn subagents, or wait for more context.",
        "If you must open the offloaded prompt file, read it once, then immediately",
        "emit the structured review JSON. Never reply with only a plan like",
        "'reading the prompt' or all_clear=false with findings=[].",
        "",
        "RUNTIME / FAIRNESS: Respect the runtime and constraints blocks below.",
        "MULTI-TURN: Later operator instructions change scope; work requested in a",
        "later turn is not 'unsolicited' vs an earlier setup-only prompt.",
        "OPERATOR NOTES: When present, human evaluators left notes (focus areas,",
        "suspected issues, turn/event links). Use them to prioritize review; still",
        "ground each finding in the timeline. Do not invent notes that are absent.",
        "",
        instructions.strip(),
        "",
        "Cite event_index values that appear as #N in the timeline, operator",
        "instructions, or operator notes. Include tool_call_id when present.",
        "Prefer 1–8 findings.",
        "If nothing material is wrong: all_clear=true and findings=[].",
        "Never set all_clear=false with an empty findings array.",
        "title must be a short single-line summary (no markdown).",
        "",
        "<meta>",
        pack.format_meta(),
        "</meta>",
        "",
        "<runtime_context>",
        pack.format_runtime(),
        "</runtime_context>",
        "",
        "<review_constraints>",
        pack.format_constraints(),
        "</review_constraints>",
        "",
        "<operator_instructions>",
        pack.format_operator_instructions(),
        "</operator_instructions>",
    ]
    notes = pack.format_operator_notes()
    if notes:
        lines.extend(["", "<operator_notes>", notes, "</operator_notes>"])
    prior = pack.format_prior_findings()
    if prior:
        lines.extend(["", "<detector_hints>", prior, "</detector_hints>"])
    lines.extend(
        [
            "",
            "<timeline>",
            pack.format_timeline_digest(),
            "</timeline>",
            "",
            "END OF EVIDENCE. Produce the review now.",
            "Do not mention offloaded prompts or file reads.",
        ]
    )
    return "\n".join(lines)
