"""Apply persona MCP/skills to eval config.toml and on-disk skill packs."""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import JsonObject
from .catalog import get_mcp_entry, resolve_skill_path
from .registry import definition_to_toml_block

if TYPE_CHECKING:
    from ..runs.personas import Persona

logger = logging.getLogger(__name__)


class PluginStageError(RuntimeError):
    """Selected plugin(s) could not be staged for the eval container."""


_MCP_SECTION_RE = re.compile(r"(?im)^\s*\[mcp_servers\.[^\]]+\]\s*$")


def _strip_mcp_sections(config_text: str) -> str:
    lines = config_text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if _MCP_SECTION_RE.match(stripped) or stripped.lower() == "[mcp_servers]":
                skipping = True
                continue
            skipping = False
            out.append(line)
            continue
        if skipping:
            continue
        out.append(line)
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _extract_host_mcp_section(host_text: str, server_id: str) -> str:
    sid = (server_id or "").strip()
    if not sid or not host_text:
        return ""
    lines = host_text.splitlines()
    header_pat = re.compile(rf"(?im)^\s*\[mcp_servers\.{re.escape(sid)}\]\s*$")
    start = None
    for i, line in enumerate(lines):
        if header_pat.match(line):
            start = i
            break
    if start is None:
        return ""
    block = [lines[start]]
    for line in lines[start + 1 :]:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            break
        block.append(line)
    return "\n".join(block).rstrip() + "\n"


def _toml_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def _mcp_block_from_catalog_entry(entry) -> str:
    sid = entry.id
    lines = [f"[mcp_servers.{sid}]", "enabled = true"]
    transport = (entry.transport or "http").lower()
    if transport in ("http", "sse") and entry.url:
        lines.append(f'url = "{_toml_escape(entry.url)}"')
    elif transport == "stdio" and entry.command:
        lines.append(f'command = "{_toml_escape(entry.command)}"')
        if entry.args:
            args_lit = ", ".join(f'"{_toml_escape(a)}"' for a in entry.args)
            lines.append(f"args = [{args_lit}]")
    else:
        return ""
    return "\n".join(lines) + "\n"


def apply_persona_mcp_to_config_toml(
    base_config: str,
    persona: Persona | None,
    *,
    work_dir: Path | None = None,
    host_config_text: str = "",
) -> str:
    text = base_config or ""
    if persona is None:
        return text

    selected = [s.strip() for s in (persona.mcp_servers or []) if (s or "").strip()]
    extra = (persona.mcp_extra_toml or "").strip()
    replace_host = bool(getattr(persona, "mcp_replace_host", True))
    definitions = list(getattr(persona, "mcp_definitions", None) or [])
    def_by_id: dict[str, dict] = {}
    for d in definitions:
        if isinstance(d, dict):
            did = str(d.get("id") or "").strip()
            if did:
                def_by_id[did] = d

    if not selected and not extra and not def_by_id:
        if replace_host:
            return _strip_mcp_sections(text)
        return text

    if replace_host:
        text = _strip_mcp_sections(text)

    for did in def_by_id:
        if did not in selected:
            selected.append(did)

    blocks: list[str] = []
    for sid in selected:
        block = ""
        if sid in def_by_id:
            block = definition_to_toml_block(def_by_id[sid])
        entry = get_mcp_entry(sid, work_dir=work_dir)
        if not block and entry and entry.transport != "host":
            block = _mcp_block_from_catalog_entry(entry)
        if not block and host_config_text:
            block = _extract_host_mcp_section(host_config_text, sid)
        if block:
            if re.search(r"(?im)^\s*enabled\s*=\s*false\s*$", block):
                block = re.sub(
                    r"(?im)^(\s*enabled\s*=\s*)false(\s*)$",
                    r"\1true\2",
                    block,
                    count=1,
                )
            elif not re.search(r"(?im)^\s*enabled\s*=", block):
                blines = block.splitlines()
                if blines:
                    blines.insert(1, "enabled = true")
                    block = "\n".join(blines) + "\n"
            blocks.append(block.rstrip() + "\n")

    if extra:
        blocks.append(extra if extra.endswith("\n") else extra + "\n")

    if not blocks:
        return text

    if not text.endswith("\n"):
        text += "\n"
    text += "\n# --- persona MCP ---\n"
    text += "\n".join(blocks)
    if not text.endswith("\n"):
        text += "\n"
    return text


def skills_config_toml_fragment(*, skills_enabled: list[str], skills_disabled: list[str]) -> str:
    disabled = [d.strip() for d in (skills_disabled or []) if (d or "").strip()]
    lines = ["", "# --- persona skills ---", "[skills]", "paths = []"]
    if disabled:
        dis_lit = ", ".join(f'"{_toml_escape(d)}"' for d in disabled)
        lines.append(f"disabled = [{dis_lit}]")
    else:
        lines.append("disabled = []")
    lines.extend(
        ["", "[compat.cursor]", "skills = false", "", "[compat.claude]", "skills = false", ""]
    )
    _ = skills_enabled
    return "\n".join(lines)


def write_inline_skills(dest: Path, inline: list[tuple[str, str]] | None) -> list[str]:
    """Write one-off skill packs (``SKILL.md``) under *dest*; return skill ids written."""
    if not inline:
        return []
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, body in inline:
        sid = (name or "").strip()
        sid = "".join(c if c.isalnum() or c in "-_" else "-" for c in sid).strip("-_")
        if not sid:
            continue
        pack = dest / sid
        try:
            pack.mkdir(parents=True, exist_ok=True)
            text = (body or "").strip()
            if not text:
                text = f"---\nname: {sid}\ndescription: Inline run skill\n---\n\n# {sid}\n"
            (pack / "SKILL.md").write_text(
                text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8"
            )
            written.append(sid)
        except OSError:
            continue
    return written


def prepare_persona_skills_dir(
    dest: Path,
    persona: Persona | None,
    *,
    work_dir: Path | None = None,
    inline_skills: list[tuple[str, str]] | None = None,
) -> Path | None:
    """Stage skill packs for the eval container.

    Copies named skills from the host catalog, then writes *inline_skills*
    (``(id, SKILL.md body)``) for run-only packs.
    """
    names = [
        s.strip()
        for s in ((persona.skills or []) if persona is not None else [])
        if (s or "").strip()
    ]
    inline = list(inline_skills or [])
    if not names and not inline:
        return None

    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for name in names:
        src = resolve_skill_path(name, work_dir=work_dir)
        if src is None or not src.is_dir():
            continue
        target = dest / name
        try:
            shutil.copytree(src, target, dirs_exist_ok=True)
            copied += 1
        except OSError:
            continue

    written = write_inline_skills(dest, inline)
    if copied == 0 and not written:
        try:
            dest.rmdir()
        except OSError:
            pass
        return None
    return dest


def apply_persona_skills_to_config_toml(base_config: str, persona: Persona | None) -> str:
    if persona is None:
        return base_config
    if not persona.skills and not persona.skills_disabled:
        return base_config
    text = base_config or ""
    if not text.endswith("\n"):
        text += "\n"
    text += skills_config_toml_fragment(
        skills_enabled=list(persona.skills or []),
        skills_disabled=list(persona.skills_disabled or []),
    )
    return text


_PLUGINS_SECTION_RE = re.compile(r"(?im)^\s*\[plugins\]\s*$")


def _strip_plugins_sections(config_text: str) -> str:
    """Remove host ``[plugins]`` tables so persona enabled list is authoritative."""
    lines = (config_text or "").splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if _PLUGINS_SECTION_RE.match(stripped):
                skipping = True
                continue
            skipping = False
            out.append(line)
            continue
        if skipping:
            continue
        out.append(line)
    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def plugins_config_toml_fragment(*, plugins_enabled: list[str]) -> str:
    """``[plugins] enabled = [...]`` for Grok inside the eval container.

    Always emits a table (including ``enabled = []``) so host marketplace
    plugins cannot remain enabled without a staged install.
    """
    names = [n.strip() for n in (plugins_enabled or []) if (n or "").strip()]
    lit = ", ".join(f'"{_toml_escape(n)}"' for n in names)
    return f"\n# --- persona Grok plugins ---\n[plugins]\nenabled = [{lit}]\ndisabled = []\n"


def resolve_plugin_path(name: str, *, work_dir: Path | None = None) -> Path | None:
    """Return a local plugin directory for *name* when one exists under the work tree."""
    from .marketplace import is_grok_plugin_dir, list_installed_plugins_for_work

    n = (name or "").strip()
    if not n:
        return None
    for plugin in list_installed_plugins_for_work(work_dir):
        if plugin.name == n and plugin.path.is_dir() and is_grok_plugin_dir(plugin.path):
            return plugin.path
        if plugin.marketplace_plugin == n and plugin.path.is_dir():
            return plugin.path
    return None


def _copy_plugin_skills(skills_root: Path, skills_dest: Path) -> int:
    """Copy ``skills_root/<id>/SKILL.md`` packs into *skills_dest*. Returns count."""
    if not skills_root.is_dir():
        return 0
    dest = Path(skills_dest)
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    try:
        for child in sorted(skills_root.iterdir()):
            if not child.is_dir() or not (child / "SKILL.md").is_file():
                continue
            target = dest / child.name
            try:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
                n += 1
            except OSError:
                logger.debug("could not stage skill %s", child.name, exc_info=True)
    except OSError:
        return n
    return n


def prepare_persona_plugins_dir(
    dest: Path,
    persona: Persona | None,
    *,
    work_dir: Path | None = None,
    skills_dest: Path | None = None,
) -> Path | None:
    """Stage plugin checkouts + manifest for the eval container (one host path).

    **Contract:** every plugin on the persona/run list is materialized under
    ``dest/checkouts/<name>/`` on the **host** (copy of host install, else git
    clone from the marketplace catalog). The container entrypoint only runs
    ``grok plugin install --trust`` on those trees — no git inside the image.

    Plugin skill packs are also copied into *skills_dest* (stable
    ``~/.grok/skills/<id>/`` surface after the entrypoint seeds the stage).

    :returns: *dest* when every requested plugin was staged.
    :raises RuntimeError: When any requested plugin cannot be staged (so the
        launch does not enable plugins that will not install).
    """
    if persona is None:
        return None
    names = [s.strip() for s in (getattr(persona, "plugins", None) or []) if (s or "").strip()]
    if not names:
        return None

    from .marketplace import (
        list_installed_plugins_for_work,
        materialize_plugin,
        plugin_install_specs,
    )

    specs = plugin_install_specs(names, work_dir=work_dir)
    specs_by_name = {
        str(s.get("name") or "").strip().lower(): s
        for s in specs
        if isinstance(s, dict) and str(s.get("name") or "").strip()
    }

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    checkouts = dest / "checkouts"
    checkouts.mkdir(parents=True, exist_ok=True)

    by_name: dict[str, Path] = {}
    for plugin in list_installed_plugins_for_work(work_dir):
        if plugin.path.is_dir():
            by_name[plugin.name.lower()] = plugin.path
            if plugin.marketplace_plugin:
                by_name[plugin.marketplace_plugin.lower()] = plugin.path

    staged: list[JsonObject] = []
    failed: list[str] = []
    skills_copied = 0
    for name in names:
        target = checkouts / name
        src = by_name.get(name.lower())
        ok = False
        if src is not None and src.is_dir():
            try:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(
                    src,
                    target,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
                )
                ok = True
            except OSError as exc:
                logger.warning("plugin checkout copy failed for %s: %s", name, exc)
        if not ok:
            # Host-side catalog clone — still one path: materialize on host.
            got = materialize_plugin(name, target, work_dir=work_dir)
            ok = got is not None and target.is_dir()
        if not ok:
            failed.append(name)
            logger.warning("plugin %r not staged", name)
            continue
        row: JsonObject = {
            "name": name,
            "checkout": f"checkouts/{name}",
        }
        # Keep source_url/sha so the container can fall back to git clone
        # (origin/main behaviour) if the staged checkout is missing.
        spec = specs_by_name.get(name.lower())
        if isinstance(spec, dict):
            if spec.get("source_url"):
                row["source_url"] = str(spec["source_url"])
            if spec.get("sha"):
                row["sha"] = str(spec["sha"])
        staged.append(row)
        if skills_dest is not None:
            skills_copied += _copy_plugin_skills(target / "skills", skills_dest)

    if failed:
        raise PluginStageError(
            "Could not stage plugin(s) for install: "
            + ", ".join(failed)
            + ". Install on the host (grok plugin install) or ensure the "
            "marketplace catalog has a clone URL."
        )
    if not staged:
        return None

    manifest = dest / "plugins-manifest.json"
    try:
        manifest.write_text(json.dumps(staged, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise PluginStageError(f"failed to write plugins manifest {manifest}: {exc}") from exc
    if skills_copied:
        logger.info("staged %d skill pack(s) from plugins into %s", skills_copied, skills_dest)
    return dest


def apply_persona_plugins_to_config_toml(base_config: str, persona: Persona | None) -> str:
    """Apply persona/run plugin enablement for an eval container.

    Always strips host ``[plugins]`` tables. Eval containers only get plugins
    that were staged for install; leaving host ``enabled = ["superpowers", …]``
    made Grok report plugins as enabled without ``grok plugin install``.
    """
    names = [
        s.strip()
        for s in ((getattr(persona, "plugins", None) or []) if persona is not None else [])
        if (s or "").strip()
    ]
    # Host config often has [plugins] enabled = [...] from interactive use —
    # never leave that list in the eval config without a matching staged install.
    text = _strip_plugins_sections(base_config or "")
    if text and not text.endswith("\n"):
        text += "\n"
    text += plugins_config_toml_fragment(plugins_enabled=names)
    return text
