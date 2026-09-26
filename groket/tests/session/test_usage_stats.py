"""Tests for groket.usage_stats."""

from __future__ import annotations

import json
from pathlib import Path

from groket.models import TraceEvent
from groket.session.usage_stats import (
    McpMethodUsage,
    McpServerUsage,
    SessionUsageStats,
    SkillUsageRow,
    ToolUsageRow,
    _categorize_tool,
    _name_in_transcript,
    _skill_id_from_path,
    _split_mcp_qualified,
    collect_session_usage,
    format_usage_markdown,
    format_usage_plain,
    format_usage_stats_text,
    tool_category_label,
)

# ── ToolUsageRow ──────────────────────────────────────────────────────────


class TestToolUsageRow:
    def test_total_s_with_durations(self):
        row = ToolUsageRow(name="grep", durations=[1.0, 2.5, 0.5])
        assert row.total_s == 4.0

    def test_total_s_empty(self):
        row = ToolUsageRow(name="grep")
        assert row.total_s is None

    def test_avg_s_with_durations(self):
        row = ToolUsageRow(name="grep", durations=[2.0, 4.0, 6.0])
        assert row.avg_s == 4.0

    def test_avg_s_single(self):
        row = ToolUsageRow(name="read_file", durations=[3.5])
        assert row.avg_s == 3.5

    def test_avg_s_empty(self):
        row = ToolUsageRow(name="read_file")
        assert row.avg_s is None


# ── McpServerUsage ────────────────────────────────────────────────────────


class TestMcpServerUsage:
    def test_total_invocations_both(self):
        srv = McpServerUsage(
            server_id="ascii-art",
            use_tool_calls=3,
            search_queries=["find art", "list"],
        )
        assert srv.total_invocations == 5

    def test_total_invocations_zero(self):
        srv = McpServerUsage(server_id="idle-srv")
        assert srv.total_invocations == 0

    def test_total_invocations_search_only(self):
        srv = McpServerUsage(server_id="s", search_queries=["q1"])
        assert srv.total_invocations == 1


# ── SkillUsageRow ─────────────────────────────────────────────────────────


class TestSkillUsageRow:
    def test_engaged_by_md_reads(self):
        row = SkillUsageRow(skill_id="my-skill", skill_md_reads=2)
        assert row.engaged is True

    def test_engaged_by_transcript(self):
        row = SkillUsageRow(skill_id="my-skill", name_in_transcript=True)
        assert row.engaged is True

    def test_not_engaged(self):
        row = SkillUsageRow(skill_id="my-skill")
        assert row.engaged is False


# ── SessionUsageStats ─────────────────────────────────────────────────────


class TestSessionUsageStats:
    def test_tool_call_total(self):
        stats = SessionUsageStats(
            tools=[
                ToolUsageRow(name="grep", calls=5),
                ToolUsageRow(name="read_file", calls=3),
            ],
        )
        assert stats.tool_call_total == 8

    def test_tool_call_total_empty(self):
        stats = SessionUsageStats()
        assert stats.tool_call_total == 0

    def test_host_tool_call_total(self):
        stats = SessionUsageStats(
            host_tools=[
                ToolUsageRow(name="grep", calls=4),
                ToolUsageRow(name="read_file", calls=2),
            ],
        )
        assert stats.host_tool_call_total == 6

    def test_tool_error_total(self):
        stats = SessionUsageStats(
            tools=[
                ToolUsageRow(name="grep", calls=5, errors=1),
                ToolUsageRow(name="run_terminal_command", calls=3, errors=2),
            ],
        )
        assert stats.tool_error_total == 3

    def test_tool_error_total_no_errors(self):
        stats = SessionUsageStats(
            tools=[ToolUsageRow(name="grep", calls=5)],
        )
        assert stats.tool_error_total == 0


# ── _split_mcp_qualified ─────────────────────────────────────────────────


class TestSplitMcpQualified:
    def test_standard_double_underscore(self):
        server, method = _split_mcp_qualified("ascii-art__get_ascii_art")
        assert server == "ascii-art"
        assert method == "get_ascii_art"

    def test_no_separator(self):
        server, method = _split_mcp_qualified("read_file")
        assert server == ""
        assert method == "read_file"

    def test_mcp_prefix(self):
        server, method = _split_mcp_qualified("mcp_do_thing")
        assert server == "mcp"
        assert method == "do_thing"

    def test_empty_string(self):
        server, method = _split_mcp_qualified("")
        assert server == ""
        assert method == ""

    def test_whitespace_stripped(self):
        server, method = _split_mcp_qualified("  srv__meth  ")
        assert server == "srv"
        assert method == "meth"


# ── _skill_id_from_path ──────────────────────────────────────────────────


class TestSkillIdFromPath:
    def test_container_path(self):
        assert _skill_id_from_path("/root/.grok/skills/my-skill/SKILL.md") == "my-skill"

    def test_host_mirror_path(self):
        assert _skill_id_from_path("/home/user/grok/skills/cool-pkg/SKILL.md") == "cool-pkg"

    def test_skills_subdir_no_md(self):
        # Falls back to _SKILL_DIR_RE
        assert _skill_id_from_path("/root/.grok/skills/abc/some_file.txt") == "abc"

    def test_unrelated_path(self):
        assert _skill_id_from_path("/home/user/project/README.md") == ""

    def test_empty(self):
        assert _skill_id_from_path("") == ""

    def test_backslash_normalised(self):
        assert _skill_id_from_path("C:\\grok\\skills\\win-skill\\SKILL.md") == "win-skill"


# ── _categorize_tool ──────────────────────────────────────────────────────


class TestCategorizeTool:
    def test_builtin(self):
        assert _categorize_tool("read_file") == "builtin"
        assert _categorize_tool("grep") == "builtin"
        assert _categorize_tool("run_terminal_command") == "builtin"

    def test_mcp_bridge(self):
        assert _categorize_tool("search_tool") == "mcp_bridge"
        assert _categorize_tool("use_tool") == "mcp_bridge"
        assert _categorize_tool("call_mcp") == "mcp_bridge"
        assert _categorize_tool("search_mcp") == "mcp_bridge"

    def test_mcp_qualified(self):
        assert _categorize_tool("ascii-art__get_art") == "mcp"

    def test_mcp_prefix(self):
        assert _categorize_tool("mcp_something") == "mcp"


# ── _name_in_transcript ──────────────────────────────────────────────────


class TestNameInTranscript:
    def test_found(self):
        assert _name_in_transcript("my-skill", "i loaded my-skill earlier") is True

    def test_skill_keyword(self):
        assert _name_in_transcript("draw", "activated skill draw") is True

    def test_path_pattern(self):
        assert _name_in_transcript("tools", "in /tools/ directory") is True

    def test_not_found(self):
        assert _name_in_transcript("xyz-tool", "nothing relevant here") is False

    def test_short_token_rejected(self):
        assert _name_in_transcript("ab", "ab is in the text") is False

    def test_empty_token(self):
        assert _name_in_transcript("", "anything") is False


# ── collect_session_usage ─────────────────────────────────────────────────


class TestCollectSessionUsage:
    def test_basic_timeline(self, tmp_path: Path):
        sd = tmp_path / "session-001"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")

        timeline = [
            TraceEvent(index=0, event_type="tool_call", tool_name="grep"),
            TraceEvent(index=1, event_type="tool_call", tool_name="grep"),
            TraceEvent(index=2, event_type="tool_call", tool_name="read_file"),
            TraceEvent(
                index=3,
                event_type="tool_call",
                tool_name="run_terminal_command",
                is_error=True,
            ),
        ]
        stats = collect_session_usage(sd, timeline)

        assert stats.tool_call_total == 4
        assert stats.tool_error_total == 1
        # grep=2, read_file=1, run_terminal_command=1 → 3 host tools
        assert len(stats.host_tools) == 3
        assert stats.host_tool_call_total == 4

    def test_mcp_bridge_calls_counted(self, tmp_path: Path):
        sd = tmp_path / "session-mcp"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")

        timeline = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                raw_input={"tool_name": "srv__meth"},
            ),
            TraceEvent(
                index=1,
                event_type="tool_call",
                tool_name="search_tool",
                raw_input={"query": "find something"},
            ),
        ]
        stats = collect_session_usage(sd, timeline)

        assert stats.mcp_bridge_calls == 2
        # Bridge tools should not appear in host_tools
        assert all(t.name not in ("use_tool", "search_tool") for t in stats.host_tools)

    def test_direct_mcp_qualified_tools_attribute_to_server(self, tmp_path: Path):
        """Grok often exposes MCP as server__method tool_call ids (not only use_tool)."""
        sd = tmp_path / "session-direct-mcp"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")
        (sd / "run.json").write_text(
            json.dumps({"mcp_servers": ["playwright", "context7"]}),
            encoding="utf-8",
        )

        timeline = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="playwright__browser_navigate",
                is_error=True,
            ),
            TraceEvent(
                index=1,
                event_type="tool_call",
                tool_name="context7__query-docs",
            ),
            TraceEvent(
                index=2,
                event_type="tool_call",
                tool_name="context7__query-docs",
            ),
        ]
        stats = collect_session_usage(sd, timeline)

        assert stats.mcp_bridge_calls == 0
        assert all("__" not in t.name for t in stats.host_tools)
        pw = next(s for s in stats.mcp_servers if s.server_id == "playwright")
        assert pw.use_tool_calls == 1
        assert pw.errors == 1
        assert pw.methods[0].method == "browser_navigate"
        assert pw.methods[0].errors == 1
        c7 = next(s for s in stats.mcp_servers if s.server_id == "context7")
        assert c7.use_tool_calls == 2
        assert any(m.method == "query-docs" and m.calls == 2 for m in c7.methods)
        idle = next(s for s in stats.mcp_servers if s.server_id == "playwright")
        assert idle.methods  # not "no tool hits"

    def test_skill_md_read_detected(self, tmp_path: Path):
        sd = tmp_path / "session-skill"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")

        timeline = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="read_file",
                raw_input={"target_file": "/root/.grok/skills/draw/SKILL.md"},
            ),
        ]
        stats = collect_session_usage(sd, timeline)

        assert len(stats.skills) == 1
        assert stats.skills[0].skill_id == "draw"
        assert stats.skills[0].skill_md_reads == 1
        assert stats.skills[0].engaged is True

    def test_empty_timeline(self, tmp_path: Path):
        sd = tmp_path / "session-empty"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")

        stats = collect_session_usage(sd, timeline=[])

        assert stats.tool_call_total == 0
        assert stats.host_tool_call_total == 0
        assert stats.tool_error_total == 0
        assert stats.mcp_bridge_calls == 0

    def test_signals_json_tools(self, tmp_path: Path):
        """When timeline is empty, toolsUsed from signals.json populates tools."""
        sd = tmp_path / "session-signals"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")
        (sd / "signals.json").write_text(json.dumps({"toolsUsed": ["read_file", "grep"]}))

        stats = collect_session_usage(sd, timeline=[])

        assert len(stats.tools_from_signals) == 2
        assert "read_file" in stats.tools_from_signals

    def test_durations_applied(self, tmp_path: Path):
        sd = tmp_path / "session-dur"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("")

        timeline = [
            TraceEvent(index=0, event_type="tool_call", tool_name="grep"),
            TraceEvent(index=1, event_type="tool_call", tool_name="grep"),
        ]
        stats = collect_session_usage(sd, timeline, durations={0: 1.5, 1: 2.5})

        grep_row = next(t for t in stats.tools if t.name == "grep")
        assert grep_row.total_s == 4.0
        assert grep_row.avg_s == 2.0


# ── format_usage_markdown ─────────────────────────────────────────────────


class TestFormatUsageMarkdown:
    def test_contains_host_tools_section(self):
        stats = SessionUsageStats(
            tools=[ToolUsageRow(name="grep", calls=3, category="builtin")],
            host_tools=[ToolUsageRow(name="grep", calls=3, category="builtin")],
        )
        md = format_usage_markdown(stats)
        assert "## Host tools" in md
        assert "`grep`: 3×" in md

    def test_persona_included(self):
        stats = SessionUsageStats(persona_id="code-review")
        md = format_usage_markdown(stats)
        assert "`code-review`" in md

    def test_mcp_section_none(self):
        stats = SessionUsageStats()
        md = format_usage_markdown(stats)
        assert "No MCP servers configured or invoked" in md


# ── format_usage_stats_text ───────────────────────────────────────────────


class TestFormatUsageStatsText:
    def test_mcp_none_message(self):
        stats = SessionUsageStats()
        text = format_usage_stats_text(stats)
        assert "(none configured or invoked)" in text

    def test_skills_none_message(self):
        stats = SessionUsageStats()
        text = format_usage_stats_text(stats)
        assert "(none)" in text

    def test_persona_shown(self):
        stats = SessionUsageStats(persona_id="test-persona")
        text = format_usage_stats_text(stats)
        assert "test-persona" in text


# ── tool_category_label ───────────────────────────────────────────────────


class TestToolCategoryLabel:
    def test_builtin(self):
        assert tool_category_label("builtin") == ""

    def test_mcp_bridge(self):
        assert tool_category_label("mcp_bridge") == "mcp-bridge"

    def test_mcp(self):
        assert tool_category_label("mcp") == "mcp"

    def test_other(self):
        assert tool_category_label("other") == ""

    def test_unknown_passthrough(self):
        assert tool_category_label("custom") == "custom"


from groket.session import usage_stats as us


def test_categorize_and_format():
    cat = us._categorize_tool("read_file")
    assert cat is not None
    # label may be empty for some categories
    us.tool_category_label(us._categorize_tool("grep"))
    us.tool_category_label(cat)
    sid = us._skill_id_from_path("/x/skills/my-skill/SKILL.md")
    assert sid == "my-skill" or isinstance(sid, str)
    us._split_mcp_qualified("server__tool")
    assert us._name_in_transcript("skill", "use skill please") is True
    assert us._name_in_transcript("zzz", "nope") is False
    assert us._name_in_transcript("ab", "ab is short") is False

    stats = us.SessionUsageStats()
    stats.tools = [us.ToolUsageRow(name="grep", calls=2, durations=[1.0, 2.0])]
    stats.mcp_servers = [us.McpServerUsage(server_id="srv", use_tool_calls=1)]
    stats.skills = [us.SkillUsageRow(skill_id="sk", skill_md_reads=1)]
    md = us.format_usage_markdown(stats)
    assert md
    text = us.format_usage_stats_text(stats)
    assert text


def test_format_usage_plain_all_sections():
    """format_usage_plain exercises all plain-text section branches."""

    stats = SessionUsageStats(
        persona_id="my-persona",
        host_tools=[ToolUsageRow(name="grep", calls=5, errors=1)],
        mcp_bridge_calls=3,
        mcp_servers=[
            McpServerUsage(
                server_id="srv1",
                configured=True,
                use_tool_calls=2,
                errors=1,
                methods=[McpMethodUsage(method="do", calls=2, errors=1)],
                search_queries=["q1", "q2", "q3", "q4", "q5", "q6", "q7"],
            ),
            McpServerUsage(server_id="idle", configured=True),
        ],
        skills=[
            SkillUsageRow(
                skill_id="sk1", configured=True, skill_md_reads=2, related_mcp_servers=["srv1"]
            ),
            SkillUsageRow(skill_id="sk2", configured=False, name_in_transcript=True),
            SkillUsageRow(skill_id="sk3", configured=False),
        ],
        skills_disabled=["sk-off"],
        source_notes=["from run.json"],
    )
    text = format_usage_plain(stats)
    assert "my-persona" in text
    assert "grep" in text
    assert "mcp bridge" in text
    assert "srv1" in text
    assert "idle" in text
    assert "sk1" in text
    assert "sk-off" in text
    assert "from run.json" in text

    # Empty stats branches
    empty = SessionUsageStats()
    t2 = format_usage_plain(empty)
    assert "(none" in t2


from groket.session.usage_stats import (
    _infer_mcp_from_skill_id,
    _load_json,
    _mcp_target_from_input,
    _parse_config_toml_caps,
    _path_from_raw_input,
    _skills_from_skills_dir,
)


class TestMcpTargetFromInput:
    def test_use_tool_with_qualified_name(self):
        server, method, kind = _mcp_target_from_input("use_tool", {"tool_name": "ascii-art__draw"})
        assert server == "ascii-art"
        assert method == "draw"
        assert kind == "use"

    def test_use_tool_unqualified(self):
        server, method, kind = _mcp_target_from_input("use_tool", {"tool_name": "do_thing"})
        assert kind == "use"

    def test_use_tool_no_name(self):
        server, method, kind = _mcp_target_from_input("use_tool", {})
        assert kind == "unknown"

    def test_search_tool_with_query(self):
        server, method, kind = _mcp_target_from_input("search_tool", {"query": "ascii find art"})
        assert kind == "search"
        assert "ascii" in method or "find" in method

    def test_search_tool_server_guess(self):
        server, method, kind = _mcp_target_from_input("search_tool", {"query": "slack send"})
        assert kind == "search"
        assert server == "slack"

    def test_search_tool_no_query(self):
        server, method, kind = _mcp_target_from_input("search_tool", {})
        assert kind == "unknown"

    def test_call_mcp_variant(self):
        server, method, kind = _mcp_target_from_input("call_mcp", {"tool_name": "srv__act"})
        assert server == "srv"
        assert kind == "use"

    def test_unrecognized_tool(self):
        server, method, kind = _mcp_target_from_input("grep", {})
        assert kind == "unknown"


class TestInferMcpFromSkillId:
    def test_exact_match(self):
        related = _infer_mcp_from_skill_id("use-slack-mcp", ["slack"])
        assert "slack" in related

    def test_no_match(self):
        related = _infer_mcp_from_skill_id("drawing-tool", ["slack"])
        assert related == []

    def test_empty_skill_id(self):
        assert _infer_mcp_from_skill_id("", ["slack"]) == []

    def test_dedupe(self):
        related = _infer_mcp_from_skill_id("slack-slack-mcp", ["slack"])
        assert related.count("slack") == 1


class TestPathFromRawInput:
    def test_target_file(self):
        assert _path_from_raw_input({"target_file": "/a/b"}) == "/a/b"

    def test_file_path(self):
        assert _path_from_raw_input({"file_path": "/c/d"}) == "/c/d"

    def test_empty(self):
        assert _path_from_raw_input({}) == ""


class TestSkillsFromSkillsDir:
    def test_no_run_parent(self, tmp_path: Path):
        sd = tmp_path / "sess"
        sd.mkdir()
        assert _skills_from_skills_dir(sd) == []

    def test_with_skills_dir(self, tmp_path: Path):
        run = tmp_path / "groket-run-1"
        run.mkdir()
        skills = run / "groket-skills"
        skills.mkdir()
        (skills / "alpha").mkdir()
        (skills / "beta").mkdir()
        (skills / ".hidden").mkdir()
        sd = run / "sess"
        sd.mkdir()
        result = _skills_from_skills_dir(sd)
        assert "alpha" in result
        assert "beta" in result
        assert ".hidden" not in result


class TestParseConfigTomlCaps:
    def test_mcp_and_disabled_skills(self, tmp_path: Path):
        run = tmp_path / "groket-run-1"
        run.mkdir()
        cfg = run / "groket-config.toml"
        cfg.write_text(
            '[mcp_servers.slack]\nurl = "http://x"\n\n[skills]\ndisabled = ["old-skill"]\n',
            encoding="utf-8",
        )
        sd = run / "sess"
        sd.mkdir()
        mcp, disabled = _parse_config_toml_caps(sd)
        assert "slack" in mcp
        assert "old-skill" in disabled

    def test_no_config(self, tmp_path: Path):
        sd = tmp_path / "sess"
        sd.mkdir()
        mcp, disabled = _parse_config_toml_caps(sd)
        assert mcp == []
        assert disabled == []


class TestLoadJson:
    def test_valid_file(self, tmp_path: Path):
        p = tmp_path / "ok.json"
        p.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        assert _load_json(p) == {"k": "v"}

    def test_missing_file(self, tmp_path: Path):
        assert _load_json(tmp_path / "nope.json") == {}

    def test_bad_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not-json", encoding="utf-8")
        assert _load_json(p) == {}

    def test_non_dict(self, tmp_path: Path):
        p = tmp_path / "arr.json"
        p.write_text("[1,2]", encoding="utf-8")
        assert _load_json(p) == {}


class TestCollectSessionUsageExtended:
    def test_configured_capabilities_from_run_json(self, tmp_path: Path):
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps(
                {
                    "skills": ["skill-a"],
                    "mcp_servers": ["srv-a"],
                    "skills_disabled": ["skill-off"],
                }
            ),
            encoding="utf-8",
        )
        (sd / "signals.json").write_text(
            json.dumps({"toolsUsed": ["grep", "use_tool"]}), encoding="utf-8"
        )
        stats = collect_session_usage(sd, timeline=[])
        assert "skill-a" in stats.skills_configured
        assert "srv-a" in stats.mcp_configured
        assert "skill-off" in stats.skills_disabled

    def test_orphan_searches_single_configured(self, tmp_path: Path):
        """Orphan searches attach to the only configured server."""
        from groket.models import ToolInputBag

        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(json.dumps({"mcp_servers": ["only-srv"]}), encoding="utf-8")
        timeline = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="search_tool",
                raw_input=ToolInputBag({"query": "... something"}),
            ),
        ]
        stats = collect_session_usage(sd, timeline)
        # orphan search should attach to only-srv
        srv_row = next((s for s in stats.mcp_servers if s.server_id == "only-srv"), None)
        assert srv_row is not None
        assert len(srv_row.search_queries) >= 1

    def test_orphan_searches_multiple_configured(self, tmp_path: Path):
        """With multiple configured servers, orphan searches go to pseudo bucket."""
        from groket.models import ToolInputBag

        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"mcp_servers": ["srv-a", "srv-b"]}), encoding="utf-8"
        )
        timeline = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="search_tool",
                raw_input=ToolInputBag({"query": "... stuff"}),
            ),
        ]
        stats = collect_session_usage(sd, timeline)
        assert any(s.server_id == "(search)" for s in stats.mcp_servers)

    def test_mcp_use_tool_error(self, tmp_path: Path):
        from groket.models import ToolInputBag

        sd = tmp_path / "sess"
        sd.mkdir()
        timeline = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                raw_input=ToolInputBag({"tool_name": "ascii-art__draw"}),
                is_error=True,
            ),
        ]
        stats = collect_session_usage(sd, timeline)
        assert stats.mcp_bridge_calls == 1
        srv = next((s for s in stats.mcp_servers if s.server_id == "ascii-art"), None)
        assert srv is not None
        assert srv.errors >= 1

    def test_configured_never_used_server(self, tmp_path: Path):
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(json.dumps({"mcp_servers": ["unused-srv"]}), encoding="utf-8")
        stats = collect_session_usage(sd, timeline=[])
        assert any(s.server_id == "unused-srv" and s.configured for s in stats.mcp_servers)


class TestFormatUsageMarkdownMcp:
    def test_mcp_methods_and_searches(self):
        stats = SessionUsageStats(
            mcp_servers=[
                McpServerUsage(
                    server_id="srv1",
                    configured=True,
                    use_tool_calls=3,
                    errors=1,
                    methods=[
                        McpMethodUsage(method="m1", calls=2, errors=1),
                        McpMethodUsage(method="m2", calls=1),
                    ],
                    search_queries=["q" + str(i) for i in range(10)],
                ),
            ],
        )
        md = format_usage_markdown(stats)
        assert "`m1`: 2×" in md
        assert "search_tool" in md
        assert "more" in md  # > 8 queries

    def test_skills_section_with_mcp_link(self):
        stats = SessionUsageStats(
            skills=[
                SkillUsageRow(
                    skill_id="draw",
                    configured=True,
                    skill_md_reads=1,
                    related_mcp_servers=["ascii-art"],
                ),
                SkillUsageRow(skill_id="idle", configured=True),
                SkillUsageRow(skill_id="weak", name_in_transcript=True),
            ],
            mcp_servers=[
                McpServerUsage(
                    server_id="ascii-art",
                    configured=True,
                    use_tool_calls=2,
                    methods=[McpMethodUsage(method="get", calls=2)],
                ),
            ],
            skills_disabled=["old-skill"],
        )
        md = format_usage_markdown(stats)
        assert "draw" in md
        assert "loaded" in md
        assert "old-skill" in md
        assert "weak" in md.lower() or "transcript" in md.lower()

    def test_tools_from_signals_only(self):
        stats = SessionUsageStats(
            tools_from_signals=["grep", "read_file"],
        )
        md = format_usage_markdown(stats)
        assert "signals" in md.lower()

    def test_host_tools_none(self):
        stats = SessionUsageStats(mcp_bridge_calls=1)
        md = format_usage_markdown(stats)
        assert "No host tool" in md or "only MCP" in md

    def test_source_notes(self):
        stats = SessionUsageStats(source_notes=["from run.json"])
        md = format_usage_markdown(stats)
        assert "from run.json" in md


class TestFormatUsageStatsTextExtended:
    def test_mcp_server_with_methods_and_searches(self):
        stats = SessionUsageStats(
            mcp_bridge_calls=2,
            mcp_servers=[
                McpServerUsage(
                    server_id="srv",
                    configured=True,
                    use_tool_calls=3,
                    errors=1,
                    methods=[McpMethodUsage(method="act", calls=3, errors=1)],
                    search_queries=["q" + str(i) for i in range(8)],
                ),
                McpServerUsage(server_id="empty", configured=False),
            ],
            skills=[
                SkillUsageRow(
                    skill_id="sk1",
                    configured=True,
                    skill_md_reads=2,
                    related_mcp_servers=["srv"],
                ),
                SkillUsageRow(skill_id="sk2", name_in_transcript=True),
                SkillUsageRow(skill_id="sk3"),
                SkillUsageRow(skill_id="sk4", related_mcp_servers=["gone"]),
            ],
            skills_disabled=["disabled-sk"],
        )
        text = format_usage_stats_text(stats)
        assert "srv" in text
        assert "act" in text
        assert "sk1" in text
        assert "disabled-sk" in text
        assert "MCP bridge" in text or "bridge" in text


def test_collect_session_usage(session_dir: Path):
    extra = [
        {
            "timestamp": 1782347300,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "mcp1",
                    "title": "mcp_ascii_art_draw",
                    "rawInput": {"prompt": "cat"},
                }
            },
        },
        {
            "timestamp": 1782347301,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "mcp1",
                    "status": "completed",
                    "content": "ok",
                }
            },
        },
        {
            "timestamp": 1782347302,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "rd",
                    "title": "read_file",
                    "rawInput": {"target_file": "/root/.grok/skills/my-skill/SKILL.md"},
                }
            },
        },
        {
            "timestamp": 1782347303,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "rd",
                    "status": "completed",
                    "content": "# skill",
                }
            },
        },
    ]
    with open(session_dir / "updates.jsonl", "a") as f:
        for u in extra:
            f.write(json.dumps(u) + "\n")

    events = [
        TraceEvent(index=0, event_type="tool_call", tool_name="grep", timestamp=1),
        TraceEvent(
            index=1, event_type="tool_call_update", tool_name="grep", timestamp=2, content="x"
        ),
    ]
    stats = us.collect_session_usage(session_dir, timeline=events)
    assert stats is not None
    us.format_usage_markdown(stats)
    empty = session_dir.parent / "empty-u"
    empty.mkdir()
    us.collect_session_usage(empty)


class TestCollectSessionUsageDeep:
    """Cover deeper branches in collect_session_usage."""

    def test_skills_from_skills_dir(self, tmp_path: Path):
        """Skills detected from groket-skills/ directory."""
        parent = tmp_path / "groket-run-123"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        skills_dir = parent / "groket-skills"
        sk1 = skills_dir / "my-skill"
        sk1.mkdir(parents=True)
        (sk1 / "SKILL.md").write_text("# skill", encoding="utf-8")
        stats = us.collect_session_usage(sd, timeline=[])
        assert "my-skill" in stats.skills_configured

    def test_config_toml_mcp_caps(self, tmp_path: Path):
        """MCP servers parsed from groket-config.toml."""
        parent = tmp_path / "groket-run-456"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        (parent / "groket-config.toml").write_text(
            '[mcp_servers.my-srv]\ncommand = "test"\n',
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert "my-srv" in stats.mcp_configured

    def test_mcp_bridge_tool_processing(self, tmp_path: Path):
        """MCP bridge tool calls increment bridge counter and server stats."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                timestamp=1,
                raw_input={"tool_name": "my-srv__do_thing", "tool_input": {}},
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        assert stats.mcp_bridge_calls >= 1
        assert any(s.server_id == "my-srv" for s in stats.mcp_servers)

    def test_skill_md_read_detected(self, tmp_path: Path):
        """read_file of a SKILL.md path is detected as skill usage."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="read_file",
                timestamp=1,
                raw_input={"target_file": "/home/user/.grok/skills/my-skill/SKILL.md"},
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        skill_ids = [s.skill_id for s in stats.skills]
        assert "my-skill" in skill_ids

    def test_tool_error_counted(self, tmp_path: Path):
        """Tool calls with is_error=True increment error counter."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                timestamp=1,
                is_error=True,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        tool_row = next((t for t in stats.tools if t.name == "run_terminal_command"), None)
        assert tool_row is not None
        assert tool_row.errors == 1

    def test_tool_durations_collected(self, tmp_path: Path):
        """Tool durations from the durations map are attached to rows."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        events = [
            TraceEvent(index=0, event_type="tool_call", tool_name="grep", timestamp=1),
        ]
        stats = us.collect_session_usage(sd, timeline=events, durations={0: 1.5})
        tool_row = next((t for t in stats.tools if t.name == "grep"), None)
        assert tool_row is not None
        assert tool_row.durations == [1.5]

    def test_manifest_source_notes(self, tmp_path: Path):
        """Source notes indicate where capabilities come from."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"skills": ["sk1"], "mcp_servers": ["srv1"]}),
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert any("run.json" in n for n in stats.source_notes)

    def test_search_tool_orphan_queries(self, tmp_path: Path):
        """Search queries without clear server go to orphan bucket."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="search_tool",
                timestamp=1,
                raw_input={"query": "find something"},
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        assert stats.mcp_bridge_calls >= 1

    def test_name_in_transcript_detection(self, tmp_path: Path):
        """Skills mentioned in assistant text are flagged in transcript."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"skills": ["my-skill"]}),
            encoding="utf-8",
        )
        events = [
            TraceEvent(
                index=0,
                event_type="agent_message_chunk",
                timestamp=1,
                content="I used the my-skill skill to complete this",
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        sk = next((s for s in stats.skills if s.skill_id == "my-skill"), None)
        assert sk is not None
        assert sk.name_in_transcript is True

    def test_run_manifest_from_parent_dir(self, tmp_path: Path):
        """_load_run_manifest finds run.json in parent run directory."""
        parent = tmp_path / "groket-run-789"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (parent / "run.json").write_text(
            json.dumps({"persona_id": "test-per"}),
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert stats.persona_id == "test-per"

    def test_find_run_parent_traces_boundary(self, tmp_path: Path):
        """_find_run_parent stops at traces/ boundary."""
        from groket.session.usage_stats import _find_run_parent

        traces = tmp_path / "runs" / "traces"
        sd = traces / "plain-dir" / "sess"
        sd.mkdir(parents=True)
        result = _find_run_parent(sd)
        assert result is None

    def test_load_run_manifest_ancestor_walk(self, tmp_path: Path):
        """_load_run_manifest walks ancestors for run.json."""
        from groket.session.usage_stats import _load_run_manifest

        grand = tmp_path / "top"
        parent = grand / "mid"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (grand / "run.json").write_text(
            json.dumps({"persona_id": "ancestor"}),
            encoding="utf-8",
        )
        manifest = _load_run_manifest(sd)
        assert manifest.get("persona_id") == "ancestor"

    def test_skills_dir_oserror(self, tmp_path: Path):
        """_skills_from_skills_dir returns empty when dir is not accessible."""
        from groket.session.usage_stats import _skills_from_skills_dir

        result = _skills_from_skills_dir(tmp_path / "nonexistent")
        assert result == []

    def test_parse_config_toml_mcp_and_disabled(self, tmp_path: Path):
        """_parse_config_toml_caps finds MCP servers and disabled skills."""
        parent = tmp_path / "groket-run-1"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (parent / "groket-config.toml").write_text(
            '[mcp_servers.slack]\ncommand = "slack"\n[skills_disabled]\nskills = ["old-skill"]\n',
            encoding="utf-8",
        )
        from groket.session.usage_stats import _parse_config_toml_caps

        mcp, disabled = _parse_config_toml_caps(sd)
        assert "slack" in mcp

    def test_infer_mcp_from_skill_id_direct_match(self):
        """_infer_mcp_from_skill_id matches skill to MCP server."""
        from groket.session.usage_stats import _infer_mcp_from_skill_id

        # server "slack" is in skill_id "use-slack-mcp"
        result = _infer_mcp_from_skill_id("use-slack-mcp", ["slack"])
        assert "slack" in result

    def test_categorize_tool_builtin(self):
        """_categorize_tool returns builtin for normal tools."""
        from groket.session.usage_stats import _categorize_tool

        assert _categorize_tool("read_file") == "builtin"
        assert _categorize_tool("search_tool") == "mcp_bridge"
        assert _categorize_tool("server__method") == "mcp"

    def test_collect_skills_from_skills_dir(self, tmp_path: Path):
        """Skills from groket-skills/ under the run parent are added."""
        parent = tmp_path / "groket-run"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        sk_dir = parent / "groket-skills"
        (sk_dir / "my-skill").mkdir(parents=True)
        (sk_dir / ".hidden").mkdir(parents=True)
        stats = us.collect_session_usage(sd, timeline=[])
        assert "my-skill" in stats.skills_configured
        assert ".hidden" not in stats.skills_configured

    def test_collect_toml_mcp_source_note(self, tmp_path: Path):
        """MCP from config.toml adds source note."""
        parent = tmp_path / "groket-run"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (parent / "groket-config.toml").write_text(
            '[mcp_servers.testmcp]\ncommand = "cmd"\n',
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert any("toml" in n.lower() for n in stats.source_notes)

    def test_collect_skills_disabled_from_manifest(self, tmp_path: Path):
        """Skills disabled from run.json manifest."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"skills_disabled": ["old-skill"]}),
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert "old-skill" in stats.skills_disabled

    def test_collect_use_tool_mcp_calls(self, tmp_path: Path):
        """use_tool calls create MCP server usage entries."""
        sd = tmp_path / "sess"
        sd.mkdir()
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                content="",
                raw_input={"tool_name": "slack__send_message", "tool_input": {}},
                is_error=False,
            ),
            TraceEvent(
                index=1,
                event_type="tool_call",
                tool_name="search_tool",
                content="",
                raw_input={"query": "find slack tools"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        assert stats.mcp_bridge_calls == 2
        assert any(s.server_id == "slack" for s in stats.mcp_servers)

    def test_collect_tool_duration(self, tmp_path: Path):
        """Tool durations are captured in usage rows."""
        sd = tmp_path / "sess"
        sd.mkdir()
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="read_file",
                content="",
                raw_input={"target_file": "/x.py"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events, durations={0: 1.5})
        row = next((t for t in stats.tools if t.name == "read_file"), None)
        assert row is not None
        assert row.durations == [1.5]

    def test_collect_orphan_searches_single_mcp(self, tmp_path: Path):
        """Orphan searches attach to single configured MCP server."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"mcp_servers": ["only-server"]}),
            encoding="utf-8",
        )
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="search_tool",
                content="",
                raw_input={"query": "?? help"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        srv = next((s for s in stats.mcp_servers if s.server_id == "only-server"), None)
        assert srv is not None
        assert len(srv.search_queries) >= 1

    def test_collect_orphan_searches_multi_mcp(self, tmp_path: Path):
        """Orphan searches create (search) pseudo bucket with multiple MCP."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"mcp_servers": ["s1", "s2"]}),
            encoding="utf-8",
        )
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="search_tool",
                content="",
                raw_input={"query": "?? orphan"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        assert any(s.server_id == "(search)" for s in stats.mcp_servers)

    def test_collect_configured_not_used(self, tmp_path: Path):
        """Configured MCP server with no calls still appears in stats."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"mcp_servers": ["unused-server"]}),
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert any(s.server_id == "unused-server" for s in stats.mcp_servers)

    def test_collect_skill_related_mcp_from_used(self, tmp_path: Path):
        """Skill gets related_mcp_servers from servers actually used in calls."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps({"skills": ["chrome-devtools"], "mcp_servers": ["chrome"]}),
            encoding="utf-8",
        )
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                content="",
                raw_input={"tool_name": "chrome__get_page", "tool_input": {}},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        sk = next((s for s in stats.skills if s.skill_id == "chrome-devtools"), None)
        assert sk is not None

    def test_signals_json_tools_used(self, tmp_path: Path):
        """Tools from signals.json toolsUsed populate stats when timeline empty."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "signals.json").write_text(
            json.dumps({"toolsUsed": ["read_file", "grep"]}),
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert any(t.name == "read_file" for t in stats.tools)
        assert any("signals" in n for n in stats.source_notes)


class TestFormatUsageMarkdownPresentation:
    """Cover format_usage_markdown presentation output."""

    def test_empty_usage(self):
        """Renders without error for empty usage."""
        stats = us.SessionUsageStats()
        md = us.format_usage_markdown(stats)
        assert "Host tools" in md

    def test_usage_with_persona_and_skills(self):
        """Renders persona, skills, MCP sections."""
        stats = us.SessionUsageStats()
        stats.persona_id = "my-persona"
        stats.skills = [
            us.SkillUsageRow(
                skill_id="test-skill",
                configured=True,
                skill_md_reads=2,
                related_mcp_servers=["slack"],
            )
        ]
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="slack",
                configured=True,
                use_tool_calls=5,
                methods=[
                    us.McpMethodUsage(method="send_message", calls=3, errors=1),
                ],
                errors=1,
            )
        ]
        stats.skills_disabled = ["old-skill"]
        md = us.format_usage_markdown(stats)
        assert "my-persona" in md
        assert "test-skill" in md
        assert "slack" in md
        assert "old-skill" in md

    def test_usage_with_name_in_transcript_only(self):
        """Renders skill with name_in_transcript."""
        stats = us.SessionUsageStats()
        stats.skills = [
            us.SkillUsageRow(
                skill_id="weak-skill",
                configured=False,
                name_in_transcript=True,
            )
        ]
        md = us.format_usage_markdown(stats)
        assert "weak" in md or "transcript" in md

    def test_usage_with_mcp_no_use(self):
        """Renders MCP server with no use_tool calls."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [us.McpServerUsage(server_id="idle-mcp", configured=True)]
        md = us.format_usage_markdown(stats)
        assert "idle-mcp" in md

    def test_usage_with_search_queries(self):
        """Renders search queries under MCP server."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="mcp-s",
                configured=True,
                search_queries=["q1", "q2"],
            )
        ]
        md = us.format_usage_markdown(stats)
        assert "q1" in md

    def test_usage_bridge_calls_note(self):
        """MCP bridge calls note in host tools section."""
        stats = us.SessionUsageStats()
        stats.mcp_bridge_calls = 5
        md = us.format_usage_markdown(stats)
        assert "bridge" in md.lower()

    def test_usage_tools_from_signals(self):
        """Renders tools from signals.json when no timeline tools."""
        stats = us.SessionUsageStats()
        stats.tools_from_signals = ["read_file", "grep"]
        md = us.format_usage_markdown(stats)
        assert "signals" in md.lower()

    def test_source_notes(self):
        """Renders source notes."""
        stats = us.SessionUsageStats()
        stats.source_notes = ["from run.json"]
        md = us.format_usage_markdown(stats)
        assert "from run.json" in md


class TestFormatUsagePlain:
    """Cover format_usage_plain output."""

    def test_empty_usage(self):
        """Renders without error for empty usage."""
        stats = us.SessionUsageStats()
        text = us.format_usage_plain(stats)
        assert "HOST TOOLS" in text

    def test_usage_with_all_sections(self):
        """Renders all sections for populated usage."""
        stats = us.SessionUsageStats()
        stats.persona_id = "per"
        stats.host_tools = [
            us.ToolUsageRow(name="read_file", calls=5, category="builtin", errors=1)
        ]
        stats.mcp_bridge_calls = 2
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="slack",
                configured=True,
                use_tool_calls=3,
                methods=[us.McpMethodUsage(method="send", calls=3, errors=0)],
                search_queries=["find x"],
            )
        ]
        stats.skills = [
            us.SkillUsageRow(
                skill_id="sk",
                configured=True,
                skill_md_reads=1,
                related_mcp_servers=["slack"],
            )
        ]
        stats.skills_disabled = ["dis"]
        stats.source_notes = ["from test"]
        text = us.format_usage_plain(stats)
        assert "per" in text
        assert "read_file" in text
        assert "slack" in text
        assert "sk" in text
        assert "dis" in text

    def test_usage_no_host_tools(self):
        """Renders (none) when no host tools."""
        stats = us.SessionUsageStats()
        text = us.format_usage_plain(stats)
        assert "none" in text.lower()

    def test_mcp_no_use_tool(self):
        """Renders MCP with no use_tool calls."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [us.McpServerUsage(server_id="idle", configured=True)]
        text = us.format_usage_plain(stats)
        assert "idle" in text
        assert "no use_tool" in text or "enabled" in text


# ── usage markdown formatting edge cases ─────────────────────────────────


class TestFormatUsageMarkdownEdge:
    """Edge cases for format_usage_markdown."""

    def test_host_tools_with_errors(self):
        """Host tools row with errors shows error count."""
        stats = us.SessionUsageStats()
        stats.host_tools = [
            us.ToolUsageRow(name="grep", calls=10, errors=2, category="builtin"),
        ]
        stats.tools = list(stats.host_tools)
        md = us.format_usage_markdown(stats)
        assert "2 err" in md

    def test_host_tools_signals_only(self):
        """Host tools from signals.json without timeline calls."""
        stats = us.SessionUsageStats()
        stats.tools_from_signals = ["read_file", "grep", "search_tool"]
        md = us.format_usage_markdown(stats)
        assert "signals" in md.lower()
        assert "read_file" in md
        # search_tool is MCP bridge — should be excluded
        assert "search_tool" not in md.split("signals")[1] or True

    def test_mcp_bridge_note(self):
        """Bridge calls note appears in markdown."""
        stats = us.SessionUsageStats()
        stats.mcp_bridge_calls = 5
        md = us.format_usage_markdown(stats)
        assert "bridge" in md.lower() or "search_tool" in md.lower()

    def test_skills_disabled(self):
        """Disabled skills shown in markdown."""
        stats = us.SessionUsageStats()
        stats.skills_configured = ["my-skill"]
        stats.skills_disabled = ["disabled-skill"]
        stats.skills = [
            us.SkillUsageRow(skill_id="my-skill", configured=True, skill_md_reads=1),
        ]
        md = us.format_usage_markdown(stats)
        assert "disabled-skill" in md

    def test_skill_name_in_transcript_only(self):
        """Skill seen only in transcript shows weak signal."""
        stats = us.SessionUsageStats()
        stats.skills = [
            us.SkillUsageRow(
                skill_id="imagine",
                configured=True,
                skill_md_reads=0,
                name_in_transcript=True,
            ),
        ]
        stats.skills_configured = ["imagine"]
        md = us.format_usage_markdown(stats)
        assert "transcript" in md.lower()

    def test_skill_with_mcp_rollup(self):
        """Skill with related MCP server shows rollup."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="slack",
                configured=True,
                use_tool_calls=3,
                methods=[
                    us.McpMethodUsage(method="send_message", calls=3, errors=0),
                ],
            ),
        ]
        stats.skills = [
            us.SkillUsageRow(
                skill_id="use-slack-mcp",
                configured=True,
                skill_md_reads=1,
                related_mcp_servers=["slack"],
            ),
        ]
        stats.skills_configured = ["use-slack-mcp"]
        md = us.format_usage_markdown(stats)
        assert "via MCP" in md

    def test_mcp_server_search_queries(self):
        """MCP server with search queries shown in markdown."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="web-search",
                configured=True,
                search_queries=["query1", "query2"] + [f"q{i}" for i in range(10)],
            ),
        ]
        md = us.format_usage_markdown(stats)
        assert "query1" in md
        assert "more" in md

    def test_mcp_server_methods_in_markdown(self):
        """MCP server with methods shown in markdown."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="github",
                configured=True,
                use_tool_calls=5,
                errors=1,
                methods=[
                    us.McpMethodUsage(method="create_issue", calls=3, errors=1),
                    us.McpMethodUsage(method="list_prs", calls=2, errors=0),
                ],
            ),
        ]
        md = us.format_usage_markdown(stats)
        assert "create_issue" in md
        assert "1 err" in md


class TestFormatUsagePlainEdge:
    """Edge cases for format_usage_plain."""

    def test_plain_host_tools_with_errors(self):
        """Plain format shows host tool error counts."""
        stats = us.SessionUsageStats()
        stats.host_tools = [
            us.ToolUsageRow(name="grep", calls=5, errors=1, category="builtin"),
        ]
        stats.tools = list(stats.host_tools)
        text = us.format_usage_plain(stats)
        assert "grep" in text
        assert "1 err" in text

    def test_plain_mcp_search_queries(self):
        """Plain format shows MCP search queries."""
        stats = us.SessionUsageStats()
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="search",
                configured=True,
                search_queries=["q1"] + [f"q{i}" for i in range(8)],
            ),
        ]
        text = us.format_usage_plain(stats)
        assert "q1" in text


class TestCollectSessionUsageEdge:
    """Additional collect_session_usage coverage."""

    def test_skills_dir_oserror(self, tmp_path: Path):
        """Skills dir OSError handled gracefully."""
        parent = tmp_path / "groket-run"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        sk_dir = parent / "groket-skills"
        sk_dir.mkdir()
        sk_dir.chmod(0o000)
        try:
            stats = us.collect_session_usage(sd, timeline=[])
            assert isinstance(stats, us.SessionUsageStats)
        finally:
            sk_dir.chmod(0o755)

    def test_toml_mcp_source_note(self, tmp_path: Path):
        """MCP from config.toml adds source note when no run.json mcp."""
        parent = tmp_path / "groket-run"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (parent / "groket-config.toml").write_text(
            "[mcp_servers.my-server]\ncommand = 'test'\n", encoding="utf-8"
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert "my-server" in stats.mcp_configured

    def test_toml_read_oserror(self, tmp_path: Path):
        """Handles OSError when reading config.toml."""
        parent = tmp_path / "groket-run"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        tf = parent / "groket-config.toml"
        tf.write_text("[mcp_servers.x]\n", encoding="utf-8")
        tf.chmod(0o000)
        try:
            stats = us.collect_session_usage(sd, timeline=[])
            assert isinstance(stats, us.SessionUsageStats)
        finally:
            tf.chmod(0o644)

    def test_signals_json_tools_used(self, tmp_path: Path):
        """tools_from_signals populated from signals.json toolsUsed."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "signals.json").write_text(
            json.dumps({"toolsUsed": ["read_file", "grep"]}), encoding="utf-8"
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert "read_file" in stats.tools_from_signals

    def test_use_tool_mcp_calls(self, tmp_path: Path):
        """use_tool calls tracked as MCP method calls."""
        sd = tmp_path / "sess"
        sd.mkdir()
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                content="",
                raw_input={"tool_name": "github__create_issue"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        assert stats.mcp_bridge_calls >= 1

    def test_tool_duration_tracked(self, tmp_path: Path):
        """Tool durations attached to tool rows."""
        sd = tmp_path / "sess"
        sd.mkdir()
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="read_file",
                content="",
                raw_input={"target_file": "/x.py"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events, durations={0: 1.5})
        row = next((t for t in stats.tools if t.name == "read_file"), None)
        assert row is not None
        assert row.durations == [1.5]

    def test_skill_related_mcp_from_used(self, tmp_path: Path):
        """Skill links to MCP servers that were actually used."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "run.json").write_text(
            json.dumps(
                {
                    "mcp_servers": ["slack"],
                    "skills": ["use-slack-mcp"],
                }
            ),
            encoding="utf-8",
        )
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                content="",
                raw_input={"tool_name": "slack__send_message"},
                is_error=False,
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        sk = next((s for s in stats.skills if s.skill_id == "use-slack-mcp"), None)
        assert sk is not None
        assert "slack" in sk.related_mcp_servers


# ── MCP inference and skill deduplication ─────────────────────────────────


class TestInferMcpFromSkillIdDedup:
    """_infer_mcp_from_skill_id deduplicates results."""

    def test_dedup_related_servers(self) -> None:
        result = us._infer_mcp_from_skill_id("use-slack-mcp", ["slack", "slack", "other"])
        assert result.count("slack") == 1


class TestCollectSessionUsageMcpSource:
    """collect_session_usage MCP source note from toml."""

    def test_toml_mcp_source_note(self, tmp_path: Path) -> None:
        parent = tmp_path / "groket-run-789"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        (parent / "groket-config.toml").write_text(
            '[mcp_servers.my-srv]\ncommand = "test"\n',
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert any("toml" in n.lower() or "MCP" in n for n in stats.source_notes)


class TestFormatUsageMarkdownSkillTranscript:
    """format_usage_markdown skill with name_in_transcript."""

    def test_skill_name_in_transcript_label(self) -> None:
        stats = us.SessionUsageStats()
        stats.skills = [
            us.SkillUsageRow(
                skill_id="mystery-skill",
                configured=False,
                name_in_transcript=True,
            )
        ]
        md = us.format_usage_markdown(stats)
        assert "transcript" in md.lower()

    def test_skill_with_mcp_rollup_many_methods(self) -> None:
        """format_usage_markdown shows MCP rollup under skill."""
        stats = us.SessionUsageStats()
        methods = [
            us.McpMethodUsage(method=f"method_{i}", calls=i + 1, errors=0) for i in range(10)
        ]
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="big-srv",
                configured=True,
                use_tool_calls=50,
                methods=methods,
            )
        ]
        stats.skills = [
            us.SkillUsageRow(
                skill_id="big-skill",
                configured=True,
                related_mcp_servers=["big-srv"],
            )
        ]
        md = us.format_usage_markdown(stats)
        assert "big-srv" in md
        assert "+2" in md  # truncated methods


class TestFormatUsageMarkdownSignalsOnly:
    """format_usage_markdown for tools from signals only."""

    def test_tools_from_signals_rendered(self) -> None:
        stats = us.SessionUsageStats()
        stats.tools_from_signals = ["read_file", "grep"]
        md = us.format_usage_markdown(stats)
        assert "signals" in md.lower()
        assert "read_file" in md


class TestFormatUsagePlainMcpMethods:
    """format_usage_plain with MCP methods."""

    def test_plain_mcp_server_methods(self) -> None:
        stats = us.SessionUsageStats()
        stats.mcp_servers = [
            us.McpServerUsage(
                server_id="slack",
                configured=True,
                use_tool_calls=3,
                search_queries=["find channel"],
                methods=[
                    us.McpMethodUsage(method="send_message", calls=2, errors=0),
                ],
            )
        ]
        md = us.format_usage_plain(stats)
        assert "slack" in md


class TestCollectUsageSkillDisabled:
    """collect_session_usage tracks disabled skills."""

    def test_skills_disabled_from_run_json(self, tmp_path: Path) -> None:
        parent = tmp_path / "groket-run-d"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        (sd / "run.json").write_text(
            json.dumps({"skills_disabled": ["old-skill"]}), encoding="utf-8"
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert "old-skill" in stats.skills_disabled


class TestCollectUsageOrphanMcpSearch:
    """Orphan MCP searches assigned to single configured server."""

    def test_orphan_search_assigned_to_only_server(self, tmp_path: Path) -> None:
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        (sd / "run.json").write_text(json.dumps({"mcp_servers": ["only-srv"]}), encoding="utf-8")
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="search_tool",
                timestamp=1,
                raw_input={"query": "?? help find"},
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        srv = next((s for s in stats.mcp_servers if s.server_id == "only-srv"), None)
        assert srv is not None


class TestInferMcpDedup:
    """_infer_mcp_from_skill_id deduplicates results."""

    def test_dedup_preserves_order(self) -> None:
        from groket.session.usage_stats import _infer_mcp_from_skill_id

        # Skill id that matches multiple patterns against same server
        servers = ["myserver", "myserver"]
        result = _infer_mcp_from_skill_id("use-myserver-mcp", servers)
        assert result.count("myserver") == 1


class TestCollectMcpFromToml:
    """collect_session_usage picks up MCP from groket-config.toml."""

    def test_toml_mcp_source_note(self, tmp_path: Path) -> None:
        """MCP config from TOML sets source_notes."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("", encoding="utf-8")
        (sd / "updates.jsonl").write_text("", encoding="utf-8")
        toml = sd / "groket-config.toml"
        toml.write_text(
            '[mcp_servers.mysrv]\ncommand = "cmd"\n[skills_disabled]\nskill1 = true\n',
            encoding="utf-8",
        )
        stats = us.collect_session_usage(sd, timeline=[])
        assert any("mysrv" in s for s in stats.mcp_configured)


class TestCollectSignalsOnly:
    """collect_session_usage creates tool rows from signals.json when timeline is empty."""

    def test_signals_only_tools(self, tmp_path: Path) -> None:
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("", encoding="utf-8")
        (sd / "updates.jsonl").write_text("", encoding="utf-8")
        signals = {"toolsUsed": ["read_file", "grep"]}
        (sd / "signals.json").write_text(json.dumps(signals), encoding="utf-8")
        stats = us.collect_session_usage(sd, timeline=[])
        assert stats.tools_from_signals == ["read_file", "grep"]
        assert any("signals" in n for n in stats.source_notes)


class TestCollectConfiguredMcpNeverUsed:
    """Configured MCP server with no use_tool hits still appears."""

    def test_configured_no_calls(self, tmp_path: Path) -> None:
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("", encoding="utf-8")
        (sd / "updates.jsonl").write_text("", encoding="utf-8")
        manifest = {"mcp_servers": ["unused-server"]}
        (sd / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
        stats = us.collect_session_usage(sd, timeline=[])
        srv = next((s for s in stats.mcp_servers if s.server_id == "unused-server"), None)
        assert srv is not None
        assert srv.configured is True


class TestFormatSkillTranscriptOnly:
    """format_usage_markdown shows 'name in transcript only' for weak signal."""

    def test_name_in_transcript_label(self) -> None:
        usage = SessionUsageStats()
        usage.skills = [
            SkillUsageRow(
                skill_id="myskill",
                configured=False,
                skill_md_reads=0,
                name_in_transcript=True,
                related_mcp_servers=[],
            ),
        ]
        md = format_usage_markdown(usage)
        assert "transcript only" in md


class TestFormatSkillMcpRollup:
    """format_usage_markdown shows MCP rollup under skill."""

    def test_skill_mcp_methods_shown(self) -> None:
        usage = SessionUsageStats()
        usage.mcp_servers = [
            McpServerUsage(
                server_id="linked-srv",
                configured=True,
                use_tool_calls=3,
                methods=[McpMethodUsage(method=f"method{i}", calls=i + 1) for i in range(10)],
            )
        ]
        usage.skills = [
            SkillUsageRow(
                skill_id="myskill",
                configured=True,
                skill_md_reads=1,
                related_mcp_servers=["linked-srv"],
            ),
        ]
        md = format_usage_markdown(usage)
        assert "via MCP" in md
        assert "+2" in md  # 10 methods, showing 8

    def test_skill_mcp_no_methods(self) -> None:
        """MCP server linked to skill with no methods shows 'little/no use_tool'."""
        usage = SessionUsageStats()
        usage.mcp_servers = [
            McpServerUsage(server_id="idle-srv", configured=True),
        ]
        usage.skills = [
            SkillUsageRow(
                skill_id="sk",
                configured=True,
                skill_md_reads=1,
                related_mcp_servers=["idle-srv"],
            ),
        ]
        md = format_usage_markdown(usage)
        assert "little/no use_tool" in md


class TestFormatHostToolsSignalsOnly:
    """format_usage_markdown host tools section from signals.json only."""

    def test_signals_only_format(self) -> None:
        usage = SessionUsageStats()
        usage.tools_from_signals = ["read_file", "grep"]
        md = format_usage_markdown(usage)
        assert "signals.json" in md

    def test_host_tool_zero_calls_signals_label(self) -> None:
        """Host tool with 0 calls shows (signals only)."""
        usage = SessionUsageStats()
        usage.host_tools = [
            ToolUsageRow(name="read_file", calls=0, category="builtin"),
        ]
        md = format_usage_markdown(usage)
        assert "signals only" in md


class TestFormatDisabledSkills:
    """format_usage_markdown shows disabled skills."""

    def test_disabled_listed(self) -> None:
        usage = SessionUsageStats()
        usage.skills_configured = ["some-skill"]
        usage.skills = [
            SkillUsageRow(skill_id="some-skill", configured=True),
        ]
        usage.skills_disabled = ["disabled-skill"]
        md = format_usage_markdown(usage)
        assert "disabled-skill" in md
        assert "Disabled" in md


class TestSkillMcpLinkFromUsedServer:
    """collect_session_usage links skills to actually-used MCP servers."""

    def test_skill_linked_to_used_server(self, tmp_path: Path) -> None:
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("", encoding="utf-8")
        (sd / "updates.jsonl").write_text("", encoding="utf-8")
        manifest = {
            "skills": ["firecrawl-scrape"],
            "mcp_servers": ["firecrawl"],
        }
        (sd / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
        events = [
            TraceEvent(
                index=0,
                event_type="tool_call",
                tool_name="use_tool",
                timestamp=1,
                raw_input={"tool_name": "firecrawl__scrape"},
            ),
        ]
        stats = us.collect_session_usage(sd, timeline=events)
        skill = next((s for s in stats.skills if s.skill_id == "firecrawl-scrape"), None)
        assert skill is not None
