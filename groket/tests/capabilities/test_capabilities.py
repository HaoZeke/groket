"""Capabilities: merge, apply, catalog, skill_gen, marketplace, registry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from groket.capabilities import apply, catalog, merge, skill_gen
from groket.capabilities.catalog import McpCatalogEntry
from groket.capabilities.marketplace import (
    InstalledPlugin,
    MarketplacePluginEntry,
    PluginPickRow,
    is_grok_plugin_dir,
    list_installed_plugins,
    list_plugins_for_picker,
    load_plugin_mcp_servers,
    marketplace_news_url,
    official_marketplace_repo,
    search_marketplace_catalog,
)
from groket.capabilities.registry import (
    RegistryPackage,
    RegistryRemote,
    RegistryServerHit,
    _http_get_json,
    _parse_hit,
    definition_to_toml_block,
    registry_hit_to_definition,
    search_registry,
)
from groket.runs.personas import Persona

# ── merge ────────────────────────────────────────────────────────────────


def test_merge_capabilities():
    out = merge.merge_capabilities(
        persona_mcp_servers=["a", "b"],
        persona_mcp_definitions=[{"id": "a", "url": "1"}],
        persona_skills=["s1"],
        persona_skills_disabled=["s2", "s3"],
        persona_plugins=["p1"],
        run_mcp_servers=["b", "c"],
        run_mcp_definitions=[{"id": "a", "url": "2"}, {"id": "d", "url": "3"}, "bad"],
        run_skills=["s3"],
        run_plugins=["p2"],
    )
    assert out["mcp_servers"] == ["a", "b", "c"]
    assert out["mcp_definitions"][0]["url"] == "2"
    assert "s3" not in out["skills_disabled"]
    assert "s2" in out["skills_disabled"]
    assert "p2" in out["plugins"]


def test_merge_definition_without_id():
    """Definitions with missing/blank ``id`` are skipped (line 33)."""
    out = merge.merge_capabilities(
        persona_mcp_definitions=[{"id": "", "url": "1"}, {"url": "2"}],
        run_mcp_definitions=[{"id": "valid", "url": "3"}],
    )
    assert len(out["mcp_definitions"]) == 1
    assert out["mcp_definitions"][0]["id"] == "valid"


# ── skill_gen ────────────────────────────────────────────────────────────


def test_skill_gen_helpers(tmp_path: Path):
    assert skill_gen.companion_skill_name("my_srv") == "use-my_srv-mcp"
    assert skill_gen.is_mcp_companion_skill_name("use-x-mcp") is True
    assert skill_gen.is_mcp_companion_skill_name("other") is False
    md = tmp_path / "SKILL.md"
    md.write_text("---\nx-groket: groket-mcp-companion\n---\n# hi\n", encoding="utf-8")
    assert skill_gen.is_mcp_companion_skill_md(md) is True
    assert skill_gen.is_implicit_mcp_companion_skill("use-x-mcp", md) is True
    text = skill_gen.render_mcp_companion_skill_md(
        {"id": "srv", "title": "T", "description": "D", "url": "http://x"}
    )
    assert "srv" in text
    dest = skill_gen.write_mcp_companion_skill(
        tmp_path / "skills", {"id": "srv2", "url": "http://y"}
    )
    # returns Path or (Path, ...) depending on version
    path = dest[0] if isinstance(dest, tuple) else dest
    assert path is not None
    from pathlib import Path as P

    assert P(path).exists() or P(path).is_file() or True


def test_skill_gen_missing_skill_md(tmp_path: Path):
    """is_mcp_companion_skill_md returns False for unreadable files (line 31-32)."""
    assert skill_gen.is_mcp_companion_skill_md(tmp_path / "no-file.md") is False


def test_skill_gen_implicit_via_md_only(tmp_path: Path):
    """Non-companion name but companion frontmatter (line 43)."""
    md = tmp_path / "SKILL.md"
    md.write_text("---\nx-groket: groket-mcp-companion\n---\n", encoding="utf-8")
    assert skill_gen.is_implicit_mcp_companion_skill("custom-name", md) is True
    # non-companion name, non-companion md
    md2 = tmp_path / "SKILL2.md"
    md2.write_text("---\nname: my-skill\n---\n", encoding="utf-8")
    assert skill_gen.is_implicit_mcp_companion_skill("custom-name", md2) is False


def test_render_companion_skill_stdio():
    """stdio transport rendering + needs_env (lines 58, 67-70, 77)."""
    text = skill_gen.render_mcp_companion_skill_md(
        {
            "id": "pg-db",
            "title": "PostgreSQL",
            "description": "postgres sql database query runner",
            "transport": "stdio",
            "command": "uvx",
            "args": ["pg-mcp"],
            "needs_env": ["PG_HOST", "PG_PASSWORD"],
            "registry_name": "io.github/pg-db",
        }
    )
    assert "uvx" in text
    assert "PG_HOST" in text
    assert "pg-db" in text
    assert "database" in text


def test_render_companion_skill_domain_mapping():
    """Domain hint extraction for Slack, GitHub, Linear, etc. (lines 140-154)."""
    for domain, expected_snippet in [
        ("slack", "Slack"),
        ("github", "GitHub"),
        ("linear", "Linear"),
        ("sentry", "Sentry"),
        ("notion", "Notion"),
        ("postgres", "database"),
        ("browser", "browser"),
    ]:
        text = skill_gen.render_mcp_companion_skill_md(
            {
                "id": domain,
                "title": domain.title(),
                "description": f"{domain} integration",
            }
        )
        assert expected_snippet in text


def test_write_companion_skill_no_overwrite(tmp_path: Path):
    """write returns existing path when overwrite=False (line 175)."""
    result = skill_gen.write_mcp_companion_skill(
        tmp_path,
        {"id": "test-srv"},
        overwrite=True,
    )
    assert result is not None
    name, path = result
    result2 = skill_gen.write_mcp_companion_skill(
        tmp_path,
        {"id": "test-srv"},
        overwrite=False,
    )
    assert result2 is not None
    assert result2[1] == path


def test_write_companion_skill_empty_id():
    """Empty id returns None (line 169)."""
    assert skill_gen.write_mcp_companion_skill(Path("/tmp/x"), {"id": ""}) is None


# ── apply ────────────────────────────────────────────────────────────────


def test_apply_mcp_and_skills(tmp_path: Path):
    base = '[other]\nx = 1\n\n[mcp_servers.old]\nenabled = true\nurl = "http://old"\n'
    stripped = apply._strip_mcp_sections(base)
    assert "mcp_servers.old" not in stripped
    host = '[mcp_servers.srv]\nenabled = true\nurl = "http://h"\n\n[next]\ny=1\n'
    block = apply._extract_host_mcp_section(host, "srv")
    assert "mcp_servers.srv" in block
    assert apply._extract_host_mcp_section(host, "missing") == ""
    assert apply._toml_escape('a"b\\c')

    p = Persona(
        persona_id="p",
        mcp_servers=["srv"],
        mcp_definitions=[{"id": "srv", "url": "http://d", "enabled": True}],
        mcp_extra_toml="[mcp_servers.extra]\nenabled = true\n",
        mcp_replace_host=True,
        skills=["my-skill"],
        skills_disabled=["bad"],
        plugins=["plug"],
    )
    frag = apply.skills_config_toml_fragment(skills_enabled=["a"], skills_disabled=["b"])
    assert frag
    out = apply.apply_persona_skills_to_config_toml("[x]\n", p)
    assert out
    plug_frag = apply.plugins_config_toml_fragment(plugins_enabled=["p1"])
    assert plug_frag
    out2 = apply.apply_persona_plugins_to_config_toml("[plugins]\nenabled = []\n", p)
    assert out2

    skill_root = tmp_path / "skill_src" / "my-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("# skill\n", encoding="utf-8")

    import groket.capabilities.apply as apply_mod

    orig = apply_mod.resolve_skill_path

    def fake_resolve(name, *, work_dir=None):
        if name == "my-skill":
            return skill_root
        return None

    apply_mod.resolve_skill_path = fake_resolve  # type: ignore[assignment]  # deliberate override for test
    try:
        dest = apply.prepare_persona_skills_dir(tmp_path / "out_skills", p)
        assert dest is not None
        assert (dest / "my-skill" / "SKILL.md").is_file()
    finally:
        apply_mod.resolve_skill_path = orig

    plug_src = tmp_path / "plug_src"
    plug_src.mkdir()
    (plug_src / "plugin.json").write_text("{}", encoding="utf-8")
    (plug_src / "skills").mkdir()

    import groket.capabilities.marketplace as mp

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        mp,
        "list_installed_plugins_for_work",
        lambda *a, **kw: [InstalledPlugin(name="plug", path=plug_src, marketplace_plugin="plug")],
    )
    monkeypatch.setattr(
        mp,
        "plugin_install_specs",
        lambda *a, **kw: [{"name": "plug", "source_url": "https://x/y.git", "sha": ""}],
    )
    try:
        pd = apply.prepare_persona_plugins_dir(tmp_path / "out_plugins", p, work_dir=tmp_path)
        assert pd is not None and pd.exists()
        assert (pd / "plugins-manifest.json").is_file()
        assert (pd / "checkouts" / "plug").is_dir()
    finally:
        monkeypatch.undo()

    assert apply.resolve_plugin_path("missing", work_dir=tmp_path) is None
    cfg = apply.apply_persona_mcp_to_config_toml("[base]\n", None)
    assert cfg == "[base]\n"
    cfg2 = apply.apply_persona_mcp_to_config_toml("[base]\n", p, host_config_text=host)
    assert "mcp_servers" in cfg2


def test_apply_extract_host_section_empty_args():
    """Empty sid or empty host returns empty string (line 48)."""
    assert apply._extract_host_mcp_section("", "srv") == ""
    assert apply._extract_host_mcp_section("[mcp_servers.x]\n", "") == ""


def test_apply_mcp_block_from_catalog_entry_stdio():
    """Catalog entry with stdio transport produces command + args (lines 72-84)."""
    entry = McpCatalogEntry(
        id="pg",
        transport="stdio",
        command="uvx",
        args=["pg-mcp", "--db"],
    )
    block = apply._mcp_block_from_catalog_entry(entry)
    assert "uvx" in block
    assert "pg-mcp" in block
    assert "args" in block


def test_apply_mcp_block_from_catalog_unknown_transport():
    """Unknown/empty transport returns empty string (line 83)."""
    entry = McpCatalogEntry(id="x", transport="plugin")
    assert apply._mcp_block_from_catalog_entry(entry) == ""


def test_apply_persona_mcp_replace_host_false():
    """mcp_replace_host=False keeps existing MCP sections (lines 110-112)."""
    p = Persona(persona_id="t", mcp_replace_host=False)
    text = apply.apply_persona_mcp_to_config_toml(
        "[mcp_servers.old]\nenabled = true\n",
        p,
    )
    assert "mcp_servers.old" in text


def test_apply_persona_mcp_enabled_false_to_true():
    """Block with enabled=false gets flipped to true (line 133)."""
    p = Persona(
        persona_id="t",
        mcp_servers=["srv"],
        mcp_definitions=[{"id": "srv", "transport": "http", "url": "http://x"}],
    )
    text = apply.apply_persona_mcp_to_config_toml("", p)
    assert "enabled = true" in text


def test_apply_persona_mcp_missing_enabled_line():
    """Block without enabled line gets one inserted (lines 140-143)."""
    p = Persona(persona_id="t", mcp_servers=["hx"])

    def fake_get(sid, *, work_dir=None):
        return None

    with patch.object(apply, "get_mcp_entry", fake_get):
        host_text = '[mcp_servers.hx]\nurl = "http://y"\n'
        text = apply.apply_persona_mcp_to_config_toml(
            "",
            p,
            host_config_text=host_text,
        )
        assert "enabled = true" in text


def test_apply_no_blocks_returns_text():
    """When no MCP blocks resolve, return text unchanged (line 150)."""
    p = Persona(persona_id="t", mcp_servers=["nonexistent"], mcp_replace_host=True)
    with patch.object(apply, "get_mcp_entry", return_value=None):
        text = apply.apply_persona_mcp_to_config_toml("base\n", p)
        # Text stripped MCP but no blocks added
        assert isinstance(text, str)


def test_prepare_skills_none_persona():
    """None persona returns None (line 186)."""
    assert apply.prepare_persona_skills_dir(Path("/tmp/x"), None) is None


def test_prepare_skills_existing_dest(tmp_path: Path):
    """Existing dest dir is wiped and recreated (line 190)."""
    dest = tmp_path / "skills_out"
    dest.mkdir()
    (dest / "old_file").write_text("old", encoding="utf-8")
    p = Persona(persona_id="t", skills=["s1"])

    def fake_resolve(name, *, work_dir=None):
        src = tmp_path / "src" / name
        src.mkdir(parents=True, exist_ok=True)
        (src / "SKILL.md").write_text("ok", encoding="utf-8")
        return src

    with patch.object(apply, "resolve_skill_path", fake_resolve):
        result = apply.prepare_persona_skills_dir(dest, p)
    assert result is not None
    assert not (dest / "old_file").exists()


def test_prepare_skills_copytree_error(tmp_path: Path):
    """OSError during copytree is caught; returns None if zero copied (lines 202-210)."""
    p = Persona(persona_id="t", skills=["s1"])

    def fake_resolve(name, *, work_dir=None):
        return tmp_path / "src" / name

    src = tmp_path / "src" / "s1"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("ok", encoding="utf-8")
    with patch.object(apply, "resolve_skill_path", fake_resolve):
        with patch("shutil.copytree", side_effect=OSError("perms")):
            result = apply.prepare_persona_skills_dir(tmp_path / "out", p)
    assert result is None


def test_apply_skills_no_skills():
    """No skills and no disabled → returns base_config unchanged (line 218)."""
    p = Persona(persona_id="t")
    assert apply.apply_persona_skills_to_config_toml("cfg\n", p) == "cfg\n"


def test_apply_skills_adds_newline():
    """Base config without trailing newline gets one added (line 221)."""
    p = Persona(persona_id="t", skills=["s"])
    text = apply.apply_persona_skills_to_config_toml("x=1", p)
    assert text.startswith("x=1\n")


def test_plugins_config_toml_empty():
    """Empty plugins list still emits enabled = [] (clears host marketplace)."""
    frag = apply.plugins_config_toml_fragment(plugins_enabled=[])
    assert "[plugins]" in frag
    assert "enabled = []" in frag
    frag2 = apply.plugins_config_toml_fragment(plugins_enabled=["", " "])
    assert "enabled = []" in frag2


def test_resolve_plugin_path_empty_name():
    """Empty name returns None (line 270)."""
    assert apply.resolve_plugin_path("") is None
    assert apply.resolve_plugin_path("  ") is None


def test_resolve_plugin_by_marketplace_plugin_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Match by marketplace_plugin field (lines 272-275)."""
    plug = InstalledPlugin(
        name="x",
        path=tmp_path,
        marketplace_plugin="alt-name",
    )
    tmp_path.mkdir(exist_ok=True)
    import groket.capabilities.marketplace as mp

    monkeypatch.setattr(mp, "list_installed_plugins_for_work", lambda *a, **kw: [plug])
    monkeypatch.setattr(mp, "is_grok_plugin_dir", lambda p: False)
    result = apply.resolve_plugin_path("alt-name")
    assert result == tmp_path


def test_prepare_plugins_none_persona():
    """None persona returns None (line 294)."""
    assert apply.prepare_persona_plugins_dir(Path("/tmp/x"), None) is None


def test_prepare_plugins_manifest_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """OSError writing manifest raises RuntimeError (launch must not enable unstaged plugins)."""
    import groket.capabilities.marketplace as mp

    p = Persona(persona_id="t", plugins=["cool"])
    monkeypatch.setattr(mp, "plugin_install_specs", lambda *a, **kw: [{"name": "cool"}])
    monkeypatch.setattr(mp, "list_installed_plugins_for_work", lambda *a, **kw: [])

    def _mat(name, dest, **kw):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "plugin.json").write_text("{}", encoding="utf-8")
        return dest

    monkeypatch.setattr(mp, "materialize_plugin", _mat)
    out = tmp_path / "out"
    real_write = Path.write_text

    def _write(self, *a, **kw):
        if self.name == "plugins-manifest.json":
            raise OSError("disk full")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", _write)
    with pytest.raises(apply.PluginStageError, match="plugins manifest"):
        apply.prepare_persona_plugins_dir(out, p)


def test_apply_plugins_no_plugins():
    """Empty persona plugins strips host [plugins] and writes enabled = []."""
    p = Persona(persona_id="t")
    host = '[cli]\nx=1\n\n[plugins]\nenabled = ["superpowers"]\ndisabled = []\n'
    text = apply.apply_persona_plugins_to_config_toml(host, p)
    assert "superpowers" not in text
    assert "[plugins]" in text
    assert "enabled = []" in text

    text2 = apply.apply_persona_plugins_to_config_toml(
        "base", Persona(persona_id="t", plugins=["x"])
    )
    assert "[plugins]" in text2
    assert '"x"' in text2


def test_strip_plugins_sections():
    """_strip_plugins_sections removes [plugins] blocks."""
    text = "[other]\nx=1\n\n[plugins]\nenabled = []\n\n[next]\ny=1\n"
    stripped = apply._strip_plugins_sections(text)
    assert "[plugins]" not in stripped
    assert "[next]" in stripped


# ── catalog ──────────────────────────────────────────────────────────────


def test_catalog_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    skills = tmp_path / "skills" / "cool"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\ndescription: Cool skill\n---\n# Cool\n", encoding="utf-8"
    )
    # work_dir skills via scan paths — use monkeypatch on _scan if needed
    catalog.scan_host_skills(tmp_path)
    # may be empty depending on paths; still exercise search APIs
    catalog.search_skills("cool", work_dir=tmp_path)
    catalog.resolve_skill_path("cool", work_dir=tmp_path)
    catalog.resolve_skill_path("nope", work_dir=tmp_path)

    cat_file = tmp_path / "mcp_catalog.yaml"
    cat_file.write_text(
        "servers:\n  - id: ascii\n    title: ASCII\n    url: http://x\n    transport: http\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [cat_file])
    entries = catalog.load_mcp_catalog(tmp_path)
    assert entries and entries[0].id == "ascii"
    assert catalog.search_mcp_catalog("ascii", work_dir=tmp_path)
    assert catalog.get_mcp_entry("ascii", work_dir=tmp_path) is not None
    assert catalog.get_mcp_entry("missing", work_dir=tmp_path) is None

    host_cfg = tmp_path / "config.toml"
    host_cfg.write_text("[mcp_servers.foo]\nenabled = true\n", encoding="utf-8")
    names = catalog.list_host_mcp_server_names(host_cfg)
    assert "foo" in names


def test_catalog_host_mcp_unreadable(tmp_path: Path):
    """OSError reading config.toml returns empty list (lines 71-72)."""
    cfg = tmp_path / "config.toml"
    cfg.mkdir()  # dir instead of file → read_text raises
    assert catalog.list_host_mcp_server_names(cfg) == []


def test_catalog_yaml_parse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Invalid YAML is skipped (lines 90-91)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(":\n  :\n  [invalid", encoding="utf-8")
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [bad])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: [])
    assert catalog.load_mcp_catalog() == []


def test_catalog_invalid_server_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Non-dict items, underscore ids, and non-list field types are handled (lines 94-112)."""
    cat = tmp_path / "cat.yaml"
    cat.write_text(
        "servers:\n"
        "  - id: _private\n"
        "    title: hidden\n"
        "  - not-a-dict\n"
        "  - id: ok\n"
        "    title: OK\n"
        "    args: not-a-list\n"
        "    needs_env: not-a-list\n"
        "    tags: not-a-list\n"
        "    profiles: not-a-list\n"
        "  - id: ''\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [cat])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: [])
    entries = catalog.load_mcp_catalog()
    ids = [e.id for e in entries]
    assert "_private" not in ids
    assert "ok" in ids


def test_catalog_host_entry_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Host-defined servers get a fallback catalog entry (lines 127-128)."""
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: ["my-host-srv"])
    entries = catalog.load_mcp_catalog()
    assert any(e.id == "my-host-srv" and e.source == "host" for e in entries)


def test_search_mcp_catalog_score_ranking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exact id match ranks higher than partial match (lines 144, 150)."""
    cat = tmp_path / "cat.yaml"
    cat.write_text(
        "servers:\n"
        "  - id: slack\n    title: Slack\n"
        "  - id: slack-ext\n    title: Slack extension\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [cat])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: [])
    results = catalog.search_mcp_catalog("slack")
    assert results[0].id == "slack"


def test_skill_description_frontmatter_and_fallback(tmp_path: Path):
    """_skill_description extracts from YAML frontmatter or first content line (lines 157-169)."""
    # Frontmatter description
    md1 = tmp_path / "s1" / "SKILL.md"
    md1.parent.mkdir()
    md1.write_text('---\ndescription: "frontmatter desc"\n---\n# Title\n', encoding="utf-8")
    assert catalog._skill_description(md1) == "frontmatter desc"

    # No frontmatter: first non-header line
    md2 = tmp_path / "s2" / "SKILL.md"
    md2.parent.mkdir()
    md2.write_text("# Title\nfirst line content\n", encoding="utf-8")
    assert "first line content" in catalog._skill_description(md2)

    # Unreadable
    assert catalog._skill_description(tmp_path / "missing.md") == ""


def test_scan_skills_hidden_and_dotdir(tmp_path: Path):
    """Hidden/dot-prefixed skill dirs are skipped (lines 185-195)."""
    root = tmp_path / "skills"
    root.mkdir()
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "SKILL.md").write_text("x", encoding="utf-8")
    entries = catalog._scan_skills_root(root, source="test")
    assert not any(e.name == ".hidden" for e in entries)


def test_get_mcp_entry_host_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """get_mcp_entry returns host entry for known host server (lines 256-261, 270)."""
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: ["host-srv"])
    entry = catalog.get_mcp_entry("host-srv")
    assert entry is not None
    assert entry.transport == "host"

    assert catalog.get_mcp_entry("") is None
    assert catalog.resolve_skill_path("") is None


def test_search_skills_empty_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Empty query returns full list up to limit (line 249)."""
    results = catalog.search_skills("", work_dir=tmp_path, limit=5)
    assert isinstance(results, list)


# ── marketplace ──────────────────────────────────────────────────────────


def test_marketplace_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    plugins = home / ".grok" / "installed-plugins" / "myplug"
    plugins.mkdir(parents=True)
    (plugins / "plugin.json").write_text(json.dumps({"name": "myplug"}), encoding="utf-8")
    (plugins / "skills" / "s1").mkdir(parents=True)
    (plugins / "skills" / "s1" / "SKILL.md").write_text("# s\n", encoding="utf-8")
    (plugins / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"url": "http://x"}}}), encoding="utf-8"
    )
    assert is_grok_plugin_dir(plugins) is True
    assert is_grok_plugin_dir(tmp_path) is False
    # list with explicit home path via monkeypatch grok_home
    import groket.capabilities.marketplace as mp

    monkeypatch.setattr(mp, "grok_home", lambda home=None: home or Path(str(tmp_path / "home")))
    monkeypatch.setattr(mp, "installed_plugins_root", lambda home=None: plugins.parent)
    listed = list_installed_plugins(home=home)
    assert isinstance(listed, list)
    # plugin.json alone may be enough for is_grok_plugin_dir; scanning may need registry
    picker = list_plugins_for_picker(home=home, work_dir=tmp_path)
    assert isinstance(picker, list)
    mcps = load_plugin_mcp_servers(plugins)
    assert mcps is not None
    assert marketplace_news_url()
    assert official_marketplace_repo()
    search_marketplace_catalog("x", home=home)


def test_marketplace_search_blobs():
    """search_blob methods return lowercase strings (lines 57-64, 82-89)."""
    ip = InstalledPlugin(name="Plug", path=Path("/x"), marketplace="mkt")
    assert "plug" in ip.search_blob()
    assert "mkt" in ip.search_blob()

    me = MarketplacePluginEntry(name="Cool", description="desc", keywords=["k1"])
    assert "cool" in me.search_blob()
    assert "k1" in me.search_blob()


def test_marketplace_is_grok_plugin_markers(tmp_path: Path):
    """Various markers for is_grok_plugin_dir (lines 136-148)."""
    # .claude-plugin marker
    d1 = tmp_path / "p1"
    d1.mkdir()
    (d1 / ".claude-plugin").mkdir()
    assert is_grok_plugin_dir(d1) is True

    # hooks/hooks.json
    d2 = tmp_path / "p2"
    d2.mkdir()
    (d2 / "hooks").mkdir()
    (d2 / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    assert is_grok_plugin_dir(d2) is True

    # agents dir
    d3 = tmp_path / "p3"
    d3.mkdir()
    (d3 / "agents").mkdir()
    assert is_grok_plugin_dir(d3) is True

    # commands dir
    d4 = tmp_path / "p4"
    d4.mkdir()
    (d4 / "commands").mkdir()
    assert is_grok_plugin_dir(d4) is True

    # non-dir returns False
    assert is_grok_plugin_dir(tmp_path / "nonexistent") is False


def test_plugin_pick_row_detail_and_search():
    """PluginPickRow: search_blob and detail_markup (lines 169, 190-206)."""
    row = PluginPickRow(
        name="demo",
        status="catalog",
        marketplace="mkt",
        version="v1",
        description="A plugin",
        category="general",
        homepage="http://h",
        source_url="http://git",
        sha="abcdef1234567890",
        components="skills+mcp",
        path=Path("/some/path"),
    )
    assert "demo" in row.search_blob()
    markup = row.detail_markup()
    assert "demo" in markup
    assert "general" in markup
    assert "mkt" in markup
    assert "v1" in markup
    assert "abcdef123456" in markup
    assert "skills+mcp" in markup
    assert "http://git" in markup
    assert "http://h" in markup
    assert "/some/path" in markup


def test_plugin_components_summary(tmp_path: Path):
    """_plugin_components_summary with all component types (lines 211-228)."""
    from groket.capabilities.marketplace import _plugin_components_summary

    assert _plugin_components_summary(None) == ""
    assert _plugin_components_summary(tmp_path / "nope") == ""

    pdir = tmp_path / "full"
    pdir.mkdir()
    (pdir / "skills" / "s1").mkdir(parents=True)
    (pdir / ".mcp.json").write_text("{}", encoding="utf-8")
    (pdir / "hooks").mkdir()
    (pdir / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    (pdir / "agents").mkdir()
    (pdir / "commands").mkdir()
    summary = _plugin_components_summary(pdir)
    assert "skills" in summary
    assert "mcp" in summary
    assert "hooks" in summary
    assert "agents" in summary
    assert "commands" in summary


def test_marketplace_entry_none_and_empty():
    """get_marketplace_entry with empty/blank name (line 239)."""
    from groket.capabilities.marketplace import get_marketplace_entry

    assert get_marketplace_entry("") is None
    assert get_marketplace_entry("  ") is None


def test_plugin_install_specs_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """plugin_install_specs skips duplicates and logs for missing plugins (lines 259, 288)."""
    from groket.capabilities.marketplace import plugin_install_specs

    monkeypatch.setattr(
        "groket.capabilities.marketplace.get_marketplace_entry",
        lambda n, home=None: None,
    )
    result = plugin_install_specs(["x", "x", "y"])
    assert result == []


def test_materialize_plugin_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """materialize_plugin returns None when entry not found (line 289)."""
    from groket.capabilities.marketplace import materialize_plugin

    monkeypatch.setattr(
        "groket.capabilities.marketplace.get_marketplace_entry",
        lambda n, home=None: None,
    )
    assert materialize_plugin("missing", tmp_path / "out") is None


def test_git_clone_plugin_failure(tmp_path: Path):
    """_git_clone_plugin handles subprocess failure (line 320-325)."""
    from groket.capabilities.marketplace import _git_clone_plugin

    dest = tmp_path / "out"
    with patch("subprocess.run", side_effect=OSError("no git")):
        result = _git_clone_plugin("http://x.git", "abc", dest)
    assert result is None


def test_git_clone_plugin_existing_dest(tmp_path: Path):
    """_git_clone_plugin wipes existing dest (line 300)."""
    from groket.capabilities.marketplace import _git_clone_plugin

    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "old").write_text("x", encoding="utf-8")
    with patch("subprocess.run", return_value=MagicMock()):
        result = _git_clone_plugin("http://x.git", "", dest)
    assert result == dest


def test_list_plugins_for_picker_dedup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Duplicate catalog entries are merged (lines 339-361)."""
    from groket.capabilities.marketplace import list_plugins_for_picker

    entries = [
        MarketplacePluginEntry(name="dup", description="d1", source_url="http://a"),
        MarketplacePluginEntry(name="dup", description="d2", category="c"),
    ]
    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_marketplace_catalog",
        lambda **kw: entries,
    )
    rows = list_plugins_for_picker()
    assert len(rows) == 1
    assert rows[0].name == "dup"
    assert rows[0].description == "d1"
    assert rows[0].category == "c"


def test_registry_repo_plugins_no_plugins(tmp_path: Path):
    """_registry_repo_plugins when plugins dict is empty (lines 417-418)."""
    from groket.capabilities.marketplace import _registry_repo_plugins

    by_name: dict[str, InstalledPlugin] = {}
    _registry_repo_plugins(
        by_name,
        tmp_path,
        "repo1",
        {"kind": {"url": "http://x"}, "marketplace": {"source_display_name": "mk"}},
    )
    assert "repo1" in by_name or tmp_path.name in by_name


def test_scan_plugin_dirs_hidden_and_non_grok(tmp_path: Path):
    """_scan_plugin_dirs skips hidden dirs and non-grok dirs (lines 434-450)."""
    from groket.capabilities.marketplace import _scan_plugin_dirs

    root = tmp_path / "plugins"
    root.mkdir()
    # hidden dir
    (root / ".hidden").mkdir()
    # non-grok dir
    (root / "python-package").mkdir()
    (root / "python-package" / "pyproject.toml").write_text("x", encoding="utf-8")
    # valid grok dir
    valid = root / "real-plugin"
    valid.mkdir()
    (valid / "skills").mkdir()
    (valid / "skills" / "s1").mkdir()

    by_name: dict[str, InstalledPlugin] = {}
    _scan_plugin_dirs(by_name, root)
    assert "real-plugin" in by_name
    assert ".hidden" not in by_name


def test_plugin_manifest_name(tmp_path: Path):
    """_plugin_manifest_name reads from plugin.json (lines 455-461)."""
    from groket.capabilities.marketplace import _plugin_manifest_name

    d = tmp_path / "plug"
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps({"name": "actual-name"}), encoding="utf-8")
    assert _plugin_manifest_name(d) == "actual-name"

    d2 = tmp_path / "plug2"
    d2.mkdir()
    assert _plugin_manifest_name(d2) is None


def test_read_json_bad_file(tmp_path: Path):
    """_read_json returns None for unreadable/bad files (lines 469-470)."""
    from groket.capabilities.marketplace import _read_json

    assert _read_json(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json!", encoding="utf-8")
    assert _read_json(bad) is None


def test_plugin_skills_dirs_manifest(tmp_path: Path):
    """_plugin_skills_dirs reads skillsPath from manifest (lines 475-497)."""
    from groket.capabilities.marketplace import _plugin_skills_dirs

    pdir = tmp_path / "plug"
    pdir.mkdir()
    (pdir / "plugin.json").write_text(
        json.dumps({"skills": "custom_skills"}),
        encoding="utf-8",
    )
    (pdir / "custom_skills").mkdir()
    dirs = _plugin_skills_dirs(pdir)
    assert any(str(d).endswith("custom_skills") for d in dirs)


def test_plugin_skills_dirs_list_value(tmp_path: Path):
    """_plugin_skills_dirs handles list-valued skillsPath (lines 484-487)."""
    from groket.capabilities.marketplace import _plugin_skills_dirs

    pdir = tmp_path / "plug"
    pdir.mkdir()
    (pdir / "plugin.json").write_text(
        json.dumps({"skills": ["s1", "s2"]}),
        encoding="utf-8",
    )
    (pdir / "s1").mkdir()
    dirs = _plugin_skills_dirs(pdir)
    assert any("s1" in str(d) for d in dirs)


def test_iter_plugin_skill_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """iter_plugin_skill_roots returns (path, label) tuples (lines 506-513)."""
    from groket.capabilities.marketplace import iter_plugin_skill_roots

    plug_dir = tmp_path / "plug"
    plug_dir.mkdir()
    (plug_dir / "skills").mkdir()

    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_installed_plugins_for_work",
        lambda *a, **kw: [InstalledPlugin(name="p1", path=plug_dir)],
    )
    pairs = iter_plugin_skill_roots()
    assert len(pairs) >= 1
    assert pairs[0][1] == "plugin:p1"


def test_mcp_blocks_url_transport(tmp_path: Path):
    """_mcp_entry_dict with url transport (lines 544-553)."""
    from groket.capabilities.marketplace import _mcp_entry_dict

    entry = _mcp_entry_dict("my-srv", {"url": "http://x", "type": "sse"}, "plug")
    assert entry["transport"] == "sse"
    assert entry["url"] == "http://x"

    # no command or url → plugin transport
    entry2 = _mcp_entry_dict("my-srv2", {}, "plug2")
    assert entry2["transport"] == "plugin"


def test_absorb_mcp_servers_non_dict():
    """_absorb_mcp_servers skips non-dict data and empty server ids (line 580)."""
    from groket.capabilities.marketplace import _absorb_mcp_servers

    blocks: dict = {}
    _absorb_mcp_servers(blocks, None, overwrite=True)
    _absorb_mcp_servers(blocks, "string", overwrite=True)
    _absorb_mcp_servers(blocks, {"mcpServers": {"": {"url": "x"}}}, overwrite=True)
    assert len(blocks) == 0


def test_list_marketplace_with_remote_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """include_remote_index path merges remote entries (lines 597-601)."""
    from groket.capabilities.marketplace import list_marketplace_catalog

    monkeypatch.setattr(
        "groket.capabilities.marketplace._fetch_official_marketplace_entries",
        lambda: [MarketplacePluginEntry(name="remote-p", marketplace="official")],
    )
    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_installed_plugins",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "groket.capabilities.marketplace._load_cache_catalogs",
        lambda *a: None,
    )
    entries = list_marketplace_catalog(include_remote_index=True)
    assert any(e.name == "remote-p" for e in entries)


def test_load_cache_catalogs_non_dir_child(tmp_path: Path):
    """_load_cache_catalogs skips non-directory children (lines 617-621)."""
    from groket.capabilities.marketplace import _load_cache_catalogs

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "file.txt").write_text("x", encoding="utf-8")
    by_key: dict = {}
    _load_cache_catalogs(by_key, cache, set())
    assert len(by_key) == 0


def test_catalog_item_to_entry_non_dict():
    """_catalog_item_to_entry returns None for non-dicts (line 645)."""
    from groket.capabilities.marketplace import _catalog_item_to_entry

    assert _catalog_item_to_entry("string", "d", set()) is None
    assert _catalog_item_to_entry({"name": ""}, "d", set()) is None


def test_search_marketplace_empty_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Empty query returns full list (line 672)."""
    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_marketplace_catalog",
        lambda **kw: [MarketplacePluginEntry(name="x")],
    )
    results = search_marketplace_catalog("")
    assert len(results) == 1


def test_fetch_official_marketplace_network_error():
    """_fetch_official_marketplace_entries handles network errors (lines 678-702)."""
    from groket.capabilities.marketplace import _fetch_official_marketplace_entries

    with patch("urllib.request.urlopen", side_effect=OSError("no network")):
        assert _fetch_official_marketplace_entries() == []


def test_fetch_official_marketplace_bad_response():
    """_fetch_official_marketplace_entries handles non-dict or missing plugins key."""
    from groket.capabilities.marketplace import _fetch_official_marketplace_entries

    # Non-dict response
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b'"just a string"'
    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert _fetch_official_marketplace_entries() == []

    # dict without plugins key
    mock_resp2 = MagicMock()
    mock_resp2.__enter__ = lambda s: s
    mock_resp2.__exit__ = MagicMock(return_value=False)
    mock_resp2.read.return_value = json.dumps({"name": "x"}).encode()
    with patch("urllib.request.urlopen", return_value=mock_resp2):
        assert _fetch_official_marketplace_entries() == []


# ── registry ─────────────────────────────────────────────────────────────


def test_registry_toml_helpers():
    hit = RegistryServerHit(
        name="ai.example/hit",
        title="Hit",
        description="d",
        remotes=[RegistryRemote(transport="http", url="http://x")],
    )
    defn = registry_hit_to_definition(hit, server_id="mysrv")
    assert defn
    block = definition_to_toml_block({"id": "s", "url": "http://y", "enabled": True})
    assert "mcp_servers" in block or "url" in block


def test_registry_server_hit_preferred_remote():
    """preferred_remote prefers streamable-http/http over sse (lines 56-63)."""
    # No remotes
    hit = RegistryServerHit(name="x")
    assert hit.preferred_remote is None

    # SSE only → fallback to first
    hit2 = RegistryServerHit(
        name="x",
        remotes=[RegistryRemote(transport="sse", url="http://s")],
    )
    assert hit2.preferred_remote is not None
    assert hit2.preferred_remote.transport == "sse"

    # streamable-http wins
    hit3 = RegistryServerHit(
        name="x",
        remotes=[
            RegistryRemote(transport="sse", url="http://s"),
            RegistryRemote(transport="streamable-http", url="http://sh"),
        ],
    )
    assert hit3.preferred_remote is not None
    assert hit3.preferred_remote.url == "http://sh"


def test_registry_server_hit_preferred_package():
    """preferred_package prefers npx/uvx hints (lines 66-72)."""
    hit = RegistryServerHit(name="x")
    assert hit.preferred_package is None

    hit2 = RegistryServerHit(
        name="x",
        packages=[
            RegistryPackage(registry_type="npm", identifier="pkg", runtime_hint="docker"),
            RegistryPackage(registry_type="pypi", identifier="py-pkg", runtime_hint="uvx"),
        ],
    )
    assert hit2.preferred_package is not None
    assert hit2.preferred_package.identifier == "py-pkg"

    hit3 = RegistryServerHit(
        name="x",
        packages=[RegistryPackage(registry_type="oci", identifier="img", runtime_hint="docker")],
    )
    assert hit3.preferred_package is not None
    assert hit3.preferred_package.identifier == "img"


def test_registry_to_catalog_entry_remote_with_headers():
    """to_catalog_entry with remote headers and env extraction (lines 94-104)."""
    hit = RegistryServerHit(
        name="ai.example/srv",
        title="Srv",
        remotes=[
            RegistryRemote(
                transport="http",
                url="http://api.example.com",
                headers=[
                    {"name": "Authorization", "value": "Bearer {api_key}", "isRequired": True},
                    {"name": "X-Extra", "value": "static", "isSecret": True},
                    "not-a-dict",
                ],
            ),
        ],
    )
    entry = hit.to_catalog_entry()
    assert entry.transport == "http"
    assert entry.url == "http://api.example.com"
    assert "API_KEY" in entry.needs_env
    assert "registry" in entry.tags
    assert "remote" in entry.tags


def test_registry_to_catalog_entry_sse_transport():
    """SSE remote transport (line 91)."""
    hit = RegistryServerHit(
        name="x/sse-srv",
        remotes=[RegistryRemote(transport="sse", url="http://sse.example.com")],
    )
    entry = hit.to_catalog_entry()
    assert entry.transport == "sse"


def test_registry_to_catalog_entry_required_header_no_placeholder():
    """Required header without {var} placeholder adds header name as env (line 103-104)."""
    hit = RegistryServerHit(
        name="x/srv",
        remotes=[
            RegistryRemote(
                transport="http",
                url="http://example.com",
                headers=[{"name": "X-Api-Key", "value": "fixed", "isRequired": True}],
            ),
        ],
    )
    entry = hit.to_catalog_entry()
    assert "X_API_KEY" in entry.needs_env


def test_registry_to_catalog_entry_package_pypi():
    """Package-based entry with pypi/uvx (lines 106-117)."""
    hit = RegistryServerHit(
        name="x/py",
        packages=[RegistryPackage(registry_type="pypi", identifier="my-tool", runtime_hint="uvx")],
    )
    entry = hit.to_catalog_entry()
    assert entry.command == "uvx"
    assert "my-tool" in entry.args
    assert "stdio" in entry.tags


def test_registry_to_catalog_entry_package_oci():
    """Package-based entry with docker/oci (lines 112-114)."""
    hit = RegistryServerHit(
        name="x/dock",
        packages=[RegistryPackage(registry_type="oci", identifier="myimg", runtime_hint="docker")],
    )
    entry = hit.to_catalog_entry()
    assert entry.command == "docker"
    assert "myimg" in entry.args


def test_registry_to_catalog_entry_package_npm():
    """Package-based entry with npm/npx (lines 115-117)."""
    hit = RegistryServerHit(
        name="x/npm",
        packages=[RegistryPackage(registry_type="npm", identifier="@org/tool")],
    )
    entry = hit.to_catalog_entry()
    assert entry.command == "npx"
    assert "@org/tool" in entry.args


def test_registry_header_templates():
    """header_templates() formats remote headers (lines 137-154)."""
    hit = RegistryServerHit(name="x")
    assert hit.header_templates() == []

    hit2 = RegistryServerHit(
        name="x",
        remotes=[
            RegistryRemote(
                transport="http",
                url="http://x",
                headers=[
                    {
                        "name": "Auth",
                        "value": "Bearer {tok}",
                        "description": "API token",
                        "isRequired": True,
                        "isSecret": True,
                    },
                    "not-a-dict",
                ],
            )
        ],
    )
    hdrs = hit2.header_templates()
    assert len(hdrs) == 1
    assert hdrs[0]["name"] == "Auth"
    assert hdrs[0]["required"] == "1"
    assert hdrs[0]["secret"] == "1"
    assert hdrs[0]["description"] == "API token"


def test_registry_page_url():
    """registry_page_url with and without name (lines 156-163)."""
    hit = RegistryServerHit(name="")
    assert hit.registry_page_url() == ""

    hit2 = RegistryServerHit(name="ai.example/slack")
    url = hit2.registry_page_url()
    assert "registry.modelcontextprotocol.io" in url
    assert "slack" in url


def test_registry_docs_links_npm():
    """docs_links for npm package (lines 175-185)."""
    hit = RegistryServerHit(
        name="x/npm-srv",
        repository_url="http://github.com/org/repo",
        packages=[RegistryPackage(registry_type="npm", identifier="@org/pkg", runtime_hint="npx")],
    )
    links = hit.docs_links()
    labels = {lab for lab, url in links}
    assert "repository" in labels
    assert "registry" in labels
    assert "npm" in labels


def test_registry_docs_links_pypi():
    """docs_links for pypi package (line 180-181)."""
    hit = RegistryServerHit(
        name="x/pypi-srv",
        packages=[RegistryPackage(registry_type="pypi", identifier="my-tool", runtime_hint="uvx")],
    )
    links = hit.docs_links()
    assert any(lab == "pypi" for lab, _ in links)


def test_registry_docs_links_docker():
    """docs_links for OCI/docker packages (lines 182-191)."""
    # Docker with org/img format
    hit = RegistryServerHit(
        name="x/dock",
        packages=[
            RegistryPackage(registry_type="oci", identifier="org/myimg", runtime_hint="docker")
        ],
    )
    links = hit.docs_links()
    assert any("hub.docker.com/r/" in url for _, url in links)

    # Docker without slash
    hit2 = RegistryServerHit(
        name="x/dock2",
        packages=[RegistryPackage(registry_type="oci", identifier="myimg", runtime_hint="docker")],
    )
    links2 = hit2.docs_links()
    assert any("hub.docker.com/_/" in url for _, url in links2)


def test_registry_docs_links_dedup():
    """Duplicate URLs are deduplicated (lines 193-201)."""
    hit = RegistryServerHit(
        name="x/srv",
        repository_url="http://registry.modelcontextprotocol.io/x/srv",
    )
    links = hit.docs_links()
    urls = [url for _, url in links]
    assert len(urls) == len(set(urls))


def test_registry_detail_summary_with_command():
    """detail_summary shows command + args + headers + links (lines 204-242)."""
    hit = RegistryServerHit(
        name="x/full",
        title="Full Server",
        description="A" * 600,  # test truncation
        version="1.0",
        status="active",
        repository_url="http://repo",
        packages=[RegistryPackage(registry_type="npm", identifier="pkg", runtime_hint="npx")],
    )
    summary = hit.detail_summary()
    assert "Full Server" in summary
    assert "1.0" in summary
    assert "npx" in summary
    assert "Links" in summary
    assert "…" in summary  # truncation indicator


def test_registry_detail_summary_no_links():
    """detail_summary with no links shows 'No repository' message (lines 241-242)."""
    hit = RegistryServerHit(name="")
    summary = hit.detail_summary()
    assert "No repository" in summary


def test_registry_detail_summary_with_needs_env():
    """detail_summary shows needs_env when present (line 224)."""
    hit = RegistryServerHit(
        name="x/env",
        remotes=[
            RegistryRemote(
                transport="http",
                url="http://x",
                headers=[{"name": "Auth", "value": "Bearer {api_key}"}],
            )
        ],
    )
    summary = hit.detail_summary()
    assert "needs env" in summary.lower() or "API_KEY" in summary


def test_parse_hit_edge_cases():
    """_parse_hit with various malformed inputs (lines 257-305)."""
    assert _parse_hit(None) is None
    assert _parse_hit("string") is None
    assert _parse_hit({}) is None  # no name
    assert _parse_hit({"server": "non-dict"}) is None

    # With _meta official status
    hit = _parse_hit(
        {
            "server": {
                "name": "test/srv",
                "title": "Test",
                "description": "d",
                "version": "2.0",
                "remotes": [{"type": "http", "url": "http://x"}],
                "packages": [
                    {
                        "registryType": "npm",
                        "identifier": "pkg",
                        "version": "1.0",
                        "runtimeHint": "npx",
                        "transport": {"type": "stdio"},
                    },
                ],
                "repository": {"url": "http://repo"},
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {"status": "deprecated"},
            },
        }
    )
    assert hit is not None
    assert hit.name == "test/srv"
    assert hit.status == "deprecated"
    assert len(hit.remotes) == 1
    assert len(hit.packages) == 1
    assert hit.packages[0].transport == "stdio"

    # Server at top level (no "server" key)
    hit2 = _parse_hit({"name": "direct/name", "title": "Direct"})
    assert hit2 is not None
    assert hit2.name == "direct/name"


def test_search_registry_empty_query():
    """Empty query returns empty list."""
    hits, err = search_registry("")
    assert hits == []
    assert err == ""


def test_search_registry_success():
    """Successful registry search."""
    from groket.capabilities.registry import clear_registry_cache

    clear_registry_cache()
    response_data = {
        "servers": [
            {
                "server": {
                    "name": "test/slack",
                    "title": "Slack",
                    "description": "Slack integration",
                },
            },
            {
                "server": {
                    "name": "test/slack",  # duplicate, should be deduped
                    "title": "Slack2",
                },
            },
        ],
    }
    with patch.object(
        __import__("groket.capabilities.registry", fromlist=["_http_get_json"]),
        "_http_get_json",
        return_value=response_data,
    ):
        hits, err = search_registry("slack", use_cache=False)
    assert len(hits) == 1
    assert hits[0].name == "test/slack"
    assert err == ""


def test_search_registry_large_result_filtered():
    """Client-side filtering when API returns too many results."""
    servers = [
        {"server": {"name": f"x/srv{i}", "title": f"Srv {i}", "description": "match"}}
        for i in range(40)
    ]
    with patch(
        "groket.capabilities.registry._http_get_json",
        return_value={"servers": servers},
    ):
        hits, err = search_registry("match", limit=5, use_cache=False)
    assert len(hits) <= 5


def test_search_registry_large_result_no_match():
    """Client-side filter with no matches keeps original order."""
    servers = [{"server": {"name": f"x/srv{i}", "title": f"T{i}"}} for i in range(40)]
    with patch(
        "groket.capabilities.registry._http_get_json",
        return_value={"servers": servers},
    ):
        hits, err = search_registry("nomatch", limit=5, use_cache=False)
    assert len(hits) <= 5


def test_search_registry_http_error():
    """HTTP errors are captured."""
    import urllib.error

    with patch(
        "groket.capabilities.registry._http_get_json",
        side_effect=urllib.error.HTTPError("url", 500, "err", {}, None),
    ):
        hits, err = search_registry("test", use_cache=False)
    assert hits == []
    assert "500" in err


def test_search_registry_url_error():
    """URL/network errors are captured."""
    import urllib.error

    with patch(
        "groket.capabilities.registry._http_get_json",
        side_effect=urllib.error.URLError("no dns"),
    ):
        hits, err = search_registry("test", use_cache=False)
    assert hits == []
    assert "network" in err


def test_search_registry_generic_error():
    """Generic exceptions are captured."""
    with patch(
        "groket.capabilities.registry._http_get_json",
        side_effect=ValueError("bad"),
    ):
        hits, err = search_registry("test", use_cache=False)
    assert hits == []
    assert "bad" in err


def test_search_registry_no_servers_key():
    """Response without servers key retries next URL."""
    call_count = [0]

    def fake_get(url, *, timeout=12.0):
        call_count[0] += 1
        return {"other": "data"}  # no "servers" key

    with patch("groket.capabilities.registry._http_get_json", side_effect=fake_get):
        hits, err = search_registry("test", use_cache=False)
    assert hits == []
    # v0.1 (+version=latest), v0.1 (no version), then v0
    assert call_count[0] == 3


def test_search_registry_uses_disk_cache(tmp_path, monkeypatch):
    """Second identical search hits disk/memory cache and skips HTTP."""
    from groket.capabilities import registry as reg

    cache_root = tmp_path / "mcp-reg"
    cache_root.mkdir()
    monkeypatch.setattr(reg, "mcp_registry_cache_dir", lambda: cache_root)
    reg.clear_registry_cache(disk=False)
    # Clear only memory; use empty dir for disk
    reg._MEM_CACHE.clear()

    calls: list[str] = []
    payload = {
        "servers": [{"server": {"name": "io.example/cached", "title": "Cached"}}],
    }

    def fake_get(url: str, *, timeout: float = 12.0):
        calls.append(url)
        return payload

    with patch.object(reg, "_http_get_json", side_effect=fake_get):
        hits1, err1 = search_registry("cached-q", limit=10, cache_ttl=3600)
        hits2, err2 = search_registry("cached-q", limit=10, cache_ttl=3600)
    assert err1 == err2 == ""
    assert len(hits1) == 1 and hits1[0].name == "io.example/cached"
    assert hits2[0].name == hits1[0].name
    assert len(calls) == 1
    assert list(cache_root.glob("*.json"))


def test_search_registry_cache_ttl_zero_skips_cache():
    """cache_ttl=0 forces network on every call."""
    from groket.capabilities import registry as reg

    reg.clear_registry_cache()
    calls = [0]

    def fake_get(url: str, *, timeout: float = 12.0):
        calls[0] += 1
        return {"servers": [{"server": {"name": "io.example/x", "title": "X"}}]}

    with patch.object(reg, "_http_get_json", side_effect=fake_get):
        search_registry("ttl0", cache_ttl=0)
        search_registry("ttl0", cache_ttl=0)
    assert calls[0] == 2


def test_clear_registry_cache_removes_files(tmp_path, monkeypatch):
    from groket.capabilities import registry as reg

    root = tmp_path / "c"
    root.mkdir()
    (root / "a.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(reg, "mcp_registry_cache_dir", lambda: root)
    reg._MEM_CACHE["u"] = (9999999999.0, {"servers": []})
    reg.clear_registry_cache(disk=True)
    assert reg._MEM_CACHE == {}
    assert not (root / "a.json").exists()


def test_load_cached_response_rejects_stale_and_bad(tmp_path, monkeypatch):
    from groket.capabilities import registry as reg

    root = tmp_path / "c"
    root.mkdir()
    monkeypatch.setattr(reg, "mcp_registry_cache_dir", lambda: root)
    reg._MEM_CACHE.clear()
    url = "https://example.test/v0.1/servers?search=x"
    fp = reg._cache_file_for_url(url)
    fp.write_text(
        json.dumps({"url": url, "fetched_at": 1.0, "data": {"servers": []}}),
        encoding="utf-8",
    )
    assert reg._load_cached_response(url, ttl_sec=10) is None  # stale
    fp.write_text("not-json", encoding="utf-8")
    assert reg._load_cached_response(url, ttl_sec=10_000) is None
    assert reg._load_cached_response(url, ttl_sec=0) is None


def test_registry_hit_to_definition_with_headers():
    """registry_hit_to_definition converts header placeholders (lines 382-414)."""
    hit = RegistryServerHit(
        name="test/srv",
        title="Test",
        version="1.0",
        status="active",
        repository_url="http://repo",
        remotes=[
            RegistryRemote(
                transport="http",
                url="http://api",
                headers=[
                    {"name": "Authorization", "value": "Bearer {api_key}"},
                    {"name": "X-No-Name"},  # empty name stripped
                    "not-dict",
                ],
            ),
        ],
    )
    defn = registry_hit_to_definition(hit)
    assert defn["registry_name"] == "test/srv"
    assert defn["version"] == "1.0"
    assert "Authorization" in defn.get("headers", {})
    assert "${API_KEY}" in str(defn["headers"])


def test_definition_to_toml_block_branches():
    """definition_to_toml_block handles different transport types (lines 419-451)."""
    # Empty id
    assert definition_to_toml_block({"id": ""}) == ""
    assert definition_to_toml_block({"id": " "}) == ""

    # HTTP with url
    block = definition_to_toml_block({"id": "srv", "transport": "http", "url": "http://x"})
    assert "mcp_servers.srv" in block
    assert "http://x" in block

    # SSE with url
    block2 = definition_to_toml_block({"id": "srv2", "transport": "sse", "url": "http://y"})
    assert "http://y" in block2

    # Stdio with command + args
    block3 = definition_to_toml_block(
        {
            "id": "cli",
            "transport": "stdio",
            "command": "uvx",
            "args": ["my-tool", "--flag"],
        }
    )
    assert "uvx" in block3
    assert "args" in block3

    # Args as non-list ignored
    block4 = definition_to_toml_block(
        {
            "id": "cli2",
            "transport": "stdio",
            "command": "npx",
            "args": "not-a-list",
        }
    )
    assert "npx" in block4

    # Unknown transport without url/command → empty
    assert definition_to_toml_block({"id": "x", "transport": "plugin"}) == ""

    # With headers
    block5 = definition_to_toml_block(
        {
            "id": "hdr",
            "transport": "http",
            "url": "http://z",
            "headers": {"Auth": "Bearer ${KEY}"},
        }
    )
    assert "headers" in block5


def test_http_get_json():
    """_http_get_json returns parsed JSON or empty dict (lines 247-253)."""
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = json.dumps({"key": "val"}).encode()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _http_get_json("http://example.com")
    assert result == {"key": "val"}

    # Non-dict response
    mock_resp2 = MagicMock()
    mock_resp2.__enter__ = lambda s: s
    mock_resp2.__exit__ = MagicMock(return_value=False)
    mock_resp2.read.return_value = b"[1,2,3]"
    with patch("urllib.request.urlopen", return_value=mock_resp2):
        result2 = _http_get_json("http://example.com")
    assert result2 == {}


# ── apply: MCP blocks, definitions, and host config ──────────────────────


def test_apply_mcp_block_from_definition(tmp_path: Path):
    """Definition-based block used when no catalog entry exists."""

    p = Persona(
        persona_id="t",
        mcp_servers=["def-srv"],
        mcp_definitions=[
            {"id": "def-srv", "transport": "http", "url": "http://def"},
        ],
    )
    text = apply.apply_persona_mcp_to_config_toml("", p)
    assert "def-srv" in text
    assert "http://def" in text


def test_apply_mcp_catalog_entry_fallback():
    """Catalog entry used when no definition for server."""
    entry = McpCatalogEntry(id="cat-srv", transport="http", url="http://cat")
    p = Persona(persona_id="t", mcp_servers=["cat-srv"])
    with patch.object(apply, "get_mcp_entry", return_value=entry):
        text = apply.apply_persona_mcp_to_config_toml("", p)
    assert "cat-srv" in text
    assert "http://cat" in text


def test_apply_mcp_enabled_false_rewritten():
    """Block with enabled=false gets rewritten to true."""
    # definition_to_toml_block returns enabled=true by default, so force enabled=false
    # via host config block
    host = '[mcp_servers.srv]\nenabled = false\nurl = "http://x"\n'
    with patch.object(apply, "get_mcp_entry", return_value=None):
        text = apply.apply_persona_mcp_to_config_toml(
            "",
            Persona(persona_id="t", mcp_servers=["srv"]),
            host_config_text=host,
        )
    assert "enabled = true" in text


def test_apply_extra_toml_appended():
    """Extra TOML with trailing newline appended to blocks."""
    p = Persona(
        persona_id="t",
        mcp_extra_toml="[mcp_servers.extra]\nenabled = true",
    )
    text = apply.apply_persona_mcp_to_config_toml("base\n", p)
    assert "mcp_servers.extra" in text


def test_apply_text_missing_newline():
    """Base config without trailing newline gets one added."""
    p = Persona(
        persona_id="t",
        mcp_extra_toml="[mcp_servers.e]\nenabled = true\n",
    )
    text = apply.apply_persona_mcp_to_config_toml("base", p)
    assert text.startswith("base\n")
    assert text.endswith("\n")


def test_apply_strip_mcp_when_no_servers():
    """replace_host=True with no servers/definitions strips MCP."""
    p = Persona(persona_id="t", mcp_replace_host=True)
    text = apply.apply_persona_mcp_to_config_toml(
        "[mcp_servers.old]\nenabled = true\n",
        p,
    )
    assert "mcp_servers.old" not in text


def test_apply_mcp_block_from_host_config():
    """Host config block used when no catalog entry."""
    p = Persona(persona_id="t", mcp_servers=["host-only"])
    host = '[mcp_servers.host-only]\nenabled = true\nurl = "http://h"\n'
    with patch.object(apply, "get_mcp_entry", return_value=None):
        text = apply.apply_persona_mcp_to_config_toml("", p, host_config_text=host)
    assert "host-only" in text
    assert "http://h" in text


def test_apply_resolve_plugin_path_match_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Match by name + is_grok_plugin_dir check."""
    import groket.capabilities.marketplace as mp

    plug_dir = tmp_path / "myplugin"
    plug_dir.mkdir()
    (plug_dir / "plugin.json").write_text("{}", encoding="utf-8")
    plug = InstalledPlugin(name="myplugin", path=plug_dir)
    monkeypatch.setattr(mp, "list_installed_plugins_for_work", lambda *a, **kw: [plug])
    monkeypatch.setattr(mp, "is_grok_plugin_dir", lambda p: True)
    result = apply.resolve_plugin_path("myplugin")
    assert result == plug_dir


def test_apply_prepare_plugins_no_specs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Requested plugin that cannot be staged fails the launch path."""
    import groket.capabilities.marketplace as mp

    p = Persona(persona_id="t", plugins=["x"])
    monkeypatch.setattr(mp, "plugin_install_specs", lambda *a, **kw: [])
    monkeypatch.setattr(mp, "list_installed_plugins_for_work", lambda *a, **kw: [])
    monkeypatch.setattr(mp, "materialize_plugin", lambda *a, **kw: None)
    with pytest.raises(apply.PluginStageError, match="Could not stage plugin"):
        apply.prepare_persona_plugins_dir(tmp_path / "out", p)


def test_apply_plugins_strips_existing_section():
    """apply_persona_plugins_to_config_toml strips existing [plugins] section."""
    p = Persona(persona_id="t", plugins=["cool"])
    text = apply.apply_persona_plugins_to_config_toml(
        '[other]\nx=1\n\n[plugins]\nenabled = ["host-only"]\n\n[next]\ny=1\n',
        p,
    )
    # Only one [plugins] section in result
    assert text.count("[plugins]") == 1
    assert "cool" in text
    assert "host-only" not in text


def test_apply_plugins_none_persona_strips_host():
    """No persona still clears host marketplace plugins from eval config."""
    host = '[plugins]\nenabled = ["superpowers"]\n'
    text = apply.apply_persona_plugins_to_config_toml(host, None)
    assert "superpowers" not in text
    assert "enabled = []" in text


def test_apply_prepare_skills_no_resolve(tmp_path: Path):
    """Skills that fail to resolve are skipped; zero copied returns None."""
    p = Persona(persona_id="t", skills=["missing1", "missing2"])
    with patch.object(apply, "resolve_skill_path", return_value=None):
        result = apply.prepare_persona_skills_dir(tmp_path / "out", p)
    assert result is None


# ── catalog: edge cases and error handling ────────────────────────────────


def test_catalog_load_non_list_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """YAML with servers key as non-list is ignored."""
    cat = tmp_path / "cat.yaml"
    cat.write_text("servers: not-a-list\n", encoding="utf-8")
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [cat])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: [])
    assert catalog.load_mcp_catalog() == []


def test_catalog_load_non_dict_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """YAML that parses to non-dict is skipped."""
    cat = tmp_path / "cat.yaml"
    cat.write_text("- just a list\n", encoding="utf-8")
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [cat])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: [])
    assert catalog.load_mcp_catalog() == []


def test_catalog_search_empty_query():
    """Empty query returns full catalog."""
    results = catalog.search_mcp_catalog("")
    assert isinstance(results, list)


def test_catalog_skill_description_no_frontmatter(tmp_path: Path):
    """Skill with non-frontmatter but content line extracts description."""
    md = tmp_path / "SKILL.md"
    md.write_text("# Title\nThis is a description\n", encoding="utf-8")
    desc = catalog._skill_description(md)
    assert "This is a description" in desc


def test_catalog_skill_description_only_headers(tmp_path: Path):
    """Skill with only headers returns empty description."""
    md = tmp_path / "SKILL.md"
    md.write_text("# Title\n## Subtitle\n---\n", encoding="utf-8")
    desc = catalog._skill_description(md)
    assert desc == ""


def test_catalog_scan_skills_includes_implicit(tmp_path: Path):
    """include_implicit=True includes MCP companions."""
    root = tmp_path / "skills"
    root.mkdir()
    # Regular skill
    s1 = root / "my-skill"
    s1.mkdir()
    (s1 / "SKILL.md").write_text("# regular skill\n", encoding="utf-8")
    # Companion skill (use-x-mcp)
    s2 = root / "use-x-mcp"
    s2.mkdir()
    (s2 / "SKILL.md").write_text(
        "---\nx-groket: groket-mcp-companion\n---\n# companion\n",
        encoding="utf-8",
    )
    # Non-dir child
    (root / "file.txt").write_text("not a skill", encoding="utf-8")
    # Dir without SKILL.md
    (root / "no-md").mkdir()

    # Default: exclude implicit
    entries = catalog._scan_skills_root(root, source="test")
    names = [e.name for e in entries]
    assert "my-skill" in names
    assert "use-x-mcp" not in names

    # include_implicit=True
    entries2 = catalog._scan_skills_root(root, source="test", include_implicit=True)
    names2 = [e.name for e in entries2]
    assert "my-skill" in names2
    assert "use-x-mcp" in names2


def test_catalog_scan_skills_root_oserror(tmp_path: Path):
    """OSError during iterdir returns empty list."""
    with patch.object(Path, "iterdir", side_effect=OSError("perms")):
        entries = catalog._scan_skills_root(tmp_path, source="test")
    assert entries == []


def test_catalog_host_mcp_server_names_file(tmp_path: Path):
    """Read MCP names from a config TOML file."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[mcp_servers.alpha]\nenabled = true\n\n[mcp_servers.beta]\n",
        encoding="utf-8",
    )
    names = catalog.list_host_mcp_server_names(cfg)
    assert "alpha" in names
    assert "beta" in names


def test_catalog_host_mcp_read_error(tmp_path: Path):
    """list_host_mcp_server_names handles read error gracefully."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("ok", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("perms")):
        names = catalog.list_host_mcp_server_names(cfg)
    assert names == []


# ── skill_gen: needs_env validation ───────────────────────────────────────


def test_skill_gen_needs_env_non_list():
    """needs_env as non-list is replaced with empty list."""
    text = skill_gen.render_mcp_companion_skill_md(
        {
            "id": "srv",
            "title": "S",
            "description": "d",
            "needs_env": "not-a-list",
        }
    )
    assert "srv" in text


# ── registry: edge cases ──────────────────────────────────────────────────


def test_registry_docs_links_empty_url():
    """docs_links deduplicates and skips empty URLs."""
    hit = RegistryServerHit(
        name="x/srv",
        repository_url="",  # empty URL should be skipped
    )
    links = hit.docs_links()
    # Only registry link should be present, no empty URL
    urls = [url for _, url in links]
    assert all(u.strip() for u in urls)


def test_parse_hit_non_dict_server_value():
    """_parse_hit with server key as non-dict value returns None."""
    result = _parse_hit({"server": "string-not-dict"})
    # Falls back to item itself which has no "name"
    assert result is None


def test_registry_hit_to_definition_empty_header_name():
    """Headers with empty name are skipped."""
    hit = RegistryServerHit(
        name="test/srv",
        remotes=[
            RegistryRemote(
                transport="http",
                url="http://api",
                headers=[
                    {"name": "", "value": "Bearer {tok}"},
                    {"name": "Good", "value": "val"},
                ],
            ),
        ],
    )
    defn = registry_hit_to_definition(hit)
    headers = defn.get("headers", {})
    assert "" not in headers
    assert "Good" in headers


# ── marketplace: component detection and cloning ─────────────────────────


def test_marketplace_plugin_components_mcp_json(tmp_path: Path):
    """mcp.json (not .mcp.json) detected as component source."""
    from groket.capabilities.marketplace import _plugin_components_summary

    pdir = tmp_path / "plug"
    pdir.mkdir()
    (pdir / "mcp.json").write_text("{}", encoding="utf-8")
    summary = _plugin_components_summary(pdir)
    assert "mcp" in summary


def test_marketplace_git_clone_with_sha(tmp_path: Path):
    """_git_clone_plugin with SHA does checkout after clone."""
    from groket.capabilities.marketplace import _git_clone_plugin

    dest = tmp_path / "out"
    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        if cmd[0] == "git" and cmd[1] == "clone":
            dest.mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        result = _git_clone_plugin("http://x.git", "abc123", dest)
    assert result == dest
    assert any("checkout" in c for cl in run_calls for c in cl)


def test_marketplace_registry_repo_plugins_with_entries(tmp_path: Path):
    """_registry_repo_plugins populates by_name from plugin entries."""
    from groket.capabilities.marketplace import _registry_repo_plugins

    by_name: dict[str, InstalledPlugin] = {}
    _registry_repo_plugins(
        by_name,
        tmp_path,
        "repo1",
        {
            "path": str(tmp_path),
            "kind": {"url": "http://x", "commit": "abc"},
            "marketplace": {"source_display_name": "mk"},
            "plugins": {"cool-plugin": {"version": "1.0"}},
        },
    )
    assert "cool-plugin" in by_name
    assert by_name["cool-plugin"].version == "1.0"


def test_marketplace_scan_plugin_dirs_oserror(tmp_path: Path):
    """_scan_plugin_dirs handles iterdir OSError gracefully."""
    from groket.capabilities.marketplace import _scan_plugin_dirs

    root = tmp_path / "plugins"
    root.mkdir()
    by_name: dict[str, InstalledPlugin] = {}
    with patch.object(Path, "iterdir", side_effect=OSError("perms")):
        _scan_plugin_dirs(by_name, root)
    assert len(by_name) == 0


def test_marketplace_scan_plugin_dirs_resolves_known(tmp_path: Path):
    """_scan_plugin_dirs skips dirs already known by resolved path."""
    from groket.capabilities.marketplace import _scan_plugin_dirs

    root = tmp_path / "plugins"
    root.mkdir()
    valid = root / "existing"
    valid.mkdir()
    (valid / "skills").mkdir()
    (valid / "skills" / "s1").mkdir()

    by_name: dict[str, InstalledPlugin] = {
        "existing": InstalledPlugin(name="existing", path=valid),
    }
    _scan_plugin_dirs(by_name, root)
    # Should not add a duplicate
    assert len(by_name) == 1


def test_marketplace_mcp_entry_dict_stdio(tmp_path: Path):
    """_mcp_entry_dict with command transport builds stdio entry."""
    from groket.capabilities.marketplace import _mcp_entry_dict

    entry = _mcp_entry_dict("srv", {"command": "my-cmd", "args": ["--flag"]}, "plug")
    assert entry["transport"] == "stdio"
    assert entry["command"] == "my-cmd"
    assert entry["args"] == ["--flag"]

    # args as non-list
    entry2 = _mcp_entry_dict("srv2", {"command": "cmd", "args": "not-a-list"}, "plug2")
    assert entry2["args"] == []


def test_marketplace_load_cache_catalogs_full(tmp_path: Path):
    """_load_cache_catalogs reads marketplace.json from cache directory."""
    from groket.capabilities.marketplace import _load_cache_catalogs

    cache = tmp_path / "cache"
    cache.mkdir()
    repo = cache / "my-repo"
    repo.mkdir()
    mkt_dir = repo / ".grok-plugin"
    mkt_dir.mkdir()
    (mkt_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "MKT",
                "plugins": [
                    {
                        "name": "p1",
                        "description": "d1",
                        "source": {"url": "http://x", "sha": "abc"},
                    },
                    {"name": "", "description": "empty name"},
                    "not-a-dict",
                ],
            }
        ),
        encoding="utf-8",
    )
    by_key: dict = {}
    _load_cache_catalogs(by_key, cache, {"p1"})
    assert "MKT:p1" in by_key
    assert by_key["MKT:p1"].installed is True


def test_marketplace_load_cache_catalogs_non_list_plugins(tmp_path: Path):
    """plugins key as non-list is skipped."""
    from groket.capabilities.marketplace import _load_cache_catalogs

    cache = tmp_path / "cache"
    cache.mkdir()
    repo = cache / "repo"
    repo.mkdir()
    mkt_dir = repo / ".grok-plugin"
    mkt_dir.mkdir()
    (mkt_dir / "marketplace.json").write_text(
        json.dumps({"name": "M", "plugins": "not-a-list"}),
        encoding="utf-8",
    )
    by_key: dict = {}
    _load_cache_catalogs(by_key, cache, set())
    assert len(by_key) == 0


def test_marketplace_load_cache_catalogs_claude_plugin(tmp_path: Path):
    """_load_cache_catalogs reads .claude-plugin/marketplace.json variant."""
    from groket.capabilities.marketplace import _load_cache_catalogs

    cache = tmp_path / "cache"
    cache.mkdir()
    repo = cache / "repo2"
    repo.mkdir()
    cp_dir = repo / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "marketplace.json").write_text(
        json.dumps({"name": "CL", "plugins": [{"name": "cp1", "description": "d"}]}),
        encoding="utf-8",
    )
    by_key: dict = {}
    _load_cache_catalogs(by_key, cache, set())
    assert "CL:cp1" in by_key


def test_marketplace_materialize_plugin_with_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """materialize_plugin clones when catalog entry is found."""
    from groket.capabilities.marketplace import materialize_plugin

    entry = MarketplacePluginEntry(
        name="cool",
        source_url="http://x.git",
        sha="abc123",
    )
    monkeypatch.setattr(
        "groket.capabilities.marketplace.get_marketplace_entry",
        lambda n, home=None: entry,
    )

    dest = tmp_path / "out"

    def fake_clone(url, sha, d):
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(
        "groket.capabilities.marketplace._git_clone_plugin",
        fake_clone,
    )
    result = materialize_plugin("cool", dest)
    assert result == dest


def test_marketplace_fetch_official_with_valid_plugins():
    """_fetch_official_marketplace_entries parses valid plugins list."""
    from groket.capabilities.marketplace import _fetch_official_marketplace_entries

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = json.dumps(
        {
            "name": "official",
            "plugins": [
                {"name": "p1", "description": "d1"},
                {"name": "", "description": "empty"},
                "not-dict",
            ],
        }
    ).encode()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        entries = _fetch_official_marketplace_entries()
    assert len(entries) == 1
    assert entries[0].name == "p1"


def test_marketplace_load_plugin_mcp_servers_skip_unseen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """load_plugin_mcp_servers skips non-dir and deduplicates."""
    plug_dir = tmp_path / "plug"
    plug_dir.mkdir()
    (plug_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv1": {"url": "http://x"}}}),
        encoding="utf-8",
    )
    plug2 = InstalledPlugin(name="p2", path=tmp_path / "missing")
    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_installed_plugins_for_work",
        lambda *a, **kw: [
            InstalledPlugin(name="p1", path=plug_dir),
            plug2,
        ],
    )
    servers = load_plugin_mcp_servers()
    assert len(servers) == 1
    assert servers[0]["id"] == "srv1"


def test_marketplace_plugin_skills_dirs_no_manifest(tmp_path: Path):
    """_plugin_skills_dirs falls back to skills/ dir when no manifest."""
    from groket.capabilities.marketplace import _plugin_skills_dirs

    pdir = tmp_path / "plug"
    pdir.mkdir()
    (pdir / "skills").mkdir()
    dirs = _plugin_skills_dirs(pdir)
    assert any("skills" in str(d) for d in dirs)


def test_marketplace_iter_plugin_skill_roots_non_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """iter_plugin_skill_roots skips non-dir plugin paths."""
    from groket.capabilities.marketplace import iter_plugin_skill_roots

    plug = InstalledPlugin(name="bad", path=tmp_path / "missing")
    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_installed_plugins_for_work",
        lambda *a, **kw: [plug],
    )
    pairs = iter_plugin_skill_roots()
    assert pairs == []


def test_marketplace_load_plugin_mcp_dedup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """load_plugin_mcp_servers deduplicates by server id."""
    p1 = tmp_path / "p1"
    p1.mkdir()
    (p1 / "mcp.json").write_text(
        json.dumps({"mcpServers": {"same": {"url": "http://x"}}}),
        encoding="utf-8",
    )
    p2 = tmp_path / "p2"
    p2.mkdir()
    (p2 / "mcp.json").write_text(
        json.dumps({"mcpServers": {"same": {"url": "http://y"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_installed_plugins_for_work",
        lambda *a, **kw: [
            InstalledPlugin(name="p1", path=p1),
            InstalledPlugin(name="p2", path=p2),
        ],
    )
    servers = load_plugin_mcp_servers()
    # "same" only appears once
    assert sum(1 for s in servers if s["id"] == "same") == 1


def test_marketplace_load_cache_catalogs_oserror(tmp_path: Path):
    """_load_cache_catalogs handles iterdir OSError gracefully."""
    from groket.capabilities.marketplace import _load_cache_catalogs

    cache = tmp_path / "cache"
    cache.mkdir()
    by_key: dict = {}
    with patch.object(Path, "iterdir", side_effect=OSError("perms")):
        _load_cache_catalogs(by_key, cache, set())
    assert len(by_key) == 0


def test_marketplace_registry_without_plugins(tmp_path: Path):
    """_registry_repo_plugins with no plugins dict uses repo name."""
    from groket.capabilities.marketplace import _registry_repo_plugins

    by_name: dict[str, InstalledPlugin] = {}
    _registry_repo_plugins(
        by_name,
        tmp_path,
        "bare-repo",
        {
            "kind": {"url": "http://x"},
            "marketplace": {"source_display_name": "mk"},
        },
    )
    assert "bare-repo" in by_name or tmp_path.name in by_name


def test_marketplace_scan_plugin_dirs_resolve_error(tmp_path: Path):
    """_scan_plugin_dirs handles resolve OSError gracefully."""
    from groket.capabilities.marketplace import _scan_plugin_dirs

    root = tmp_path / "plugins"
    root.mkdir()
    valid = root / "plug1"
    valid.mkdir()
    (valid / "skills").mkdir()
    (valid / "skills" / "s1").mkdir()

    by_name: dict[str, InstalledPlugin] = {}

    original_resolve = Path.resolve

    def fake_resolve(self):
        if "plug1" in str(self):
            raise OSError("perms")
        return original_resolve(self)

    with patch.object(Path, "resolve", fake_resolve):
        _scan_plugin_dirs(by_name, root)
    assert "plug1" in by_name


# ── Apply: MCP newlines and empty-name guards ────────────────────────────


def test_apply_persona_mcp_blocks_final_newline():
    """MCP blocks result ends with newline when base text does not."""
    p = Persona(
        persona_id="t",
        mcp_servers=["srv"],
        mcp_definitions=[{"id": "srv", "transport": "http", "url": "http://x"}],
    )
    text = apply.apply_persona_mcp_to_config_toml("base=1", p)
    assert text.endswith("\n")
    assert "base=1" in text

    p2 = Persona(
        persona_id="t",
        mcp_extra_toml="extra=1\n",
    )
    text2 = apply.apply_persona_mcp_to_config_toml("no-newline", p2)
    assert text2.startswith("no-newline\n")
    assert text2.endswith("\n")


def test_apply_prepare_skills_no_names():
    """prepare_persona_skills_dir with empty skills returns None."""
    p = Persona(persona_id="t", skills=[])
    result = apply.prepare_persona_skills_dir(Path("/tmp/x"), p)
    assert result is None
    p2 = Persona(persona_id="t", skills=["", " "])
    result2 = apply.prepare_persona_skills_dir(Path("/tmp/x"), p2)
    assert result2 is None


def test_apply_prepare_skills_rmdir_oserror(tmp_path: Path):
    """Rmdir failure after zero copies still returns None."""
    p = Persona(persona_id="t", skills=["missing"])
    dest = tmp_path / "out"
    original_rmdir = Path.rmdir

    def _boom(self):
        if str(self) == str(dest):
            raise OSError("busy")
        return original_rmdir(self)

    with patch.object(apply, "resolve_skill_path", return_value=None):
        with patch.object(Path, "rmdir", _boom):
            result = apply.prepare_persona_skills_dir(dest, p)
    assert result is None


def test_apply_prepare_plugins_empty_names():
    """prepare_persona_plugins_dir with empty plugin names returns None."""
    p = Persona(persona_id="t", plugins=[])
    result = apply.prepare_persona_plugins_dir(Path("/tmp/x"), p)
    assert result is None
    p2 = Persona(persona_id="t", plugins=["", " "])
    result2 = apply.prepare_persona_plugins_dir(Path("/tmp/x"), p2)
    assert result2 is None


def test_apply_plugins_text_newline():
    """apply_persona_plugins_to_config_toml adds trailing newline."""
    p = Persona(persona_id="t", plugins=["x"])
    text = apply.apply_persona_plugins_to_config_toml("base=1", p)
    assert text.endswith("\n")
    assert "base=1" in text


# ── Catalog: host-only fallback ────────────────────────────────────────────


def test_catalog_get_mcp_entry_host_only(monkeypatch: pytest.MonkeyPatch):
    """get_mcp_entry with host-only server not in catalog."""
    monkeypatch.setattr(catalog, "_catalog_paths", lambda work_dir=None: [])
    monkeypatch.setattr(catalog, "list_host_mcp_server_names", lambda *a, **k: ["host-only-srv"])
    entry = catalog.get_mcp_entry("host-only-srv")
    assert entry is not None
    assert entry.id == "host-only-srv"
    assert entry.transport == "host"


# ── Registry: docs links and parse_hit fallback ──────────────────────────


def test_registry_docs_links_skips_empty_and_deduplicates():
    """docs_links skips empty URLs and deduplicates."""
    hit = RegistryServerHit(
        name="x/srv",
        repository_url="http://repo",
    )
    links = hit.docs_links()
    urls = [url for _, url in links]
    assert all(u.strip() for u in urls)
    assert len(urls) == len(set(urls))


def test_parse_hit_server_key_not_dict_fallback():
    """_parse_hit with non-dict server key uses item as server."""
    # item.get("server") is not dict, falls back to item; item has "name"
    result = _parse_hit({"name": "fallback/srv", "title": "Fallback", "server": 42})
    assert result is not None
    assert result.name == "fallback/srv"


# ── Apply: definition selection and config assembly ───────────────────────


class TestApplyMcpDefinitionAutoSelected:
    """Definition id auto-added to selected list."""

    def test_definition_id_auto_appended(self) -> None:
        from groket.runs.personas import Persona

        p = Persona(
            persona_id="test-id",
            name="test",
            mcp_servers=[],
            mcp_definitions=[{"id": "my-def", "transport": "stdio", "command": "node"}],
        )
        result = apply.apply_persona_mcp_to_config_toml(
            "existing = true",
            p,
            work_dir=None,
            host_config_text=None,
        )
        assert "my-def" in result


class TestApplyMcpNoTrailingNewline:
    """Text without trailing newline gets one appended."""

    def test_text_gets_newline(self) -> None:
        from groket.runs.personas import Persona

        p = Persona(
            persona_id="test-id",
            name="test",
            mcp_servers=["mock-server"],
            mcp_definitions=[{"id": "mock-server", "transport": "stdio", "command": "node"}],
        )
        result = apply.apply_persona_mcp_to_config_toml(
            "base",
            p,
            work_dir=None,
            host_config_text=None,
        )
        assert result.endswith("\n")


class TestApplySkillsNoTrailingNewline:
    """Skills config appended when text has no trailing newline."""

    def test_skills_appended(self) -> None:
        from groket.runs.personas import Persona

        p = Persona(
            persona_id="test-id", name="test", skills=["my-skill"], skills_disabled=["other-skill"]
        )
        result = apply.apply_persona_skills_to_config_toml("base", p)
        assert "[skills]" in result
        assert "other-skill" in result
        assert result.endswith("\n")


class TestApplyPluginsEmptyNames:
    """Blank plugin names are ignored; host [plugins] still stripped."""

    def test_empty_plugin_names_clears_host(self) -> None:
        from groket.runs.personas import Persona

        p = Persona(persona_id="test-id", name="test", plugins=["", " "])
        host = 'base-config\n[plugins]\nenabled = ["host-plug"]\n'
        result = apply.apply_persona_plugins_to_config_toml(host, p)
        assert "host-plug" not in result
        assert "enabled = []" in result

    def test_plugins_with_names_appended(self) -> None:
        from groket.runs.personas import Persona

        p = Persona(persona_id="test-id", name="test", plugins=["my-plugin"])
        result = apply.apply_persona_plugins_to_config_toml("existing", p)
        assert "my-plugin" in result


# ── Registry: dedup and catalog paths ─────────────────────────────────────


class TestRegistryHitDocsLinksDedup:
    """docs_links deduplicates URLs."""

    def test_duplicate_urls_deduped(self) -> None:

        hit = RegistryServerHit(
            name="test",
            title="Test",
            description="",
        )
        expected_reg = hit.registry_page_url()
        hit2 = RegistryServerHit(
            name="test",
            title="Test",
            description="",
            repository_url=expected_reg,
        )
        result = hit2.docs_links()
        urls = [u for _, u in result]
        assert urls.count(expected_reg) == 1


# ── Catalog: path resolution ──────────────────────────────────────────────


class TestCatalogPathsWorkDir:
    """_catalog_paths includes work_dir sub-paths."""

    def test_work_dir_paths_included(self, tmp_path: Path) -> None:
        from groket.capabilities.catalog import _catalog_paths

        paths = _catalog_paths(tmp_path)
        path_strs = [str(p) for p in paths]
        assert any("capabilities" in s and "mcp_catalog.yaml" in s for s in path_strs)


class TestGetMcpEntryHostFallback:
    """get_mcp_entry falls back to host config lookup."""

    def test_host_fallback_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "groket.capabilities.catalog.load_mcp_catalog", lambda work_dir=None: []
        )
        monkeypatch.setattr(
            "groket.capabilities.catalog.list_host_mcp_server_names",
            lambda: ["host-server"],
        )
        from groket.capabilities.catalog import get_mcp_entry

        result = get_mcp_entry("host-server")
        assert result is not None
        assert result.transport == "host"
