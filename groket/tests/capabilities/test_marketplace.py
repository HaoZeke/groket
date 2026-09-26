"""Grok plugin marketplace — first-class plugins (not flattened into MCP/skills)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.capabilities.apply import (
    apply_persona_plugins_to_config_toml,
    prepare_persona_plugins_dir,
)
from groket.capabilities.catalog import load_mcp_catalog, scan_host_skills
from groket.capabilities.marketplace import (
    list_installed_plugins,
    list_marketplace_catalog,
)
from groket.capabilities.merge import merge_capabilities
from groket.runs.personas import Persona


def _write_skill(root: Path, name: str, desc: str = "test skill") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_list_installed_plugins_from_registry(tmp_path: Path) -> None:
    home = tmp_path / "home"
    grok = home / ".grok"
    inst = grok / "installed-plugins"
    plugin_dir = inst / "demo-plugin-abc"
    skills = plugin_dir / "skills"
    _write_skill(skills, "demo-skill", "from plugin")
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo-mcp": {"command": "npx", "args": ["demo-mcp@latest"]},
                }
            }
        ),
        encoding="utf-8",
    )
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "repos": {
                    "demo-plugin-abc": {
                        "kind": {
                            "type": "Git",
                            "url": "https://example.com/demo.git",
                            "commit": "deadbeef",
                        },
                        "path": str(plugin_dir),
                        "plugins": {"demo-plugin": {"version": "1.0.0"}},
                        "marketplace": {
                            "source_display_name": "plugin-marketplace",
                            "plugin_subdir": "demo",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    plugins = list_installed_plugins(home=grok)
    assert len(plugins) == 1
    assert plugins[0].name == "demo-plugin"
    assert plugins[0].path == plugin_dir


def test_plugins_not_in_skills_or_mcp_catalog(tmp_path: Path, monkeypatch) -> None:
    """Plugin components must not appear as standalone skills/MCP picks."""
    home = tmp_path / "gh"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    grok = home / ".grok"
    plugin_dir = grok / "installed-plugins" / "plug1"
    _write_skill(plugin_dir / "skills", "from-plugin", "plugin skill")
    (plugin_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"plug-srv": {"command": "echo"}}}),
        encoding="utf-8",
    )
    (grok / "installed-plugins").mkdir(parents=True, exist_ok=True)
    (grok / "installed-plugins" / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "repos": {
                    "plug1": {
                        "path": str(plugin_dir),
                        "plugins": {"myplug": {"version": "0.1"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _write_skill(grok / "skills", "user-only", "user skill")

    names = {s.name for s in scan_host_skills()}
    assert "user-only" in names
    assert "from-plugin" not in names

    mcp_ids = {e.id for e in load_mcp_catalog()}
    assert "plug-srv" not in mcp_ids


def test_prepare_persona_plugins_dir_and_config(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "gh3"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    grok = home / ".grok"
    cache = grok / "marketplace-cache" / "c" / ".grok-plugin"
    cache.mkdir(parents=True)
    (cache / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "xai-official",
                "plugins": [
                    {
                        "name": "cool-plug",
                        "description": "d",
                        "source": {
                            "url": "https://example.com/cool.git",
                            "sha": "deadbeef",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def _fake_materialize(name: str, dest_dir: Path, **kw):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "plugin.json").write_text("{}", encoding="utf-8")
        return dest_dir

    import groket.capabilities.marketplace as mp

    monkeypatch.setattr(mp, "materialize_plugin", _fake_materialize)

    persona = Persona(persona_id="t", plugins=["cool-plug"])
    dest = tmp_path / "stage"
    out = prepare_persona_plugins_dir(dest, persona)
    assert out is not None
    manifest = dest / "plugins-manifest.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data[0]["name"] == "cool-plug"
    assert data[0]["checkout"] == "checkouts/cool-plug"
    assert (dest / "checkouts" / "cool-plug").is_dir()

    toml = apply_persona_plugins_to_config_toml("", persona)
    assert "[plugins]" in toml
    assert "cool-plug" in toml
    assert "enabled" in toml


def test_merge_includes_plugins() -> None:
    m = merge_capabilities(
        persona_plugins=["a"],
        run_plugins=["b", "a"],
    )
    assert m["plugins"] == ["a", "b"]


def test_work_dir_plugins_not_scanned_as_grok(tmp_path: Path, monkeypatch) -> None:
    """groket analysis packages under work_dir/plugins/ must not appear as Grok plugins."""
    from groket.capabilities.marketplace import list_installed_plugins_for_work

    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    analysis = tmp_path / "proj" / "plugins" / "gte-feedback-grok"
    analysis.mkdir(parents=True)
    (analysis / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    names = {p.name for p in list_installed_plugins_for_work(tmp_path / "proj")}
    assert "gte-feedback-grok" not in names


def test_picker_lists_catalog_entries(tmp_path: Path, monkeypatch) -> None:
    from groket.capabilities.marketplace import list_plugins_for_picker

    home = tmp_path / "h2"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    grok = home / ".grok"
    cache = grok / "marketplace-cache" / "c" / ".grok-plugin"
    cache.mkdir(parents=True)
    (cache / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "xai-official",
                "plugins": [
                    {
                        "name": "catalog-a",
                        "description": "d1",
                        "source": {"url": "https://example.com/a.git", "sha": "aaa"},
                    },
                    {
                        "name": "catalog-b",
                        "description": "d2",
                        "source": {"url": "https://example.com/b.git", "sha": "bbb"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    rows = {r.name: r for r in list_plugins_for_picker()}
    assert rows["catalog-a"].selectable is True
    assert rows["catalog-a"].status == "catalog"
    assert rows["catalog-b"].selectable is True
    assert "d2" in rows["catalog-b"].description
    assert "d2" in rows["catalog-b"].detail_markup()


def test_picker_lists_installed_without_marketplace_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed registry plugins appear even when marketplace-cache is empty."""
    from groket.capabilities.marketplace import list_plugins_for_picker

    home = tmp_path / "home"
    grok = home / ".grok"
    inst = grok / "installed-plugins" / "repo-local"
    skills = inst / "skills" / "demo-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    reg = grok / "installed-plugins" / "registry.json"
    reg.write_text(
        json.dumps(
            {
                "version": 1,
                "repos": {
                    "repo-local": {
                        "path": str(inst),
                        "plugins": {"my-local-plugin": {"version": "1.0.0"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    # Avoid network in unit tests; installed rows still fill the picker.
    monkeypatch.setattr(
        "groket.capabilities.marketplace._fetch_official_marketplace_entries",
        lambda: [],
    )
    rows = {r.name: r for r in list_plugins_for_picker()}
    assert "my-local-plugin" in rows
    assert rows["my-local-plugin"].status == "installed"
    assert "skills" in rows["my-local-plugin"].components


def test_picker_requests_remote_catalog_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Picker enables remote index so catalog rows appear without a local cache clone."""
    from groket.capabilities.marketplace import (
        MarketplacePluginEntry,
        list_plugins_for_picker,
    )

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    seen: dict[str, bool] = {}

    def _catalog(*, home=None, include_remote_index: bool = False):
        seen["include_remote_index"] = include_remote_index
        if not include_remote_index:
            return []
        return [
            MarketplacePluginEntry(
                name="vercel",
                description="deploy",
                category="deployment",
                marketplace="xai-official",
            )
        ]

    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_marketplace_catalog",
        _catalog,
    )
    rows = {r.name: r for r in list_plugins_for_picker()}
    assert seen.get("include_remote_index") is True
    assert rows["vercel"].status == "catalog"
    assert "deploy" in rows["vercel"].description


def test_get_marketplace_entry_uses_remote_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launch staging resolves catalog plugins without a local marketplace-cache."""
    from groket.capabilities.marketplace import (
        MarketplacePluginEntry,
        get_marketplace_entry,
    )

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    seen: dict[str, bool] = {}

    def _catalog(*, home=None, include_remote_index: bool = False):
        seen["include_remote_index"] = include_remote_index
        if not include_remote_index:
            return []
        return [
            MarketplacePluginEntry(
                name="superpowers",
                source_url="https://github.com/obra/superpowers.git",
                sha="abc123",
                marketplace="xai-official",
            )
        ]

    monkeypatch.setattr(
        "groket.capabilities.marketplace.list_marketplace_catalog",
        _catalog,
    )
    entry = get_marketplace_entry("superpowers")
    assert seen.get("include_remote_index") is True
    assert entry is not None
    assert entry.source_url.endswith("superpowers.git")


def test_materialize_clones_from_catalog(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from groket.capabilities.marketplace import materialize_plugin

    home = tmp_path / "hm"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    grok = home / ".grok"
    cache = grok / "marketplace-cache" / "c" / ".grok-plugin"
    cache.mkdir(parents=True)
    (cache / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "xai-official",
                "plugins": [
                    {
                        "name": "cool",
                        "source": {
                            "url": "https://example.com/cool.git",
                            "sha": "abc",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "out" / "cool"
    with patch("subprocess.run", return_value=MagicMock()) as run:
        assert materialize_plugin("cool", dest) == dest
        assert run.called


def test_marketplace_catalog_from_cache(tmp_path: Path) -> None:
    grok = tmp_path / ".grok"
    cache = grok / "marketplace-cache" / "abc123" / ".grok-plugin"
    cache.mkdir(parents=True)
    (cache / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "xai-official",
                "plugins": [
                    {
                        "name": "vercel",
                        "description": "Deploy stuff",
                        "category": "deployment",
                        "source": {
                            "source": "url",
                            "url": "https://github.com/vercel/vercel-plugin.git",
                            "sha": "abc",
                        },
                        "keywords": ["vercel"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    entries = list_marketplace_catalog(home=grok)
    assert len(entries) == 1
    assert entries[0].name == "vercel"


class TestPluginComponentsSummary:
    """_plugin_components_summary reports skills count correctly."""

    def test_skills_dir_with_entries(self, tmp_path: Path) -> None:
        from groket.capabilities.marketplace import _plugin_components_summary

        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "sk1").mkdir()
        (skills_dir / "sk2").mkdir()
        info = _plugin_components_summary(plugin_dir)
        assert "skills:2" in info

    def test_skills_dir_empty(self, tmp_path: Path) -> None:
        from groket.capabilities.marketplace import _plugin_components_summary

        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        info = _plugin_components_summary(plugin_dir)
        assert "skills" in info


class TestGitClonePluginFailure:
    """Git clone failure returns None."""

    def test_clone_failure_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        from groket.capabilities.marketplace import _git_clone_plugin

        def bad_run(*a, **kw):
            raise subprocess.SubprocessError("clone failed")

        monkeypatch.setattr(subprocess, "run", bad_run)
        result = _git_clone_plugin(
            "https://github.com/org/repo.git",
            "abc123",
            tmp_path / "plugins" / "dest",
        )
        assert result is None


class TestListPluginsForPickerDuplicate:
    """list_plugins_for_picker deduplicates by name."""

    def test_dedup_by_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from groket.capabilities.marketplace import (
            MarketplacePluginEntry,
            list_plugins_for_picker,
        )

        entries = [
            MarketplacePluginEntry(name="dup-plugin", description="first", category="tools"),
            MarketplacePluginEntry(name="dup-plugin", description="second", homepage="https://x"),
        ]
        monkeypatch.setattr(
            "groket.capabilities.marketplace.list_marketplace_catalog",
            lambda **kw: entries,
        )
        rows = list_plugins_for_picker(home=tmp_path)
        names = [r.name for r in rows]
        assert names.count("dup-plugin") == 1


class TestLoadRegistryInto:
    """Registry parsing handles invalid JSON and non-dict repos."""

    def test_registry_bad_json(self, tmp_path: Path) -> None:
        from groket.capabilities.marketplace import _load_registry_into

        root = tmp_path / "installed-plugins"
        root.mkdir()
        reg = root / "registry.json"
        reg.write_text("not-json", encoding="utf-8")
        by_name: dict = {}
        _load_registry_into(by_name, root)
        assert by_name == {}

    def test_registry_non_dict_repos(self, tmp_path: Path) -> None:
        from groket.capabilities.marketplace import _load_registry_into

        root = tmp_path / "installed-plugins"
        root.mkdir()
        reg = root / "registry.json"
        reg.write_text(json.dumps({"repos": "not-dict"}), encoding="utf-8")
        by_name: dict = {}
        _load_registry_into(by_name, root)
        assert by_name == {}

    def test_registry_repo_empty_plugin_name(self, tmp_path: Path) -> None:
        from groket.capabilities.marketplace import _registry_repo_plugins

        by_name: dict = {}
        _registry_repo_plugins(
            by_name,
            tmp_path,
            "repo-1",
            {"plugins": {"": {"version": "1.0"}, "valid": {"version": "2.0"}}},
        )
        assert "" not in by_name
        assert "valid" in by_name


class TestPluginSkillsDirsDedup:
    """_plugin_skills_dirs deduplicates via seen set."""

    def test_dedup_candidates(self, tmp_path: Path) -> None:
        from groket.capabilities.marketplace import _plugin_skills_dirs

        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "sk1").mkdir()
        result = _plugin_skills_dirs(plugin_dir)
        assert isinstance(result, list)


def test_prepare_persona_plugins_stages_checkouts_and_skills(tmp_path: Path, monkeypatch) -> None:
    """One host path: checkouts + plugin skills staged for the container."""
    from groket.capabilities import marketplace as mp
    from groket.capabilities.apply import prepare_persona_plugins_dir
    from groket.runs.personas import Persona

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    plug = home / ".grok" / "installed-plugins" / "superpowers-x"
    skill = plug / "skills" / "using-superpowers"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# using-superpowers\n", encoding="utf-8")
    tdd = plug / "skills" / "test-driven-development"
    tdd.mkdir(parents=True)
    (tdd / "SKILL.md").write_text("# tdd\n", encoding="utf-8")

    monkeypatch.setattr(
        mp,
        "list_installed_plugins_for_work",
        lambda *a, **k: [
            mp.InstalledPlugin(
                name="superpowers",
                path=plug,
                marketplace_plugin="superpowers",
            )
        ],
    )
    monkeypatch.setattr(
        mp,
        "plugin_install_specs",
        lambda *a, **k: [
            {
                "name": "superpowers",
                "source_url": "https://example.com/superpowers.git",
                "sha": "abc",
            }
        ],
    )

    persona = Persona(persona_id="p", plugins=["superpowers"])
    plugins_dest = tmp_path / "plugins"
    skills_dest = tmp_path / "skills"
    assert prepare_persona_plugins_dir(plugins_dest, persona, skills_dest=skills_dest) is not None
    manifest = json.loads((plugins_dest / "plugins-manifest.json").read_text(encoding="utf-8"))
    assert manifest[0].get("checkout") == "checkouts/superpowers"
    assert (plugins_dest / "checkouts" / "superpowers" / "skills" / "using-superpowers").is_dir()
    assert (skills_dest / "using-superpowers" / "SKILL.md").is_file()
    assert (skills_dest / "test-driven-development" / "SKILL.md").is_file()
