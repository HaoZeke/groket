"""Live search against the official MCP registry (registry.modelcontextprotocol.io).

Successful list responses are cached under ``~/.groket/cache/mcp-registry/``
(and in-process) so repeated picker searches do not wait on the network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ..models import (
    JsonObject,
    JsonValue,
    as_json_object,
    json_as_mapping_list,
    json_as_object,
    json_as_str,
)
from ..paths import mcp_registry_cache_dir
from .catalog import McpCatalogEntry

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
DEFAULT_CACHE_TTL_SEC = 6 * 60 * 60
_USER_AGENT = "groket/1.0 (mcp-registry-search)"
# url -> (expires_at unix, payload)
_MEM_CACHE: dict[str, tuple[float, JsonObject]] = {}


@dataclass
class RegistryRemote:
    transport: str = "http"  # http | sse | streamable-http
    url: str = ""
    headers: list[JsonObject] = field(default_factory=list)  # registry header templates


@dataclass
class RegistryPackage:
    registry_type: str = ""  # npm | pypi | oci
    identifier: str = ""
    version: str = ""
    runtime_hint: str = ""  # npx | uvx | docker
    transport: str = "stdio"


@dataclass
class RegistryServerHit:
    """One registry server (normalized for UI + config)."""

    name: str  # registry qualified name e.g. ai.waystation/slack
    title: str = ""
    description: str = ""
    version: str = ""
    repository_url: str = ""
    remotes: list[RegistryRemote] = field(default_factory=list)
    packages: list[RegistryPackage] = field(default_factory=list)
    status: str = "active"

    @property
    def preferred_remote(self) -> RegistryRemote | None:
        if not self.remotes:
            return None
        # Prefer streamable-http / http over sse for Grok url= form
        for r in self.remotes:
            t = (r.transport or "").lower()
            if t in ("streamable-http", "http"):
                return r
        return self.remotes[0]

    @property
    def preferred_package(self) -> RegistryPackage | None:
        if not self.packages:
            return None
        for p in self.packages:
            if (p.runtime_hint or "").lower() in ("npx", "uvx"):
                return p
        return self.packages[0]

    def suggested_id(self) -> str:
        """Short safe id for [mcp_servers.id] (persona mcp_servers list)."""
        base = (self.name or "mcp").split("/")[-1]
        base = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-").lower()
        return (base or "mcp")[:48]

    def to_catalog_entry(self) -> McpCatalogEntry:
        remote = self.preferred_remote
        pkg = self.preferred_package
        transport = "host"
        url = ""
        command = ""
        args: list[str] = []
        needs_env: list[str] = []

        if remote and remote.url:
            t = (remote.transport or "http").lower()
            transport = "sse" if t == "sse" else "http"
            url = remote.url
            for h in remote.headers or []:
                if not isinstance(h, dict):
                    continue
                tmpl = str(h.get("value") or "")
                # Bearer {smithery_api_key} -> SMITHERY_API_KEY
                for m in re.finditer(r"\{([a-zA-Z0-9_]+)\}", tmpl):
                    env_key = m.group(1).upper()
                    if env_key not in needs_env:
                        needs_env.append(env_key)
                name = str(h.get("name") or "")
                if name and h.get("isRequired") and not needs_env:
                    needs_env.append(name.upper().replace("-", "_"))
        elif pkg and pkg.identifier:
            transport = "stdio"
            rt = (pkg.runtime_hint or "").lower()
            ident = pkg.identifier
            if rt == "uvx" or pkg.registry_type == "pypi":
                command = "uvx"
                args = [ident]
            elif rt == "docker" or pkg.registry_type == "oci":
                command = "docker"
                args = ["run", "-i", "--rm", ident]
            else:
                command = "npx"
                args = ["-y", ident]

        tags = ["registry"]
        if remote:
            tags.append("remote")
        if pkg:
            tags.append("stdio")
        return McpCatalogEntry(
            id=self.suggested_id(),
            title=self.title or self.name,
            description=(self.description or "")[:300],
            transport=transport,
            url=url,
            command=command,
            args=args,
            needs_env=needs_env,
            tags=tags,
            source="registry",
        )

    def header_templates(self) -> list[dict[str, str]]:
        remote = self.preferred_remote
        if not remote:
            return []
        out: list[dict[str, str]] = []
        for h in remote.headers or []:
            if not isinstance(h, dict):
                continue
            out.append(
                {
                    "name": str(h.get("name") or ""),
                    "value": str(h.get("value") or ""),
                    "description": str(h.get("description") or ""),
                    "required": "1" if h.get("isRequired") else "0",
                    "secret": "1" if h.get("isSecret") else "0",
                }
            )
        return out

    def registry_page_url(self, *, base_url: str = DEFAULT_REGISTRY_BASE) -> str:
        """Public registry entry page (best-effort; name is URL-encoded path segment)."""
        if not self.name:
            return ""
        base = (base_url or DEFAULT_REGISTRY_BASE).rstrip("/")
        # Official site uses the server name in the path (slash-separated namespaces).
        seg = "/".join(urllib.parse.quote(p, safe="") for p in self.name.split("/"))
        return f"{base}/{seg}"

    def docs_links(self, *, base_url: str = DEFAULT_REGISTRY_BASE) -> list[tuple[str, str]]:
        """(label, url) pairs for UI: repo, registry page, package pages when known."""
        links: list[tuple[str, str]] = []
        if self.repository_url:
            links.append(("repository", self.repository_url.strip()))
        reg = self.registry_page_url(base_url=base_url)
        if reg:
            links.append(("registry", reg))
        pkg = self.preferred_package
        if pkg and pkg.identifier:
            rt = (pkg.registry_type or "").lower()
            ident = pkg.identifier.strip()
            if rt == "npm" or (pkg.runtime_hint or "").lower() == "npx":
                # npm package name may include scope (@org/pkg)
                links.append(("npm", f"https://www.npmjs.com/package/{ident}"))
            elif rt == "pypi" or (pkg.runtime_hint or "").lower() == "uvx":
                links.append(("pypi", f"https://pypi.org/project/{ident}/"))
            elif rt == "oci" or (pkg.runtime_hint or "").lower() == "docker":
                # docker hub-ish; best effort
                name = ident.split("/")[-1] if "/" in ident else ident
                links.append(
                    (
                        "image",
                        f"https://hub.docker.com/r/{ident}"
                        if "/" in ident
                        else f"https://hub.docker.com/_/{name}",
                    )
                )
        # Deduplicate by URL
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for lab, url in links:
            u = (url or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            out.append((lab, u))
        return out

    def detail_summary(self) -> str:
        """Multi-line rich-friendly text for picker detail pane."""
        entry = self.to_catalog_entry()
        lines: list[str] = [
            f"[bold]{self.title or self.name}[/bold]",
            f"[dim]{self.name}[/dim]"
            + (f"  v{self.version}" if self.version else "")
            + (f"  [{self.status}]" if self.status else ""),
        ]
        desc = (self.description or "").strip()
        if desc:
            lines.append(desc[:500] + ("…" if len(desc) > 500 else ""))
        lines.append("")
        lines.append(f"[dim]transport=[/dim]{entry.transport}  [dim]id suggestion=[/dim]{entry.id}")
        if entry.url:
            lines.append(f"[dim]url=[/dim]{entry.url}")
        if entry.command:
            args = " ".join(str(a) for a in (entry.args or []))
            lines.append(f"[dim]command=[/dim]{entry.command} {args}".rstrip())
        if entry.needs_env:
            lines.append(f"[dim]needs env/headers:[/dim] {', '.join(entry.needs_env)}")
        hdrs = self.header_templates()
        if hdrs:
            for h in hdrs[:4]:
                req = " required" if h.get("required") == "1" else ""
                sec = " secret" if h.get("secret") == "1" else ""
                lines.append(
                    f"[dim]header[/dim] {h.get('name')}: {h.get('value') or '—'} {req} {sec}"
                    + (f" — {h.get('description')}" if h.get("description") else "")
                )
        links = self.docs_links()
        if links:
            lines.append("")
            lines.append("[bold]Links[/bold]  [dim]open in browser on host[/dim]")
            for lab, url in links:
                lines.append(f"  [cyan]{lab}[/cyan]  {url}")
        else:
            lines.append("")
            lines.append("[dim]No repository/docs URL on this registry entry.[/dim]")
        return "\n".join(lines)


def _http_get_json(url: str, *, timeout: float = 12.0) -> JsonObject:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, application/problem+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def clear_registry_cache(*, disk: bool = True) -> None:
    """Drop in-process registry responses; optionally delete on-disk entries."""
    _MEM_CACHE.clear()
    if not disk:
        return
    root = mcp_registry_cache_dir()
    if not root.is_dir():
        return
    for fp in root.glob("*.json"):
        try:
            fp.unlink()
        except OSError:
            logger.debug("failed to remove registry cache file %s", fp, exc_info=True)


def _cache_file_for_url(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return mcp_registry_cache_dir() / f"{digest}.json"


def _load_cached_response(url: str, ttl_sec: float) -> JsonObject | None:
    """Return a fresh cached registry JSON body for *url*, or ``None``."""
    if ttl_sec <= 0:
        return None
    now = time.time()
    mem = _MEM_CACHE.get(url)
    if mem is not None:
        expires_at, mem_body = mem
        if now < expires_at:
            return mem_body
        _MEM_CACHE.pop(url, None)
    fp = _cache_file_for_url(url)
    if not fp.is_file():
        return None
    try:
        envelope = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("failed to read registry cache %s", fp, exc_info=True)
        return None
    if not isinstance(envelope, dict):
        return None
    try:
        fetched_at = float(envelope.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if now - fetched_at > ttl_sec:
        return None
    raw_data = envelope.get("data")
    if not isinstance(raw_data, dict):
        return None
    body: JsonObject = raw_data
    _MEM_CACHE[url] = (fetched_at + ttl_sec, body)
    return body


def _store_cached_response(url: str, data: JsonObject, ttl_sec: float) -> None:
    """Remember a successful registry JSON body for *url* (memory + disk)."""
    if ttl_sec <= 0:
        return
    now = time.time()
    _MEM_CACHE[url] = (now + ttl_sec, data)
    fp = _cache_file_for_url(url)
    envelope: JsonObject = {"url": url, "fetched_at": now, "data": data}
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(envelope), encoding="utf-8")
    except OSError:
        logger.debug("failed to write registry cache %s", fp, exc_info=True)


def _parse_hit(item: JsonObject) -> RegistryServerHit | None:
    if not isinstance(item, dict):
        return None
    server = item.get("server") if isinstance(item.get("server"), dict) else item
    if not isinstance(server, dict):
        return None
    name = str(server.get("name") or "").strip()
    if not name:
        return None
    meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
    official = (
        meta.get("io.modelcontextprotocol.registry/official") if isinstance(meta, dict) else {}
    )
    status = "active"
    if isinstance(official, dict):
        status = str(official.get("status") or "active")

    remotes: list[RegistryRemote] = []
    for r in json_as_mapping_list(server.get("remotes")):
        remotes.append(
            RegistryRemote(
                transport=json_as_str(r.get("type") or r.get("transport")) or "http",
                url=json_as_str(r.get("url")).strip(),
                headers=json_as_mapping_list(r.get("headers")),
            )
        )

    packages: list[RegistryPackage] = []
    for p in json_as_mapping_list(server.get("packages")):
        transport = json_as_object(p.get("transport"))
        packages.append(
            RegistryPackage(
                registry_type=json_as_str(p.get("registryType") or p.get("registry_type")),
                identifier=json_as_str(p.get("identifier")),
                version=json_as_str(p.get("version")),
                runtime_hint=json_as_str(p.get("runtimeHint") or p.get("runtime_hint")),
                transport=json_as_str(transport.get("type")) or "stdio",
            )
        )

    repo = json_as_object(server.get("repository"))
    return RegistryServerHit(
        name=name,
        title=json_as_str(server.get("title")) or name.split("/")[-1],
        description=json_as_str(server.get("description")),
        version=json_as_str(server.get("version")),
        repository_url=json_as_str(repo.get("url")),
        remotes=remotes,
        packages=packages,
        status=status,
    )


def _registry_list_urls(base: str, query: str, limit: int) -> list[str]:
    """Candidate list-servers URLs (v0.1 preferred; v0 fallback)."""
    q = query.strip()
    lim = str(max(1, limit))
    # Official API freeze is v0.1; ``version=latest`` collapses multi-version spam.
    v01 = urllib.parse.urlencode({"search": q, "limit": lim, "version": "latest"})
    v0 = urllib.parse.urlencode({"search": q, "limit": lim})
    return [
        f"{base}/v0.1/servers?{v01}",
        f"{base}/v0.1/servers?{v0}",
        f"{base}/v0/servers?{v0}",
    ]


def _hits_from_servers(servers: list[JsonValue], query: str, limit: int) -> list[RegistryServerHit]:
    """Parse and de-dupe registry list items; client-filter only when oversized."""
    hits: list[RegistryServerHit] = []
    seen: set[str] = set()
    for item in servers:
        if not isinstance(item, dict):
            continue
        hit = _parse_hit(item)
        if not hit or hit.name in seen:
            continue
        seen.add(hit.name)
        hits.append(hit)
    if len(hits) <= limit:
        return hits
    ql = query.lower()
    filtered = [
        h
        for h in hits
        if ql in h.name.lower()
        or ql in (h.title or "").lower()
        or ql in (h.description or "").lower()
    ]
    return (filtered or hits)[:limit]


def search_registry(
    query: str,
    *,
    base_url: str = DEFAULT_REGISTRY_BASE,
    limit: int = 30,
    timeout: float = 12.0,
    use_cache: bool = True,
    cache_ttl: float = DEFAULT_CACHE_TTL_SEC,
) -> tuple[list[RegistryServerHit], str]:
    """Search the official MCP registry. Returns (hits, error_message).

    When *use_cache* is true, successful responses are reused from
    :func:`~groket.paths.mcp_registry_cache_dir` (and memory) for *cache_ttl*
    seconds (default 6 hours). Pass ``use_cache=False`` or ``cache_ttl=0`` to
    force a network fetch.
    """
    q = (query or "").strip()
    if not q:
        return [], ""
    base = (base_url or DEFAULT_REGISTRY_BASE).rstrip("/")
    ttl = float(cache_ttl) if use_cache else 0.0
    last_err = ""
    for url in _registry_list_urls(base, q, limit):
        if ttl > 0:
            cached = _load_cached_response(url, ttl)
            if cached is not None:
                servers = cached.get("servers")
                if isinstance(servers, list):
                    return _hits_from_servers(servers, q, limit), ""
        try:
            data = _http_get_json(url, timeout=timeout)
            servers = data.get("servers")
            if not isinstance(servers, list):
                last_err = "unexpected response (no servers list)"
                continue
            _store_cached_response(url, data, ttl)
            return _hits_from_servers(servers, q, limit), ""
        except TimeoutError as exc:
            last_err = f"timeout: {exc}"
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            reason = exc.reason
            last_err = f"network: {reason}"
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                last_err = f"timeout: {reason}"
        except Exception as exc:
            last_err = str(exc)[:120]
    return [], last_err or "registry search failed"


def registry_hit_to_definition(hit: RegistryServerHit, *, server_id: str = "") -> JsonObject:
    """Serializable persona mcp_definitions entry from a registry hit."""
    entry = hit.to_catalog_entry()
    sid = (server_id or entry.id or hit.suggested_id()).strip()
    sid = re.sub(r"[^a-zA-Z0-9._-]+", "-", sid).strip("-")[:48] or "mcp"
    remote = hit.preferred_remote
    headers_out: dict[str, str] = {}
    if remote:
        for h in remote.headers or []:
            if not isinstance(h, dict):
                continue
            name = str(h.get("name") or "").strip()
            tmpl = str(h.get("value") or "").strip()
            if not name:
                continue

            # Convert {var_name} placeholders to ${VAR_NAME} for Grok env indirection in config
            def _repl(m: re.Match[str]) -> str:
                return "${" + m.group(1).upper() + "}"

            val = re.sub(r"\{([a-zA-Z0-9_]+)\}", _repl, tmpl)
            headers_out[name] = val

    links = hit.docs_links()
    return as_json_object(
        {
            "id": sid,
            "registry_name": hit.name,
            "title": hit.title or sid,
            "description": (hit.description or "")[:800],
            "transport": entry.transport,
            "url": entry.url,
            "command": entry.command,
            "args": list(entry.args),
            "headers": headers_out,
            "needs_env": list(entry.needs_env),
            "source": "registry",
            "version": hit.version,
            "status": hit.status,
            "repository_url": (hit.repository_url or "").strip(),
            "registry_url": hit.registry_page_url(),
            "docs_links": [{"label": lab, "url": url} for lab, url in links],
        }
    )


def definition_to_toml_block(defn: JsonObject) -> str:
    """Build [mcp_servers.id] TOML from a stored definition (with optional headers)."""
    sid = str(defn.get("id") or "").strip()
    if not sid:
        return ""
    lines = [f"[mcp_servers.{sid}]", "enabled = true"]
    transport = str(defn.get("transport") or "http").lower()
    url = str(defn.get("url") or "").strip()
    command = str(defn.get("command") or "").strip()
    args = defn.get("args") or []
    if not isinstance(args, list):
        args = []

    def _esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')

    if transport in ("http", "sse") and url:
        lines.append(f'url = "{_esc(url)}"')
    elif transport == "stdio" and command:
        lines.append(f'command = "{_esc(command)}"')
        if args:
            args_lit = ", ".join(f'"{_esc(str(a))}"' for a in args)
            lines.append(f"args = [{args_lit}]")
    else:
        return ""

    headers = defn.get("headers") or {}
    if isinstance(headers, dict) and headers:
        # TOML inline table for headers
        parts = [f'{k} = "{_esc(str(v))}"' for k, v in headers.items() if k]
        if parts:
            lines.append("headers = { " + ", ".join(parts) + " }")
    return "\n".join(lines) + "\n"
