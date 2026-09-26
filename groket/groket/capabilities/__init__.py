"""MCP and skills capabilities (persona-owned; separate catalogs)."""

from __future__ import annotations

from .apply import (
    apply_persona_mcp_to_config_toml,
    apply_persona_plugins_to_config_toml,
    apply_persona_skills_to_config_toml,
    prepare_persona_plugins_dir,
    prepare_persona_skills_dir,
    skills_config_toml_fragment,
)
from .catalog import (
    McpCatalogEntry,
    SkillEntry,
    load_mcp_catalog,
    scan_host_skills,
    search_mcp_catalog,
    search_skills,
)
from .marketplace import (
    InstalledPlugin,
    MarketplacePluginEntry,
    PluginPickRow,
    get_marketplace_entry,
    list_installed_plugins,
    list_installed_plugins_for_work,
    list_marketplace_catalog,
    list_plugins_for_picker,
    marketplace_news_url,
    materialize_plugin,
    official_marketplace_repo,
    plugin_install_specs,
    search_marketplace_catalog,
)
from .merge import merge_capabilities
from .registry import (
    RegistryServerHit,
    clear_registry_cache,
    registry_hit_to_definition,
    search_registry,
)
from .skill_gen import (
    companion_skill_name,
    is_implicit_mcp_companion_skill,
    is_mcp_companion_skill_name,
    write_mcp_companion_skill,
)

__all__ = [
    "McpCatalogEntry",
    "SkillEntry",
    "InstalledPlugin",
    "MarketplacePluginEntry",
    "PluginPickRow",
    "RegistryServerHit",
    "load_mcp_catalog",
    "scan_host_skills",
    "search_mcp_catalog",
    "search_skills",
    "list_installed_plugins",
    "list_installed_plugins_for_work",
    "list_marketplace_catalog",
    "list_plugins_for_picker",
    "get_marketplace_entry",
    "materialize_plugin",
    "plugin_install_specs",
    "search_marketplace_catalog",
    "marketplace_news_url",
    "official_marketplace_repo",
    "search_registry",
    "clear_registry_cache",
    "registry_hit_to_definition",
    "apply_persona_mcp_to_config_toml",
    "apply_persona_plugins_to_config_toml",
    "apply_persona_skills_to_config_toml",
    "prepare_persona_plugins_dir",
    "prepare_persona_skills_dir",
    "skills_config_toml_fragment",
    "companion_skill_name",
    "is_implicit_mcp_companion_skill",
    "is_mcp_companion_skill_name",
    "write_mcp_companion_skill",
    "merge_capabilities",
]
