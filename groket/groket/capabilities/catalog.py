"""MCP catalog (YAML + host config) and skills filesystem scan.

Grok Build **plugins** are a separate capability (see ``marketplace.py`` /
``apply.prepare_persona_plugins_dir``) — not flattened into MCP or skills lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_PKG_CATALOG = Path(__file__).resolve().parents[3] / "capabilities" / "mcp_catalog.yaml"


@dataclass
class McpCatalogEntry:
    id: str
    title: str = ""
    description: str = ""
    transport: str = "http"
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    needs_env: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    source: str = "catalog"

    def search_blob(self) -> str:
        parts = [self.id, self.title, self.description, " ".join(self.tags), self.transport]
        return " ".join(parts).lower()


@dataclass
class SkillEntry:
    name: str
    path: Path
    description: str = ""
    source: str = "user"

    def search_blob(self) -> str:
        return f"{self.name} {self.description} {self.source}".lower()


def _catalog_paths(work_dir: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    if work_dir is not None:
        wd = Path(work_dir).expanduser()
        paths.append(wd / "capabilities" / "mcp_catalog.yaml")
        paths.append(wd / "runs" / "capabilities" / "mcp_catalog.yaml")
    paths.append(_PKG_CATALOG)
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def list_host_mcp_server_names(host_config: Path | None = None) -> list[str]:
    cfg = host_config or (Path.home() / ".grok" / "config.toml")
    if not cfg.is_file():
        return []
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"(?im)^\s*\[mcp_servers\.([^\]]+)\]\s*$", text):
        name = m.group(1).strip().strip('"').strip("'")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def load_mcp_catalog(work_dir: Path | None = None) -> list[McpCatalogEntry]:
    by_id: dict[str, McpCatalogEntry] = {}
    for fp in _catalog_paths(work_dir):
        if not fp.is_file():
            continue
        try:
            data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, list):
            continue
        for item in servers:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "").strip()
            if not sid or sid.startswith("_"):
                continue
            args = item.get("args") or []
            if not isinstance(args, list):
                args = []
            needs = item.get("needs_env") or []
            if not isinstance(needs, list):
                needs = []
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            profiles = item.get("profiles") or []
            if not isinstance(profiles, list):
                profiles = []
            by_id[sid] = McpCatalogEntry(
                id=sid,
                title=str(item.get("title") or sid),
                description=str(item.get("description") or "").strip(),
                transport=str(item.get("transport") or "http").strip().lower(),
                url=str(item.get("url") or "").strip(),
                command=str(item.get("command") or "").strip(),
                args=[str(a) for a in args],
                needs_env=[str(e) for e in needs],
                tags=[str(t) for t in tags],
                profiles=[str(p) for p in profiles],
                source="catalog",
            )
    for name in list_host_mcp_server_names():
        if name not in by_id:
            by_id[name] = McpCatalogEntry(
                id=name,
                title=f"{name} (host config)",
                description="Defined in ~/.grok/config.toml; enable by id on persona.",
                transport="host",
                source="host",
            )
    return sorted(by_id.values(), key=lambda e: e.id.lower())


def search_mcp_catalog(
    query: str, *, work_dir: Path | None = None, limit: int = 80
) -> list[McpCatalogEntry]:
    q = (query or "").strip().lower()
    items = load_mcp_catalog(work_dir)
    if not q:
        return items[:limit]
    scored: list[tuple[int, McpCatalogEntry]] = []
    for e in items:
        if q in e.search_blob() or q == e.id.lower():
            score = 0 if e.id.lower() == q else (1 if q in e.id.lower() else 2)
            scored.append((score, e))
    scored.sort(key=lambda t: (t[0], t[1].id.lower()))
    return [e for _, e in scored[:limit]]


def _skill_description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if line.strip().lower().startswith("description:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("---"):
            return s[:200]
    return ""


def _scan_skills_root(
    root: Path,
    *,
    source: str,
    include_implicit: bool = False,
) -> list[SkillEntry]:
    if not root.is_dir():
        return []
    from .skill_gen import is_implicit_mcp_companion_skill

    out: list[SkillEntry] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = child.name.strip()
        if not name or name.startswith("."):
            continue
        if not include_implicit and is_implicit_mcp_companion_skill(name, skill_md):
            continue
        out.append(
            SkillEntry(
                name=name,
                path=child,
                description=_skill_description(skill_md),
                source=source,
            )
        )
    return out


def scan_host_skills(
    work_dir: Path | None = None,
    *,
    include_implicit: bool = False,
) -> list[SkillEntry]:
    """Scan standalone skill packs (not Grok plugins — enable those via ``Persona.plugins``).

    Precedence (first wins on name collision): work_dir → user → bundled.

    By default **hides** groket-generated MCP companion skills (``use-*-mcp``),
    which are attached implicitly when configuring an MCP — they must not appear
    in skill pickers. Pass ``include_implicit=True`` for apply/debug tooling.
    """
    by_name: dict[str, SkillEntry] = {}
    roots: list[tuple[Path, str]] = [
        (Path.home() / ".grok" / "skills", "user"),
        (Path.home() / ".grok" / "bundled" / "skills", "bundled"),
    ]
    if work_dir is not None:
        wd = Path.expanduser(Path(work_dir))
        roots.insert(0, (wd / "skills", "work_dir"))
        roots.insert(0, (wd / "runs" / "skills", "work_dir"))
    for root, source in roots:
        for entry in _scan_skills_root(root, source=source, include_implicit=include_implicit):
            if entry.name not in by_name:
                by_name[entry.name] = entry
    return sorted(by_name.values(), key=lambda e: e.name.lower())


def search_skills(
    query: str,
    *,
    work_dir: Path | None = None,
    limit: int = 80,
    include_implicit: bool = False,
) -> list[SkillEntry]:
    """Search skills for pickers — implicit MCP companions excluded by default."""
    q = (query or "").strip().lower()
    items = scan_host_skills(work_dir, include_implicit=include_implicit)
    if not q:
        return items[:limit]
    return [e for e in items if q in e.search_blob() or q == e.name.lower()][:limit]


def get_mcp_entry(server_id: str, *, work_dir: Path | None = None) -> McpCatalogEntry | None:
    sid = (server_id or "").strip()
    if not sid:
        return None
    for e in load_mcp_catalog(work_dir):
        if e.id == sid:
            return e
    if sid in list_host_mcp_server_names():
        return McpCatalogEntry(
            id=sid, title=f"{sid} (host config)", transport="host", source="host"
        )
    return None


def resolve_skill_path(name: str, *, work_dir: Path | None = None) -> Path | None:
    n = (name or "").strip()
    if not n:
        return None
    # Include implicit MCP companions so persona apply still finds them on disk.
    for e in scan_host_skills(work_dir, include_implicit=True):
        if e.name == n:
            return e.path
    return None
