"""Grok Build plugin marketplace discovery (installed plugins + local catalog cache).

See https://x.ai/news/grok-plugin-marketplace and Grok user-guide 09-plugins.md.

On-disk layout (Grok CLI):
  ~/.grok/installed-plugins/registry.json   — installed plugin repos
  ~/.grok/installed-plugins/<id>/           — checkout (skills/, .mcp.json, …)
  ~/.grok/marketplace-cache/<hash>/         — cloned marketplace sources
    .grok-plugin/marketplace.json           — catalog entries
    .grok-plugin/plugin-index.json          — component summaries
  ~/.grok/plugins/                          — user plugin dirs (if present)
  .grok/plugins/ under a work dir           — project-scoped plugins
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..models import (
    JsonObject,
    JsonValue,
    json_as_object,
    json_as_str,
    json_as_str_list,
)

logger = logging.getLogger(__name__)

_OFFICIAL_MARKETPLACE_URL = "https://github.com/xai-org/plugin-marketplace"
_NEWS_URL = "https://x.ai/news/grok-plugin-marketplace"
_MANIFEST_RELS = (
    ".grok-plugin/plugin.json",
    "plugin.json",
    ".claude-plugin/plugin.json",
    ".github/plugin/plugin.json",
)
_MCP_JSON_RELS = (".mcp.json", "mcp.json")


@dataclass
class InstalledPlugin:
    """One logical plugin from installed-plugins/registry.json."""

    name: str
    path: Path
    version: str = ""
    marketplace: str = ""
    marketplace_plugin: str = ""
    git_url: str = ""
    commit: str = ""
    repo_id: str = ""

    def search_blob(self) -> str:
        parts = [
            self.name,
            self.version,
            self.marketplace,
            self.marketplace_plugin,
            self.git_url,
        ]
        return " ".join(parts).lower()


@dataclass
class MarketplacePluginEntry:
    """Catalog row from a marketplace source (may not be installed)."""

    name: str
    description: str = ""
    category: str = ""
    homepage: str = ""
    marketplace: str = ""
    source_url: str = ""
    sha: str = ""
    keywords: list[str] = field(default_factory=list)
    installed: bool = False

    def search_blob(self) -> str:
        parts = [
            self.name,
            self.description,
            self.category,
            self.marketplace,
            " ".join(self.keywords),
        ]
        return " ".join(parts).lower()


def grok_home(home: Path | None = None) -> Path:
    """Grok config root (``~/.grok``). Pass *home* to override in tests."""
    return Path(home) if home is not None else (Path.home() / ".grok")


def installed_plugins_root(home: Path | None = None) -> Path:
    return grok_home(home) / "installed-plugins"


def marketplace_cache_root(home: Path | None = None) -> Path:
    return grok_home(home) / "marketplace-cache"


def list_installed_plugins(*, home: Path | None = None) -> list[InstalledPlugin]:
    """Read ~/.grok/installed-plugins/registry.json (+ scan dirs without registry)."""
    by_name: dict[str, InstalledPlugin] = {}
    root = installed_plugins_root(home)
    _load_registry_into(by_name, root)
    _scan_plugin_dirs(by_name, root)
    _scan_plugin_dirs(by_name, grok_home(home) / "plugins")
    return sorted(by_name.values(), key=lambda p: p.name.lower())


def list_installed_plugins_for_work(
    work_dir: Path | None = None,
    *,
    home: Path | None = None,
) -> list[InstalledPlugin]:
    """Installed user plugins plus project-scoped Grok plugins under *work_dir*.

    Only scans ``work_dir/.grok/plugins/`` (Grok Build layout). Does **not** scan
    ``work_dir/plugins/`` — that path is often used for *groket analysis* plugins
    (Python packages), which are a different concept.
    """
    by_name = {p.name: p for p in list_installed_plugins(home=home)}
    if work_dir is not None:
        wd = Path(work_dir).expanduser()
        _scan_plugin_dirs(by_name, wd / ".grok" / "plugins")
    return sorted(by_name.values(), key=lambda p: p.name.lower())


def is_grok_plugin_dir(path: Path) -> bool:
    """Heuristic: Grok Build plugin checkout vs arbitrary Python package dir."""
    if not path.is_dir():
        return False
    markers = (
        path / "skills",
        path / ".mcp.json",
        path / "mcp.json",
        path / "plugin.json",
        path / ".grok-plugin",
        path / ".claude-plugin",
        path / "hooks" / "hooks.json",
        path / "agents",
        path / "commands",
    )
    return any(m.exists() for m in markers)


@dataclass
class PluginPickRow:
    """Row for the persona/runner plugin picker (installed and/or catalog)."""

    name: str
    status: str = "catalog"  # installed | catalog | fetch
    marketplace: str = ""
    version: str = ""
    description: str = ""
    category: str = ""
    homepage: str = ""
    source_url: str = ""
    sha: str = ""
    components: str = ""  # e.g. "skills+mcp" summary
    path: Path | None = None
    selectable: bool = True  # catalog ok — staged via git at launch

    def search_blob(self) -> str:
        return " ".join(
            [
                self.name,
                self.status,
                self.marketplace,
                self.version,
                self.description,
                self.category,
                self.components,
            ]
        ).lower()

    def detail_markup(self) -> str:
        """Rich lines for the picker detail pane."""
        lines = [
            f"[bold]{self.name}[/bold]  [dim]{self.status}[/dim]",
        ]
        if self.description:
            lines.append(self.description.strip())
        meta: list[str] = []
        if self.category:
            meta.append(f"category={self.category}")
        if self.marketplace:
            meta.append(f"source={self.marketplace}")
        if self.version:
            meta.append(f"ver={self.version}")
        if self.sha:
            meta.append(f"sha={self.sha[:12]}")
        if self.components:
            meta.append(self.components)
        if meta:
            lines.append("[dim]" + " · ".join(meta) + "[/dim]")
        if self.source_url:
            lines.append(f"[dim]git {self.source_url}[/dim]")
        if self.homepage:
            lines.append(f"[dim]{self.homepage}[/dim]")
        if self.path is not None:
            lines.append(f"[dim]{self.path}[/dim]")
        return "\n".join(lines)


def _plugin_components_summary(path: Path | None) -> str:
    if path is None or not path.is_dir():
        return ""
    bits: list[str] = []
    if (path / "skills").is_dir():
        try:
            n = sum(1 for c in (path / "skills").iterdir() if c.is_dir())
        except OSError:
            n = 0
        bits.append(f"skills:{n}" if n else "skills")
    if (path / ".mcp.json").is_file() or (path / "mcp.json").is_file():
        bits.append("mcp")
    if (path / "hooks" / "hooks.json").is_file():
        bits.append("hooks")
    if (path / "agents").is_dir():
        bits.append("agents")
    if (path / "commands").is_dir():
        bits.append("commands")
    return "+".join(bits)


def get_marketplace_entry(
    name: str,
    *,
    home: Path | None = None,
) -> MarketplacePluginEntry | None:
    """Resolve a catalog row by plugin name (exact).

    Uses the local marketplace-cache when present, otherwise the official
    remote index (same as the Plugins picker) so launch staging can git-clone
    catalog plugins like ``superpowers`` without a prior host install.
    """
    n = (name or "").strip()
    if not n:
        return None
    for entry in list_marketplace_catalog(home=home, include_remote_index=True):
        if entry.name == n:
            return entry
    return None


def plugin_install_specs(
    names: list[str],
    *,
    work_dir: Path | None = None,
    home: Path | None = None,
) -> list[JsonObject]:
    """Map plugin names to catalog install specs (``name``, ``source_url``, ``sha``)."""
    _ = work_dir
    out: list[JsonObject] = []
    seen: set[str] = set()
    for raw in names:
        n = (raw or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        entry = get_marketplace_entry(n, home=home)
        if entry is None or not entry.source_url:
            logger.warning(
                "plugin %r not in marketplace catalog — cannot schedule container install",
                n,
            )
            continue
        out.append(
            {
                "name": entry.name or n,
                "source_url": entry.source_url,
                "sha": entry.sha or "",
            }
        )
    return out


def materialize_plugin(
    name: str,
    dest_dir: Path,
    *,
    work_dir: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    """Clone a catalog plugin into *dest_dir* (git URL + optional SHA pin)."""
    entry = get_marketplace_entry((name or "").strip(), home=home)
    if entry is None or not entry.source_url:
        logger.warning("plugin %r not in marketplace catalog", name)
        return None
    _ = work_dir
    return _git_clone_plugin(entry.source_url, entry.sha, dest_dir)


def _git_clone_plugin(url: str, sha: str, dest_dir: Path) -> Path | None:
    import shutil
    import subprocess

    dest_dir = Path(dest_dir)
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Full clone then checkout pin (marketplaces pin exact commits).
        subprocess.run(
            ["git", "clone", "--quiet", url, str(dest_dir)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        if sha:
            subprocess.run(
                ["git", "-C", str(dest_dir), "checkout", "--quiet", sha],
                check=True,
                capture_output=True,
                timeout=60,
            )
        # Drop .git to keep volume smaller / read-only friendly.
        git_dir = dest_dir / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir, ignore_errors=True)
        return dest_dir
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git fetch plugin %s@%s failed: %s", url, sha[:12] if sha else "", exc)
        shutil.rmtree(dest_dir, ignore_errors=True)
        return None


def list_plugins_for_picker(
    work_dir: Path | None = None,
    *,
    home: Path | None = None,
) -> list[PluginPickRow]:
    """Rows for persona/runner plugin pickers.

    Includes **installed** plugins from ``~/.grok/installed-plugins`` (and
    project ``.grok/plugins/``) plus optional marketplace-cache / remote
    catalog entries. Without this, a machine with only local installs (no
    ``marketplace-cache``) shows an empty Plugins picker.
    """
    by_name: dict[str, PluginPickRow] = {}

    for plugin in list_installed_plugins_for_work(work_dir, home=home):
        name = (plugin.name or "").strip()
        if not name:
            continue
        by_name[name] = PluginPickRow(
            name=name,
            status="installed",
            marketplace=plugin.marketplace or plugin.marketplace_plugin or "installed",
            version=plugin.version or (plugin.commit[:8] if plugin.commit else ""),
            description="",
            category="",
            homepage="",
            source_url=plugin.git_url,
            sha=plugin.commit,
            components=_plugin_components_summary(plugin.path),
            path=plugin.path if plugin.path.is_dir() else None,
            selectable=True,
        )

    # Prefer local marketplace-cache; fall back to official xAI index over the network
    # so the picker is not empty when the user has never cloned a marketplace.
    for entry in list_marketplace_catalog(home=home, include_remote_index=True):
        if not entry.name:
            continue
        if entry.name in by_name:
            row = by_name[entry.name]
            row.description = row.description or entry.description
            row.category = row.category or entry.category
            row.homepage = row.homepage or entry.homepage
            row.source_url = row.source_url or entry.source_url
            row.sha = row.sha or entry.sha
            if row.status != "installed":
                row.status = "catalog"
            continue
        by_name[entry.name] = PluginPickRow(
            name=entry.name,
            status="catalog" if not entry.installed else "installed",
            marketplace=entry.marketplace or "marketplace",
            version=(entry.sha[:8] if entry.sha else ""),
            description=entry.description,
            category=entry.category,
            homepage=entry.homepage,
            source_url=entry.source_url,
            sha=entry.sha,
            components="",
            path=None,
            selectable=True,
        )

    return sorted(by_name.values(), key=lambda r: r.name.lower())


def _load_registry_into(by_name: dict[str, InstalledPlugin], root: Path) -> None:
    reg = root / "registry.json"
    if not reg.is_file():
        return
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("installed-plugins registry unreadable: %s", exc)
        return
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, dict):
        return
    for repo_id, repo in repos.items():
        if isinstance(repo, dict):
            _registry_repo_plugins(by_name, root, str(repo_id), repo)


def _registry_repo_plugins(
    by_name: dict[str, InstalledPlugin],
    root: Path,
    repo_id: str,
    repo: JsonObject,
) -> None:
    path_s = json_as_str(repo.get("path")).strip()
    path = Path(path_s) if path_s else root / repo_id
    kind = json_as_object(repo.get("kind"))
    git_url = json_as_str(kind.get("url")).strip()
    commit = (json_as_str(kind.get("commit")) or json_as_str(kind.get("git_ref"))).strip()
    mkt = json_as_object(repo.get("marketplace"))
    mkt_name = json_as_str(mkt.get("source_display_name")).strip()
    mkt_plugin = json_as_str(mkt.get("plugin_subdir")).strip()
    plugins = json_as_object(repo.get("plugins"))
    if plugins:
        for pname, meta in plugins.items():
            name = str(pname).strip()
            if not name:
                continue
            ver = ""
            if isinstance(meta, dict):
                ver = str(meta.get("version") or "").strip()
            by_name[name] = InstalledPlugin(
                name=name,
                path=path,
                version=ver,
                marketplace=mkt_name,
                marketplace_plugin=mkt_plugin or name,
                git_url=git_url,
                commit=commit,
                repo_id=repo_id,
            )
        return
    name = path.name
    by_name[name] = InstalledPlugin(
        name=name,
        path=path,
        marketplace=mkt_name,
        marketplace_plugin=mkt_plugin,
        git_url=git_url,
        commit=commit,
        repo_id=repo_id,
    )


def _scan_plugin_dirs(by_name: dict[str, InstalledPlugin], root: Path) -> None:
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    known_paths = {p.path.resolve() for p in by_name.values() if p.path.exists()}
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not is_grok_plugin_dir(child):
            continue
        try:
            resolved = child.resolve()
        except OSError:
            resolved = child
        if resolved in known_paths:
            continue
        name = _plugin_manifest_name(child) or child.name
        if name not in by_name:
            by_name[name] = InstalledPlugin(name=name, path=child, repo_id=child.name)
            known_paths.add(resolved)


def _plugin_manifest_name(plugin_dir: Path) -> str | None:
    for rel in _MANIFEST_RELS:
        data = _read_json(plugin_dir / rel)
        if isinstance(data, dict):
            name = str(data.get("name") or "").strip()
            if name:
                return name
    return None


def _read_json(fp: Path) -> JsonValue:
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _plugin_skills_dirs(plugin_dir: Path) -> list[Path]:
    """Return skill package roots (dirs containing SKILL.md children)."""
    candidates = [plugin_dir / "skills", plugin_dir / "skill"]
    for rel in _MANIFEST_RELS[:3]:
        data = _read_json(plugin_dir / rel)
        if not isinstance(data, dict):
            continue
        for key in ("skills", "skillsPath", "skills_path"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(plugin_dir / val.strip())
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item.strip():
                        candidates.append(plugin_dir / item.strip())
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_dir():
            out.append(c)
    return out


def iter_plugin_skill_roots(
    work_dir: Path | None = None,
    *,
    home: Path | None = None,
) -> list[tuple[Path, str]]:
    """(skills_root, source_label) for each installed plugin that has skills/."""
    pairs: list[tuple[Path, str]] = []
    for plugin in list_installed_plugins_for_work(work_dir, home=home):
        if not plugin.path.is_dir():
            continue
        label = f"plugin:{plugin.name}"
        for skills_root in _plugin_skills_dirs(plugin.path):
            pairs.append((skills_root, label))
    return pairs


def load_plugin_mcp_servers(
    work_dir: Path | None = None,
    *,
    home: Path | None = None,
) -> list[JsonObject]:
    """Flatten MCP server defs from installed plugins (.mcp.json / manifests)."""
    out: list[JsonObject] = []
    seen: set[str] = set()
    for plugin in list_installed_plugins_for_work(work_dir, home=home):
        if not plugin.path.is_dir():
            continue
        for sid, block in _mcp_blocks_for_plugin(plugin.path).items():
            if sid in seen:
                continue
            seen.add(sid)
            out.append(_mcp_entry_dict(sid, block, plugin.name))
    return out


def _mcp_entry_dict(sid: str, block: JsonObject, plugin_name: str) -> JsonObject:
    entry: JsonObject = {
        "id": sid,
        "plugin": plugin_name,
        "title": f"{sid} ({plugin_name})",
        "description": f"From Grok plugin `{plugin_name}`",
        "source": f"plugin:{plugin_name}",
    }
    if "command" in block:
        entry["transport"] = "stdio"
        entry["command"] = str(block.get("command") or "")
        args = block.get("args") or []
        entry["args"] = [str(a) for a in args] if isinstance(args, list) else []
    elif "url" in block:
        transport = block.get("type") or block.get("transport") or "http"
        entry["transport"] = str(transport).lower()
        entry["url"] = str(block.get("url") or "")
    else:
        entry["transport"] = "plugin"
    return entry


def _mcp_blocks_for_plugin(plugin_dir: Path) -> dict[str, JsonObject]:
    blocks: dict[str, JsonObject] = {}
    for rel in _MCP_JSON_RELS:
        _absorb_mcp_servers(blocks, _read_json(plugin_dir / rel), overwrite=True)
    for rel in _MANIFEST_RELS[:3]:
        _absorb_mcp_servers(blocks, _read_json(plugin_dir / rel), overwrite=False)
    return blocks


def _absorb_mcp_servers(
    blocks: dict[str, JsonObject],
    data: JsonValue,
    *,
    overwrite: bool,
) -> None:
    if not isinstance(data, dict):
        return
    servers = data.get("mcpServers") or data.get("mcp_servers")
    if not isinstance(servers, dict):
        return
    for sid, cfg in servers.items():
        name = str(sid).strip()
        if not name or not isinstance(cfg, dict):
            continue
        if overwrite or name not in blocks:
            blocks[name] = cfg


def list_marketplace_catalog(
    *,
    home: Path | None = None,
    include_remote_index: bool = False,
) -> list[MarketplacePluginEntry]:
    """Plugins from local marketplace-cache clones (optional HTTP index)."""
    installed = list_installed_plugins(home=home)
    installed_names = {p.name for p in installed}
    installed_names |= {p.marketplace_plugin for p in installed if p.marketplace_plugin}
    by_key: dict[str, MarketplacePluginEntry] = {}
    _load_cache_catalogs(by_key, marketplace_cache_root(home), installed_names)
    if include_remote_index:
        for entry in _fetch_official_marketplace_entries():
            key = f"{entry.marketplace}:{entry.name}"
            if key not in by_key:
                entry.installed = entry.name in installed_names
                by_key[key] = entry
    return sorted(
        by_key.values(),
        key=lambda e: (e.marketplace.lower(), e.name.lower()),
    )


def _load_cache_catalogs(
    by_key: dict[str, MarketplacePluginEntry],
    cache: Path,
    installed_names: set[str],
) -> None:
    if not cache.is_dir():
        return
    try:
        cache_dirs = sorted(cache.iterdir())
    except OSError:
        return
    for cache_dir in cache_dirs:
        if not cache_dir.is_dir():
            continue
        for mp in (
            cache_dir / ".grok-plugin" / "marketplace.json",
            cache_dir / ".claude-plugin" / "marketplace.json",
        ):
            data = _read_json(mp)
            if not isinstance(data, dict):
                continue
            display = str(data.get("name") or cache_dir.name).strip() or cache_dir.name
            plugins = data.get("plugins")
            if not isinstance(plugins, list):
                continue
            for item in plugins:
                entry = _catalog_item_to_entry(item, display, installed_names)
                if entry is not None:
                    by_key[f"{display}:{entry.name}"] = entry


def _catalog_item_to_entry(
    item: JsonValue,
    display: str,
    installed_names: set[str],
) -> MarketplacePluginEntry | None:
    if not isinstance(item, dict):
        return None
    name = json_as_str(item.get("name")).strip()
    if not name:
        return None
    src = json_as_object(item.get("source"))
    return MarketplacePluginEntry(
        name=name,
        description=json_as_str(item.get("description")).strip(),
        category=json_as_str(item.get("category")).strip(),
        homepage=json_as_str(item.get("homepage")).strip(),
        marketplace=display,
        source_url=json_as_str(src.get("url")).strip(),
        sha=json_as_str(src.get("sha")).strip(),
        keywords=json_as_str_list(item.get("keywords")),
        installed=name in installed_names,
    )


def search_marketplace_catalog(
    query: str,
    *,
    home: Path | None = None,
    limit: int = 80,
) -> list[MarketplacePluginEntry]:
    q = (query or "").strip().lower()
    items = list_marketplace_catalog(home=home)
    if not q:
        return items[:limit]
    return [e for e in items if q in e.search_blob() or q == e.name.lower()][:limit]


def _fetch_official_marketplace_entries() -> list[MarketplacePluginEntry]:
    """Best-effort fetch of xai-org/plugin-marketplace (offline-safe: returns [])."""
    url = (
        "https://raw.githubusercontent.com/xai-org/plugin-marketplace/"
        "main/.grok-plugin/marketplace.json"
    )
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as exc:
        logger.debug("official marketplace fetch failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    display = str(data.get("name") or "xai-official").strip()
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return []
    out: list[MarketplacePluginEntry] = []
    for item in plugins:
        entry = _catalog_item_to_entry(item, display, set())
        if entry is not None:
            out.append(entry)
    return out


def marketplace_news_url() -> str:
    return _NEWS_URL


def official_marketplace_repo() -> str:
    return _OFFICIAL_MARKETPLACE_URL
