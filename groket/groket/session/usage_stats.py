"""Per-session tool / skill / MCP usage for the session Summary pane.

Presentation goals:
  - Host/built-in tools (read_file, grep, …) as a flat call table.
  - MCP activity grouped by **server** (``ascii-art``), then by MCP tool method
    (``get_ascii_art``), not a flat list of ``use_tool`` / ``server__method``.
  - Skills: mounted packages; explicit ``read_file`` of ``…/skills/<id>/SKILL.md``
    counts as a skill load (stronger than name-in-transcript “referenced”).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .. import event_types as et
from ..models import (
    JsonObject,
    ToolInput,
    ToolInputBag,
    TraceEvent,
    json_as_list,
    json_as_str,
)
from ..parser import parse_timeline
from ..paths import CONFIG_FILENAME, is_run_dir_name

_MCP_BRIDGE_TOOLS = frozenset({"search_tool", "use_tool", "search_mcp", "call_mcp"})

# Paths like /root/.grok/skills/<skill-id>/SKILL.md (container or host mirrors)
_SKILL_MD_RE = re.compile(
    r"(?:^|/)(?:\.?grok/)?skills/([^/]+)/SKILL\.md\b",
    re.IGNORECASE,
)
_SKILL_DIR_RE = re.compile(
    r"(?:^|/)(?:\.?grok/)?skills/([^/]+)(?:/|$)",
    re.IGNORECASE,
)


@dataclass
class ToolUsageRow:
    name: str
    calls: int = 0
    errors: int = 0
    durations: list[float] = field(default_factory=list)
    category: str = "builtin"  # builtin | mcp_bridge | mcp | other

    @property
    def total_s(self) -> float | None:
        return sum(self.durations) if self.durations else None

    @property
    def avg_s(self) -> float | None:
        if not self.durations:
            return None
        return sum(self.durations) / len(self.durations)


@dataclass
class McpMethodUsage:
    """One MCP tool on a server (method part of server__method)."""

    method: str
    calls: int = 0
    errors: int = 0
    qualified_name: str = ""  # server__method when known


@dataclass
class McpServerUsage:
    """All activity attributed to one MCP server id."""

    server_id: str
    configured: bool = False
    use_tool_calls: int = 0  # use_tool / call_mcp hitting this server
    search_queries: list[str] = field(default_factory=list)
    methods: list[McpMethodUsage] = field(default_factory=list)
    errors: int = 0

    @property
    def total_invocations(self) -> int:
        return self.use_tool_calls + len(self.search_queries)


@dataclass
class SkillUsageRow:
    skill_id: str
    configured: bool = False
    skill_md_reads: int = 0  # read_file on SKILL.md
    name_in_transcript: bool = False  # weak heuristic
    related_mcp_servers: list[str] = field(default_factory=list)

    @property
    def engaged(self) -> bool:
        return self.skill_md_reads > 0 or self.name_in_transcript


@dataclass
class SessionUsageStats:
    """Aggregated tool / skill / MCP usage for one session."""

    tools: list[ToolUsageRow] = field(default_factory=list)
    host_tools: list[ToolUsageRow] = field(default_factory=list)  # non-bridge only
    tools_from_signals: list[str] = field(default_factory=list)
    mcp_configured: list[str] = field(default_factory=list)
    mcp_servers: list[McpServerUsage] = field(default_factory=list)
    mcp_tools_invoked: list[str] = field(default_factory=list)  # flat list, compat
    mcp_bridge_calls: int = 0
    skills: list[SkillUsageRow] = field(default_factory=list)
    skills_configured: list[str] = field(default_factory=list)
    skills_disabled: list[str] = field(default_factory=list)
    skills_referenced: list[str] = field(default_factory=list)  # engaged ids
    persona_id: str = ""
    source_notes: list[str] = field(default_factory=list)

    @property
    def tool_call_total(self) -> int:
        return sum(t.calls for t in self.tools)

    @property
    def host_tool_call_total(self) -> int:
        return sum(t.calls for t in self.host_tools)

    @property
    def tool_error_total(self) -> int:
        return sum(t.errors for t in self.tools)


def _load_json(path: Path) -> JsonObject:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _find_run_parent(session_dir: Path) -> Path | None:
    for anc in [session_dir, *session_dir.parents]:
        if is_run_dir_name(anc.name):
            return anc
        if anc.name == "traces":
            break
    return None


def _load_run_manifest(session_dir: Path) -> JsonObject:
    """Load launch recipe (session, traces volume, or fork parent seed)."""
    from ..runs.run_recipe import load_run_recipe

    return load_run_recipe(session_dir)


def _skills_from_skills_dir(session_dir: Path) -> list[str]:
    parent = _find_run_parent(session_dir)
    if parent is None:
        return []
    skills_dir = parent / "groket-skills"
    if not skills_dir.is_dir():
        return []
    names: list[str] = []
    try:
        for p in sorted(skills_dir.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                names.append(p.name)
    except OSError:
        pass
    return names


def _parse_config_toml_caps(session_dir: Path) -> tuple[list[str], list[str]]:
    mcp: list[str] = []
    skills_disabled: list[str] = []
    candidates: list[Path] = []
    parent = _find_run_parent(session_dir)
    if parent:
        candidates.append(parent / CONFIG_FILENAME)
        candidates.append(parent / "groket-config.toml")
    candidates.append(session_dir / CONFIG_FILENAME)
    for cfg_path in candidates:
        if not cfg_path.is_file():
            continue
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"^\[mcp_servers\.([^\]]+)\]", text, re.MULTILINE):
            sid = m.group(1).strip()
            if sid and sid not in mcp:
                mcp.append(sid)
        dm = re.search(
            r"\[skills\][^\[]*?disabled\s*=\s*\[([^\]]*)\]",
            text,
            re.DOTALL,
        )
        if dm:
            for part in re.findall(r'"([^"]+)"', dm.group(1)):
                if part and part not in skills_disabled:
                    skills_disabled.append(part)
        if mcp or skills_disabled:
            break
    return mcp, skills_disabled


def _split_mcp_qualified(name: str) -> tuple[str, str]:
    """``ascii-art__get_ascii_art`` → (``ascii-art``, ``get_ascii_art``)."""
    s = (name or "").strip()
    if "__" in s:
        server, method = s.split("__", 1)
        return server.strip() or "?", method.strip() or s
    if s.startswith("mcp_"):
        return "mcp", s[4:] or s
    return "", s


@dataclass
class _McpCallAcc:
    """Mutable counters while scanning the timeline for MCP activity."""

    method_counts: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    method_errors: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    method_qualified: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    server_use_calls: Counter[str] = field(default_factory=Counter)
    server_errors: Counter[str] = field(default_factory=Counter)
    server_searches: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    flat_targets: Counter[str] = field(default_factory=Counter)

    def record_method(self, server: str, method: str, *, is_error: bool, qualified: str) -> None:
        """Attribute one MCP method invocation (bridge or direct ``server__method``)."""
        srv = server or "?"
        self.server_use_calls[srv] += 1
        self.method_counts[srv][method] += 1
        if is_error:
            self.method_errors[srv][method] += 1
            self.server_errors[srv] += 1
        qname = qualified or (f"{srv}__{method}" if srv != "?" else method)
        if srv != "?":
            self.method_qualified[srv][method] = qname
        self.flat_targets[qname] += 1

    def record_search(self, server: str, query: str) -> None:
        srv = server if server and server != "?" else "_search"
        self.server_searches[srv].append(query)
        self.flat_targets[f"search:{query[:60]}"] += 1


def _mcp_target_from_input(tool_name: str, raw_input: ToolInput) -> tuple[str, str, str]:
    """Return ``(server_id, method_or_label, kind)`` with kind in use|search|unknown.

    Only for MCP **bridge** tools (``use_tool`` / ``call_mcp`` / ``search_tool``).
    Direct ``server__method`` tool ids use :func:`_split_mcp_qualified` instead.
    """
    ri = ToolInputBag.ensure(raw_input)
    key = (tool_name or "").strip().lower().replace("-", "_")
    if key in ("use_tool", "call_mcp", "call_mcp_tool", "mcp_tool"):
        q = (ri.as_str("tool_name") or ri.as_str("name")).strip()
        if q:
            server, method = _split_mcp_qualified(q)
            if server:
                return server, method, "use"
            return "?", q, "use"
    if key in ("search_tool", "search_mcp"):
        qs = (ri.as_str("query") or ri.as_str("q")).strip()
        if qs:
            # First token is often the server id the agent is searching for.
            first = qs.split()[0].strip(".,;:") if qs.split() else ""
            server = first if first and re.match(r"^[a-z0-9][\w.-]*$", first, re.I) else "?"
            return server, qs[:80], "search"
    return "", "", "unknown"


def _skill_id_from_path(path: str) -> str:
    if not path:
        return ""
    m = _SKILL_MD_RE.search(path.replace("\\", "/"))
    if m:
        return m.group(1).strip()
    m2 = _SKILL_DIR_RE.search(path.replace("\\", "/"))
    if m2:
        return m2.group(1).strip()
    return ""


def _path_from_raw_input(raw_input: ToolInput) -> str:
    bag = ToolInputBag.ensure(raw_input)
    for key in ("target_file", "file_path", "path"):
        val = bag.as_str(key)
        if val:
            return val
    return ""


def _infer_mcp_from_skill_id(skill_id: str, mcp_configured: list[str]) -> list[str]:
    """Best-effort: use-ascii-art-mcp → ascii-art if that server is configured."""
    sid = (skill_id or "").strip().lower()
    if not sid:
        return []
    related: list[str] = []
    for srv in mcp_configured:
        s = srv.lower()
        if s in sid or sid.replace("-", "").find(s.replace("-", "")) >= 0:
            related.append(srv)
            continue
        # use-<server>-mcp / use_<server>_mcp patterns
        for pat in (f"use-{s}", f"use_{s}", f"{s}-mcp", f"{s}_mcp", s):
            if pat in sid:
                related.append(srv)
                break
    # Dedupe preserve order
    out: list[str] = []
    for r in related:
        if r not in out:
            out.append(r)
    return out


def _categorize_tool(name: str) -> str:
    if name in _MCP_BRIDGE_TOOLS:
        return "mcp_bridge"
    if "__" in name or name.startswith("mcp_"):
        return "mcp"
    return "builtin"


def _name_in_transcript(skill_id: str, hay: str) -> bool:
    token = (skill_id or "").strip()
    if not token or len(token) < 3:
        return False
    needles = [token.lower(), f"skill {token.lower()}", f"/{token.lower()}/"]
    return any(n in hay for n in needles)


def collect_session_usage(
    session_dir: Path | str,
    timeline: list[TraceEvent] | None = None,
    *,
    durations: dict[int, float] | None = None,
) -> SessionUsageStats:
    """Build tool/skill/MCP usage for *session_dir* (optionally using an in-memory timeline)."""
    sd = Path(session_dir)
    stats = SessionUsageStats()
    durs = durations or {}

    manifest = _load_run_manifest(sd)
    stats.persona_id = str(manifest.get("persona_id") or "").strip()

    for key in ("run_skills", "skills"):
        for s in json_as_list(manifest.get(key)):
            ss = json_as_str(s).strip()
            if ss and ss not in stats.skills_configured:
                stats.skills_configured.append(ss)
    for key in ("run_skills_disabled", "skills_disabled"):
        for s in json_as_list(manifest.get(key)):
            ss = json_as_str(s).strip()
            if ss and ss not in stats.skills_disabled:
                stats.skills_disabled.append(ss)
    for key in ("run_mcp_servers", "mcp_servers"):
        for s in json_as_list(manifest.get(key)):
            ss = json_as_str(s).strip()
            if ss and ss not in stats.mcp_configured:
                stats.mcp_configured.append(ss)

    for sk in _skills_from_skills_dir(sd):
        if sk not in stats.skills_configured:
            stats.skills_configured.append(sk)
            if "groket-skills" not in " ".join(stats.source_notes):
                stats.source_notes.append("skills from run volume groket-skills/")

    toml_mcp, toml_sk_dis = _parse_config_toml_caps(sd)
    for m in toml_mcp:
        if m not in stats.mcp_configured:
            stats.mcp_configured.append(m)
    for s in toml_sk_dis:
        if s not in stats.skills_disabled:
            stats.skills_disabled.append(s)

    if stats.skills_configured or stats.mcp_configured:
        if (
            manifest.get("run_skills")
            or manifest.get("run_mcp_servers")
            or manifest.get("run_plugins")
            or manifest.get("skills")
            or manifest.get("mcp_servers")
        ):
            stats.source_notes.append("capabilities from run.json")
        elif toml_mcp:
            stats.source_notes.append("MCP from groket-config.toml")

    events = timeline
    if events is None:
        try:
            events = parse_timeline(sd)
        except Exception:
            events = []

    tool_rows: dict[str, ToolUsageRow] = {}
    mcp_acc = _McpCallAcc()
    skill_md_reads: Counter[str] = Counter()

    text_chunks: list[str] = []

    for e in events or []:
        if e.event_type in et.MESSAGE_TYPES and e.content:
            text_chunks.append(e.content)

        if e.event_type != "tool_call" or not e.tool_name:
            continue

        name = e.tool_name
        row = tool_rows.get(name)
        if row is None:
            row = ToolUsageRow(name=name, category=_categorize_tool(name))
            tool_rows[name] = row
        row.calls += 1
        if e.is_error:
            row.errors += 1
        dur = durs.get(e.index)
        if dur is not None:
            row.durations.append(dur)

        ri: ToolInput = (
            e.raw_input
            if isinstance(e.raw_input, ToolInputBag)
            else ToolInputBag(e.raw_input if isinstance(e.raw_input, dict) else {})
        )
        if e.raw_input:
            try:
                text_chunks.append(json.dumps(e.raw_input))
            except (TypeError, ValueError):
                pass

        # Skill SKILL.md reads via read_file (or any tool with a path arg)
        path = _path_from_raw_input(ri)
        if path:
            sk = _skill_id_from_path(path)
            if sk and (name == "read_file" or path.lower().endswith("skill.md")):
                skill_md_reads[sk] += 1

        if name in _MCP_BRIDGE_TOOLS:
            stats.mcp_bridge_calls += 1
            server, method, kind = _mcp_target_from_input(name, ri)
            if kind == "search" and method:
                mcp_acc.record_search(server, method)
            elif kind == "use" and method:
                mcp_acc.record_method(
                    server or "?",
                    method,
                    is_error=e.is_error,
                    qualified=(f"{server}__{method}" if server and server != "?" else method),
                )
        elif "__" in name or name.startswith("mcp_"):
            # Direct MCP tool id in the tool set (not only via use_tool).
            server, method = _split_mcp_qualified(name)
            if server and method:
                mcp_acc.record_method(
                    server,
                    method,
                    is_error=e.is_error,
                    qualified=name if "__" in name else f"{server}__{method}",
                )

    stats.tools = sorted(tool_rows.values(), key=lambda r: (-r.calls, r.name))
    # Host table: built-ins only (exclude MCP bridge *and* direct server__method rows).
    stats.host_tools = [t for t in stats.tools if t.category == "builtin"]
    stats.mcp_tools_invoked = [n for n, _ in mcp_acc.flat_targets.most_common()]

    # Build mcp_servers list (configured first, then discovered from calls)
    all_servers: set[str] = set(stats.mcp_configured)
    all_servers |= set(mcp_acc.method_counts.keys())
    all_servers |= set(mcp_acc.server_searches.keys())
    all_servers |= set(mcp_acc.server_use_calls.keys())
    all_servers.discard("_search")

    server_rows: list[McpServerUsage] = []
    for srv in sorted(all_servers, key=lambda s: (-mcp_acc.server_use_calls.get(s, 0), s)):
        methods = [
            McpMethodUsage(
                method=meth,
                calls=cnt,
                errors=mcp_acc.method_errors[srv].get(meth, 0),
                qualified_name=mcp_acc.method_qualified[srv].get(meth, f"{srv}__{meth}"),
            )
            for meth, cnt in mcp_acc.method_counts[srv].most_common()
        ]
        searches = list(mcp_acc.server_searches.get(srv, []))
        # Orphan searches tagged under _search with first-token guess elsewhere
        if srv == "?":
            continue
        server_rows.append(
            McpServerUsage(
                server_id=srv,
                configured=srv in stats.mcp_configured,
                use_tool_calls=mcp_acc.server_use_calls.get(srv, 0),
                search_queries=searches,
                methods=methods,
                errors=mcp_acc.server_errors.get(srv, 0),
            )
        )
    # Searches without a clear server
    orphan_searches = mcp_acc.server_searches.get("?", []) + mcp_acc.server_searches.get(
        "_search", []
    )
    if orphan_searches and not any(s.server_id == "?" for s in server_rows):
        # Attach to configured servers if only one, else a pseudo bucket
        if len(stats.mcp_configured) == 1:
            only = stats.mcp_configured[0]
            for srv_row in server_rows:
                if srv_row.server_id == only:
                    srv_row.search_queries.extend(orphan_searches)
                    break
            else:
                server_rows.append(
                    McpServerUsage(
                        server_id=only,
                        configured=True,
                        search_queries=list(orphan_searches),
                    )
                )
        elif orphan_searches:
            server_rows.append(
                McpServerUsage(
                    server_id="(search)",
                    configured=False,
                    search_queries=list(orphan_searches),
                )
            )
    # Configured but never used
    for srv in stats.mcp_configured:
        if not any(r.server_id == srv for r in server_rows):
            server_rows.append(McpServerUsage(server_id=srv, configured=True))
    stats.mcp_servers = server_rows

    # signals.json toolsUsed
    sig = _load_json(sd / "signals.json")
    tu = sig.get("toolsUsed") or sig.get("tools_used") or []
    if isinstance(tu, list):
        stats.tools_from_signals = [str(x) for x in tu if x]
        if not stats.tools and stats.tools_from_signals:
            for name in stats.tools_from_signals:
                stats.tools.append(
                    ToolUsageRow(name=name, calls=0, category=_categorize_tool(name))
                )
            stats.host_tools = [t for t in stats.tools if t.category == "builtin"]
            stats.source_notes.append("tool names from signals.json (counts from timeline empty)")

    hay = "\n".join(text_chunks).lower()
    skill_ids: set[str] = set(stats.skills_configured)
    skill_ids |= set(skill_md_reads.keys())
    skill_rows: list[SkillUsageRow] = []
    for sk in sorted(skill_ids):
        configured = sk in stats.skills_configured
        reads = skill_md_reads.get(sk, 0)
        in_text = _name_in_transcript(sk, hay)
        related = _infer_mcp_from_skill_id(sk, stats.mcp_configured)
        # Also link MCP servers that were actually used if skill name overlaps
        for mcp_row in stats.mcp_servers:
            if mcp_row.use_tool_calls or mcp_row.methods:
                extra = _infer_mcp_from_skill_id(sk, [mcp_row.server_id])
                for r in extra:
                    if r not in related:
                        related.append(r)
        skill_rows.append(
            SkillUsageRow(
                skill_id=sk,
                configured=configured,
                skill_md_reads=reads,
                name_in_transcript=in_text and reads == 0,  # only if no strong signal
                related_mcp_servers=related,
            )
        )
    # Sort: engaged first, then configured
    skill_rows.sort(key=lambda r: (-(r.skill_md_reads), -int(r.configured), r.skill_id))
    stats.skills = skill_rows
    stats.skills_referenced = [r.skill_id for r in skill_rows if r.engaged or r.skill_md_reads]

    return stats


def _fmt_host_tools_md(usage: SessionUsageStats) -> list[str]:
    lines = ["", "## Host tools", ""]
    lines.append(
        "_Built-in Grok tools only (`read_file`, `grep`, …). "
        "MCP goes through `search_tool` / `use_tool` and is summarized under **MCP**._"
    )
    if usage.host_tools:
        for row in usage.host_tools:
            err = f", {row.errors} err" if row.errors else ""
            if row.calls:
                lines.append(f"- `{row.name}`: {row.calls}×{err}")
            else:
                lines.append(f"- `{row.name}`: (signals only)")
        lines.append(f"- **Host tool calls:** {usage.host_tool_call_total}")
    elif usage.tools_from_signals:
        lines.append("From `signals.json` `toolsUsed` (no timeline counts):")
        for n in usage.tools_from_signals:
            if n not in _MCP_BRIDGE_TOOLS:
                lines.append(f"- `{n}`")
    else:
        lines.append("_No host tool calls (or only MCP bridge tools)._")
    if usage.mcp_bridge_calls:
        lines.append(
            f"- **MCP bridge (not counted above):** {usage.mcp_bridge_calls}× "
            f"`search_tool`/`use_tool` — see MCP section"
        )
    return lines


def _fmt_mcp_md(usage: SessionUsageStats) -> list[str]:
    lines = ["", "## MCP (by server)", ""]
    if not usage.mcp_servers and not usage.mcp_configured:
        lines.append("_No MCP servers configured or invoked._")
        return lines

    for srv in usage.mcp_servers:
        cfg = "configured" if srv.configured else "discovered in calls"
        err_bit = f", {srv.errors} err" if srv.errors else ""
        lines.append(f"### `{srv.server_id}` ({cfg})")
        if not srv.methods and not srv.search_queries and not srv.use_tool_calls:
            lines.append("- _enabled at launch; no `use_tool` / `search_tool` hits_")
            continue
        if srv.use_tool_calls:
            lines.append(f"- **use_tool:** {srv.use_tool_calls}×{err_bit}")
        if srv.methods:
            lines.append("- **Tools / methods:**")
            for m in srv.methods:
                e = f" ({m.errors} err)" if m.errors else ""
                lines.append(f"  - `{m.method}`: {m.calls}×{e}")
        if srv.search_queries:
            lines.append("- **search_tool queries:**")
            for q in srv.search_queries[:8]:
                lines.append(f"  - `{q}`")
            if len(srv.search_queries) > 8:
                lines.append(f"  - … +{len(srv.search_queries) - 8} more")
        lines.append("")
    return lines


def _fmt_skills_md(usage: SessionUsageStats) -> list[str]:
    lines = ["", "## Skills", ""]
    lines.append(
        "_Skills are instruction packages (`SKILL.md`), not tool names. "
        "**Loaded** = agent `read_file`'d the skill file; **MCP** = related servers "
        "inferred/used after the skill._"
    )
    if not usage.skills and not usage.skills_configured:
        lines.append("_No skills mounted or loaded._")
        return lines

    for sk in usage.skills:
        bits: list[str] = []
        if sk.configured:
            bits.append("mounted")
        if sk.skill_md_reads:
            bits.append(f"loaded {sk.skill_md_reads}× (read SKILL.md)")
        elif sk.name_in_transcript:
            bits.append("name in transcript only (weak)")
        if not bits:
            bits.append("seen in trace")
        lines.append(f"- **`{sk.skill_id}`** — {', '.join(bits)}")
        if sk.related_mcp_servers:
            # Show MCP rollup under skill when linked
            for srv_id in sk.related_mcp_servers:
                srv = next((s for s in usage.mcp_servers if s.server_id == srv_id), None)
                if srv and (srv.methods or srv.use_tool_calls):
                    meths = ", ".join(f"`{m.method}`×{m.calls}" for m in srv.methods[:8])
                    more = f" +{len(srv.methods) - 8}" if len(srv.methods) > 8 else ""
                    lines.append(
                        f"  - via MCP `{srv_id}`: {meths}{more} ({srv.use_tool_calls} use_tool)"
                    )
                else:
                    lines.append(f"  - via MCP `{srv_id}` (configured; little/no use_tool)")
    if usage.skills_disabled:
        lines.append("**Disabled at launch:**")
        for s in usage.skills_disabled:
            lines.append(f"- `{s}`")
    return lines


def format_usage_plain(usage: SessionUsageStats) -> str:
    """Plain sections for the calm Summary tab (theme-native, no Markdown)."""
    lines: list[str] = []
    rule = "─" * 52

    def _sec(title: str) -> None:
        lines.extend(["", rule, f" {title} ", rule, ""])

    if usage.persona_id:
        lines += ["", f"PERSONA      {usage.persona_id}"]

    _sec("HOST TOOLS")
    if usage.host_tools:
        for row in usage.host_tools:
            err = f"  {row.errors} err" if row.errors else ""
            lines.append(f"  {row.name:<24} {row.calls}x{err}")
        lines.append(f"  total                  {usage.host_tool_call_total}")
    else:
        lines.append("  (none — or only MCP bridge tools)")
    if usage.mcp_bridge_calls:
        lines.append(f"  mcp bridge calls        {usage.mcp_bridge_calls}")

    _sec("MCP")
    if not usage.mcp_servers and not usage.mcp_configured:
        lines.append("  (none configured or invoked)")
    else:
        for srv in usage.mcp_servers:
            cfg = "configured" if srv.configured else "from calls"
            lines.append(f"  {srv.server_id}  ({cfg})")
            if not srv.methods and not srv.search_queries and not srv.use_tool_calls:
                lines.append("    enabled; no use_tool / search_tool hits")
                continue
            if srv.use_tool_calls:
                err = f", {srv.errors} err" if srv.errors else ""
                lines.append(f"    use_tool  {srv.use_tool_calls}x{err}")
            for m in srv.methods:
                e = f" ({m.errors} err)" if m.errors else ""
                lines.append(f"    .{m.method}  {m.calls}x{e}")
            for q in srv.search_queries[:6]:
                lines.append(f"    search   {q}")
            if len(srv.search_queries) > 6:
                lines.append(f"    … +{len(srv.search_queries) - 6} queries")

    _sec("SKILLS")
    if not usage.skills and not usage.skills_configured:
        lines.append("  (none mounted or loaded)")
    else:
        for sk in usage.skills:
            bits: list[str] = []
            if sk.configured:
                bits.append("mounted")
            if sk.skill_md_reads:
                bits.append(f"loaded {sk.skill_md_reads}x")
            elif sk.name_in_transcript:
                bits.append("name in transcript")
            if not bits:
                bits.append("seen")
            lines.append(f"  {sk.skill_id}  — {', '.join(bits)}")
            for srv_id in sk.related_mcp_servers:
                lines.append(f"    mcp {srv_id}")
        if usage.skills_disabled:
            lines.append("  disabled at launch:")
            for s in usage.skills_disabled:
                lines.append(f"    {s}")

    if usage.source_notes:
        lines += ["", f"  sources: {', '.join(usage.source_notes)}"]
    return "\n".join(lines)


def format_usage_markdown(usage: SessionUsageStats) -> str:
    """Markdown sections for the Summary tab."""
    lines: list[str] = []
    if usage.persona_id:
        lines += ["", f"- **Persona:** `{usage.persona_id}`"]
    lines += _fmt_host_tools_md(usage)
    lines += _fmt_mcp_md(usage)
    lines += _fmt_skills_md(usage)
    if usage.source_notes:
        lines += ["", f"_Sources: {'; '.join(usage.source_notes)}_"]
    return "\n".join(lines)


def format_usage_stats_text(usage: SessionUsageStats, *, fmt_dur=None) -> str:
    """Plain-text usage block (capabilities + MCP/skills; host tools stay in the table)."""
    _ = fmt_dur
    out: list[str] = []

    if usage.persona_id:
        out.append(f"  Persona:   {usage.persona_id}")

    # Compact note under the flat tool table
    if usage.mcp_bridge_calls:
        out.append(
            f"\n[dim]Note: {usage.mcp_bridge_calls} search_tool/use_tool call(s) are MCP bridge — "
            f"detail by server below (not separate host tools).[/dim]"
        )

    # MCP by server (main improvement)
    out.append("\n[bold]MCP by server:[/bold]")
    if not usage.mcp_servers:
        out.append("  (none configured or invoked)")
    for srv in usage.mcp_servers:
        cfg = "[green]on[/green]" if srv.configured else "[dim]off@launch[/dim]"
        err = f" [red]{srv.errors}err[/red]" if srv.errors else ""
        out.append(f"  [bold cyan]{srv.server_id}[/bold cyan]  cfg={cfg}{err}")
        if not srv.methods and not srv.search_queries and srv.use_tool_calls == 0:
            out.append("    [dim](no invocations)[/dim]")
            continue
        if srv.use_tool_calls:
            out.append(f"    use_tool total: {srv.use_tool_calls}")
        if srv.methods:
            out.append(f"    {'method':<28} {'calls':>6} {'errs':>5}")
            out.append(f"    {'─' * 28} {'─' * 6} {'─' * 5}")
            for m in srv.methods:
                ev = str(m.errors) if m.errors else "-"
                ec = f"[red]{ev:>5}[/red]" if m.errors else f"{ev:>5}"
                out.append(f"    {m.method:<28} {m.calls:>6} {ec}")
        if srv.search_queries:
            out.append("    search_tool:")
            for q in srv.search_queries[:6]:
                out.append(f"      · {q[:70]}")
            if len(srv.search_queries) > 6:
                out.append(f"      … +{len(srv.search_queries) - 6}")

    # Skills with load + MCP link
    out.append("\n[bold]Skills:[/bold]")
    out.append("  [dim]packages at launch; load = read_file(…/skills/<id>/SKILL.md)[/dim]")
    if not usage.skills:
        out.append("  (none)")
    for sk in usage.skills:
        status_parts: list[str] = []
        if sk.configured:
            status_parts.append("mounted")
        if sk.skill_md_reads:
            status_parts.append(f"[green]loaded×{sk.skill_md_reads}[/green]")
        elif sk.name_in_transcript:
            status_parts.append("[yellow]name only[/yellow]")
        else:
            status_parts.append("[dim]idle[/dim]")
        out.append(f"  • [bold]{sk.skill_id}[/bold]  ({', '.join(status_parts)})")
        for srv_id in sk.related_mcp_servers:
            mcp_srv = next((s for s in usage.mcp_servers if s.server_id == srv_id), None)
            if mcp_srv is not None and mcp_srv.methods:
                summary = ", ".join(f"{m.method}×{m.calls}" for m in mcp_srv.methods[:6])
                extra = f" +{len(mcp_srv.methods) - 6}" if len(mcp_srv.methods) > 6 else ""
                out.append(f"      → MCP [cyan]{srv_id}[/cyan]: {summary}{extra}")
            elif srv_id:
                out.append(f"      → MCP [cyan]{srv_id}[/cyan] [dim](little/no use)[/dim]")

    if usage.skills_disabled:
        out.append("  Disabled: " + ", ".join(usage.skills_disabled))

    return "\n".join(out) + "\n"


def tool_category_label(category: str) -> str:
    return {
        "builtin": "",
        "mcp_bridge": "mcp-bridge",
        "mcp": "mcp",
        "other": "",
    }.get(category, category)
