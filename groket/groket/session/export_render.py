"""Built-in export renderers (human file dialects).

Profiles select a renderer by id (``ExportSpec.renderer``). Collectors still own
raw units; this module shapes *analysis reports* and other synthesised text.

Built-ins: ``markdown`` (default), ``plain``, ``org``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import JsonObject

# Product-backed dialect ids (profiles may set renderer to one of these).
BUILTIN_RENDERERS: frozenset[str] = frozenset({"markdown", "plain", "org"})
DEFAULT_RENDERER = "markdown"


@dataclass(frozen=True)
class SessionSummaryData:
    """Domain facts for a human session summary (export; not UI chrome)."""

    session_id: str
    title: str = ""
    model: str = ""
    outcome: str = ""
    duration_label: str = ""
    summary_text: str = ""
    event_count: int = 0
    tool_call_count: int = 0
    tool_error_count: int = 0
    turn_count: int = 0
    context_label: str = ""
    task_id: str = ""
    run_id: str = ""
    git_repo: str = ""
    git_branch: str = ""
    created_at: str = ""
    persona_id: str = ""
    # Extra capability / MCP / skills block (already formatted, light markup OK).
    usage_block: str = ""
    fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def normalize_renderer_id(renderer: str | None) -> str:
    """Return a known builtin id, or *renderer* stripped (for future plugins)."""
    rid = (renderer or DEFAULT_RENDERER).strip() or DEFAULT_RENDERER
    return rid


def is_builtin_renderer(renderer: str | None) -> bool:
    """True when *renderer* is a product builtin dialect."""
    return normalize_renderer_id(renderer) in BUILTIN_RENDERERS


def report_file_extension(renderer: str | None) -> str:
    """Filename suffix for one analysis / summary human file."""
    rid = normalize_renderer_id(renderer)
    if rid == "org":
        return ".org"
    if rid == "plain":
        return ".txt"
    return ".md"


def session_summary_body(
    data: SessionSummaryData,
    *,
    renderer: str | None = None,
) -> str:
    """Render *data* as a human session summary in the given dialect."""
    rid = normalize_renderer_id(renderer)
    if rid == "org":
        return _session_summary_org(data)
    if rid == "plain":
        return _session_summary_plain(data)
    return _session_summary_markdown(data)


def _summary_kv_rows(data: SessionSummaryData) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("Session", data.session_id or "—")]
    if data.title and data.title != data.session_id:
        rows.append(("Title", data.title))
    if data.model:
        rows.append(("Model", data.model))
    if data.outcome:
        rows.append(("Outcome", data.outcome))
    if data.duration_label:
        rows.append(("Duration", data.duration_label))
    if data.event_count:
        rows.append(("Events", str(data.event_count)))
    if data.tool_call_count or data.tool_error_count:
        tools = str(data.tool_call_count)
        if data.tool_error_count:
            tools += f" ({data.tool_error_count} errors)"
        rows.append(("Tool calls", tools))
    if data.turn_count:
        rows.append(("Turns", str(data.turn_count)))
    if data.context_label:
        rows.append(("Context", data.context_label))
    if data.task_id:
        rows.append(("Task", data.task_id))
    if data.run_id:
        rows.append(("Run", data.run_id))
    if data.persona_id:
        rows.append(("Persona", data.persona_id))
    if data.git_repo:
        rows.append(("Repo", data.git_repo))
    if data.git_branch:
        rows.append(("Branch", data.git_branch))
    if data.created_at:
        rows.append(("Created", data.created_at))
    for k, v in data.fields:
        if k and v:
            rows.append((k, v))
    return rows


def _session_summary_markdown(data: SessionSummaryData) -> str:
    title = (data.title or data.session_id or "session").strip()
    lines: list[str] = [f"# {title}", ""]
    for key, val in _summary_kv_rows(data):
        lines.append(f"- **{key}:** {val}")
    lines.append("")
    if data.summary_text.strip():
        lines.extend(["## Session summary", "", data.summary_text.strip(), ""])
    if data.usage_block.strip():
        lines.extend(["## Usage", "", data.usage_block.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _session_summary_org(data: SessionSummaryData) -> str:
    title = (data.title or data.session_id or "session").strip()
    lines: list[str] = [
        f"#+TITLE: {title}",
        "#+AUTHOR: groket",
        "",
        "* Meta",
        "",
    ]
    for key, val in _summary_kv_rows(data):
        lines.append(f"- {key}: {val}")
    lines.append("")
    if data.summary_text.strip():
        lines.extend(["* Session summary", "", data.summary_text.strip(), ""])
    if data.usage_block.strip():
        usage = _adapt_markdownish_report_to_org(data.usage_block.strip() + "\n")
        # usage block may already be section-like; nest under Usage
        lines.extend(["* Usage", "", usage.rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _session_summary_plain(data: SessionSummaryData) -> str:
    title = (data.title or data.session_id or "session").strip()
    lines: list[str] = [title, ""]
    for key, val in _summary_kv_rows(data):
        lines.append(f"{key}: {val}")
    lines.append("")
    if data.summary_text.strip():
        lines.extend(["Session summary", data.summary_text.strip(), ""])
    if data.usage_block.strip():
        lines.extend(["Usage", _strip_light_markdown(data.usage_block).rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def analysis_report_from_result(
    result: JsonObject,
    *,
    plugin_stem: str,
    renderer: str | None = None,
) -> str:
    """Build a human analysis report body for *result*.

    Prefers ``artifacts["report"]`` when present (plugin-authored text). For
    ``org`` / ``plain``, a leading markdown title line is lightly adapted when
    the artifact looks like markdown; otherwise the string is used as-is.
    When no artifact, synthesises summary + findings in the target dialect.
    """
    rid = normalize_renderer_id(renderer)
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        report = artifacts.get("report")
        if isinstance(report, str) and report.strip():
            text = report if report.endswith("\n") else report + "\n"
            if rid == "org":
                return _adapt_markdownish_report_to_org(text)
            if rid == "plain":
                return _strip_light_markdown(text)
            return text

    if rid == "org":
        return _org_from_analysis_result(result, plugin_stem=plugin_stem)
    if rid == "plain":
        return _plain_from_analysis_result(result, plugin_stem=plugin_stem)
    return _markdown_from_analysis_result(result, plugin_stem=plugin_stem)


def _markdown_from_analysis_result(result: JsonObject, *, plugin_stem: str) -> str:
    analyzer_id = str(result.get("analyzer_id") or plugin_stem or "analysis").strip()
    lines: list[str] = [f"# Analysis report — {analyzer_id}", ""]
    ok = result.get("ok")
    if ok is False:
        lines.append("**Status:** failed")
        err = str(result.get("error") or "").strip()
        if err:
            lines.append("")
            lines.append(err)
        lines.append("")
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.extend(["## Summary", "", summary, ""])
    findings = result.get("findings")
    if isinstance(findings, list) and findings:
        lines.extend(["## Findings", ""])
        for i, item in enumerate(findings, start=1):
            if not isinstance(item, dict):
                continue
            ftitle = str(item.get("title") or f"Finding {i}").strip()
            sev = str(item.get("severity") or "").strip().upper()
            head = f"### {i}. {ftitle}"
            if sev:
                head += f" ({sev})"
            lines.append(head)
            lines.append("")
            detail = str(item.get("detail") or "").strip()
            if detail:
                lines.append(detail)
                lines.append("")
            category = str(item.get("category") or "").strip()
            if category:
                lines.append(f"- **Category:** {category}")
            ev = item.get("event_indices")
            if isinstance(ev, list) and ev:
                bits = ", ".join(f"#{x}" for x in ev[:20])
                lines.append(f"- **Evidence:** {bits}")
            if category or (isinstance(ev, list) and ev):
                lines.append("")
    elif not summary and ok is not False:
        lines.append("_No findings or report artifact for this analyzer._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _org_from_analysis_result(result: JsonObject, *, plugin_stem: str) -> str:
    analyzer_id = str(result.get("analyzer_id") or plugin_stem or "analysis").strip()
    lines: list[str] = [
        f"#+TITLE: Analysis report — {analyzer_id}",
        "#+AUTHOR: groket",
        "",
    ]
    ok = result.get("ok")
    if ok is False:
        lines.append("* Status")
        lines.append("")
        lines.append("failed")
        err = str(result.get("error") or "").strip()
        if err:
            lines.append("")
            lines.append(err)
        lines.append("")
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.extend(["* Summary", "", summary, ""])
    findings = result.get("findings")
    if isinstance(findings, list) and findings:
        lines.extend(["* Findings", ""])
        for i, item in enumerate(findings, start=1):
            if not isinstance(item, dict):
                continue
            ftitle = str(item.get("title") or f"Finding {i}").strip()
            sev = str(item.get("severity") or "").strip().upper()
            head = f"** {i}. {ftitle}"
            if sev:
                head += f" ({sev})"
            lines.append(head)
            lines.append("")
            detail = str(item.get("detail") or "").strip()
            if detail:
                lines.append(detail)
                lines.append("")
            category = str(item.get("category") or "").strip()
            if category:
                lines.append(f"- Category: {category}")
            ev = item.get("event_indices")
            if isinstance(ev, list) and ev:
                bits = ", ".join(f"#{x}" for x in ev[:20])
                lines.append(f"- Evidence: {bits}")
            if category or (isinstance(ev, list) and ev):
                lines.append("")
    elif not summary and ok is not False:
        lines.append("No findings or report artifact for this analyzer.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _plain_from_analysis_result(result: JsonObject, *, plugin_stem: str) -> str:
    analyzer_id = str(result.get("analyzer_id") or plugin_stem or "analysis").strip()
    lines: list[str] = [f"Analysis report — {analyzer_id}", ""]
    ok = result.get("ok")
    if ok is False:
        lines.append("Status: failed")
        err = str(result.get("error") or "").strip()
        if err:
            lines.append(err)
        lines.append("")
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.extend(["Summary", summary, ""])
    findings = result.get("findings")
    if isinstance(findings, list) and findings:
        lines.append("Findings")
        for i, item in enumerate(findings, start=1):
            if not isinstance(item, dict):
                continue
            ftitle = str(item.get("title") or f"Finding {i}").strip()
            sev = str(item.get("severity") or "").strip().upper()
            head = f"{i}. {ftitle}"
            if sev:
                head += f" ({sev})"
            lines.append(head)
            detail = str(item.get("detail") or "").strip()
            if detail:
                lines.append(detail)
            category = str(item.get("category") or "").strip()
            if category:
                lines.append(f"Category: {category}")
            ev = item.get("event_indices")
            if isinstance(ev, list) and ev:
                bits = ", ".join(f"#{x}" for x in ev[:20])
                lines.append(f"Evidence: {bits}")
            lines.append("")
    elif not summary and ok is not False:
        lines.append("No findings or report artifact for this analyzer.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _adapt_markdownish_report_to_org(text: str) -> str:
    """Best-effort map of common markdown headings to Org for plugin reports."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("# "):
            out.append(f"#+TITLE: {line[2:].strip()}")
        elif line.startswith("### "):
            out.append(f"** {line[4:].strip()}")
        elif line.startswith("## "):
            out.append(f"* {line[3:].strip()}")
        elif line.startswith("#"):
            # Other ATX depths → level-1 Org heading
            stripped = line.lstrip("#").strip()
            out.append(f"* {stripped}" if stripped else line)
        else:
            # Drop bold markers lightly
            out.append(line.replace("**", ""))
    body = "\n".join(out)
    return body if body.endswith("\n") else body + "\n"


def _strip_light_markdown(text: str) -> str:
    """Remove common ATX/bold markers for plain reports."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.lstrip("#").strip() if line.startswith("#") else line
        out.append(s.replace("**", ""))
    body = "\n".join(out)
    return body if body.endswith("\n") else body + "\n"


__all__ = [
    "BUILTIN_RENDERERS",
    "DEFAULT_RENDERER",
    "SessionSummaryData",
    "analysis_report_from_result",
    "is_builtin_renderer",
    "normalize_renderer_id",
    "report_file_extension",
    "session_summary_body",
]
