"""Merge persona capabilities with per-run extras (union; run adds without mutating persona)."""

from __future__ import annotations

from ..models import JsonObject, as_json_object


def _uniq_str_list(*parts: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for item in part or []:
            s = (item or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _merge_mcp_definitions(
    base: list[JsonObject] | None,
    extra: list[JsonObject] | None,
) -> list[JsonObject]:
    """Later definitions with the same id win (run extras override persona for that id)."""
    by_id: dict[str, JsonObject] = {}
    order: list[str] = []
    for src in (base or [], extra or []):
        for d in src:
            if not isinstance(d, dict):
                continue
            sid = str(d.get("id") or "").strip()
            if not sid:
                continue
            if sid not in by_id:
                order.append(sid)
            by_id[sid] = dict(d)
    return [by_id[sid] for sid in order if sid in by_id]


def merge_capabilities(
    *,
    persona_mcp_servers: list[str] | None = None,
    persona_mcp_definitions: list[JsonObject] | None = None,
    persona_skills: list[str] | None = None,
    persona_skills_disabled: list[str] | None = None,
    persona_plugins: list[str] | None = None,
    run_mcp_servers: list[str] | None = None,
    run_mcp_definitions: list[JsonObject] | None = None,
    run_skills: list[str] | None = None,
    run_skills_disabled: list[str] | None = None,
    run_plugins: list[str] | None = None,
) -> JsonObject:
    """Persona is the base profile; run_* lists are additive extras for this launch only."""
    mcp_servers = _uniq_str_list(persona_mcp_servers, run_mcp_servers)
    skills = _uniq_str_list(persona_skills, run_skills)
    skills_disabled = _uniq_str_list(persona_skills_disabled, run_skills_disabled)
    plugins = _uniq_str_list(persona_plugins, run_plugins)
    run_on = {s.strip() for s in (run_skills or []) if (s or "").strip()}
    if run_on:
        skills_disabled = [s for s in skills_disabled if s not in run_on]
    mcp_definitions = _merge_mcp_definitions(persona_mcp_definitions, run_mcp_definitions)
    return as_json_object(
        {
            "mcp_servers": mcp_servers,
            "mcp_definitions": mcp_definitions,
            "skills": skills,
            "skills_disabled": skills_disabled,
            "plugins": plugins,
        }
    )
