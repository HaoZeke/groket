"""Persona domain model and on-disk store (UI-agnostic)."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..models import JsonObject
from ..paths import personas_home


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def personas_dir(work_dir: Path | None = None) -> Path:
    """App-global personas directory (``~/.groket/personas``).

    *work_dir* is ignored; personas always live under :data:`paths.APP_HOME`.
    """

    return personas_home()


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "").strip().lower())
    s = s.strip("-")[:max_len].strip("-")
    return s or "persona"


@dataclass
class Persona:
    """Reusable identity/settings for an evaluation run.

    Attach via ``RunConfig.persona_id`` or pass directly at launch.

    GitHub access is persona-owned: ``github_write`` plus optional ``github_token``
    (stored under ``~/.groket/personas/`` — treat that dir as secret).
    If the token field is empty at launch, ``github_token_env`` (host env var name)
    or host ``GH_TOKEN`` / ``GITHUB_TOKEN`` may still supply a token when write is on.
    """

    persona_id: str
    name: str = ""
    description: str = ""
    # Inject GH_TOKEN and git credential helper for push/PR ops.
    github_write: bool = False
    # Fine-grained PAT or classic token for this persona (optional; sensitive).
    github_token: str = ""
    # If set and github_token empty: read token from this host env var at launch.
    github_token_env: str = ""
    # Extra env vars injected into the container (non-secret preferred; MCP secrets often here).
    env_vars: dict[str, str] = field(default_factory=dict)
    # Default docker profile / image alias when run config omits one.
    docker_image: str = ""
    # Optional git identity hints applied inside the container (agent may still override).
    git_user_name: str = ""
    git_user_email: str = ""
    # MCP servers enabled for this persona (ids from catalog / host config.toml names).
    # Separate from skills — tools vs prompt packages.
    mcp_servers: list[str] = field(default_factory=list)
    # Full configs from interactive registry/catalog setup (id, url, headers, command, …).
    # Applied at launch as [mcp_servers.id] blocks; takes precedence over catalog for same id.
    mcp_definitions: list[JsonObject] = field(default_factory=list)
    # When True, strip host [mcp_servers.*] from eval config and only apply persona/catalog MCP.
    mcp_replace_host: bool = True
    # Extra raw TOML fragments appended for MCP (advanced; optional [mcp_servers.x] blocks).
    mcp_extra_toml: str = ""
    # Skill names enabled for this persona (copied/mounted into container ~/.grok/skills).
    skills: list[str] = field(default_factory=list)
    # Skill names explicitly disabled in container [skills].disabled (even if on disk).
    skills_disabled: list[str] = field(default_factory=list)
    # Marketplace plugin names; enabled via [plugins] in eval config.toml.
    plugins: list[str] = field(default_factory=list)
    # Free-form notes for operators.
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.persona_id:
            self.persona_id = uuid.uuid4().hex[:12]
        if not self.name:
            self.name = self.persona_id
        if not self.created_at:
            self.created_at = _utc_now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> JsonObject:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: JsonObject) -> Persona:
        env = data.get("env_vars") or {}
        if not isinstance(env, dict):
            env = {}

        def _str_list(key: str) -> list[str]:
            raw = data.get(key) or []
            if not isinstance(raw, list):
                return []
            out: list[str] = []
            seen: set[str] = set()
            for item in raw:
                s = str(item or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
            return out

        defs_raw = data.get("mcp_definitions") or []
        mcp_definitions: list[JsonObject] = []
        if isinstance(defs_raw, list):
            for item in defs_raw:
                if isinstance(item, dict) and str(item.get("id") or "").strip():
                    mcp_definitions.append(dict(item))

        def _as_bool(raw: object, *, default: bool = False) -> bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)):
                return bool(raw)
            if raw is None:
                return default
            s = str(raw).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off", ""):
                return False
            return default

        return cls(
            persona_id=str(data.get("persona_id") or ""),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            github_write=_as_bool(data.get("github_write"), default=False),
            github_token=str(data.get("github_token") or ""),
            github_token_env=str(data.get("github_token_env") or "").strip(),
            env_vars={str(k): str(v) for k, v in env.items()},
            docker_image=str(data.get("docker_image") or ""),
            git_user_name=str(data.get("git_user_name") or ""),
            git_user_email=str(data.get("git_user_email") or ""),
            mcp_servers=_str_list("mcp_servers"),
            mcp_definitions=mcp_definitions,
            mcp_replace_host=_as_bool(data.get("mcp_replace_host"), default=True),
            mcp_extra_toml=str(data.get("mcp_extra_toml") or ""),
            skills=_str_list("skills"),
            skills_disabled=_str_list("skills_disabled"),
            plugins=_str_list("plugins"),
            notes=str(data.get("notes") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def apply_to_env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """Merge persona env (+ optional git identity) into a flat env map."""
        out = dict(base or {})
        out.update(self.env_vars)
        if self.git_user_name:
            out.setdefault("GIT_AUTHOR_NAME", self.git_user_name)
            out.setdefault("GIT_COMMITTER_NAME", self.git_user_name)
        if self.git_user_email:
            out.setdefault("GIT_AUTHOR_EMAIL", self.git_user_email)
            out.setdefault("GIT_COMMITTER_EMAIL", self.git_user_email)
        return out

    def merge_github_write(self, run_flag: bool = False) -> bool:
        """GitHub write is persona-only (*run_flag* is ignored)."""
        _ = run_flag
        return bool(self.github_write)

    def resolve_github_token(self) -> str:
        """Token for this persona: stored value, then named host env, then empty."""

        direct = (self.github_token or "").strip()
        if direct:
            return direct
        env_name = (self.github_token_env or "").strip()
        if env_name:
            return (os.environ.get(env_name) or "").strip()
        return ""


class PersonaStore:
    """CRUD for personas under ``~/.groket/personas/``."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir).expanduser()
        self.root = personas_dir()
        self._index_path = self.root / "index.json"

    def _load_index(self) -> JsonObject:
        if not self._index_path.is_file():
            return {"personas": []}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"personas": []}
        except Exception:
            return {"personas": []}

    def _save_index(self, ids: list[str]) -> None:
        payload = {"personas": ids, "updated_at": _utc_now_iso()}
        self._index_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _path_for(self, persona_id: str) -> Path:
        return self.root / f"{persona_id}.json"

    def list(self) -> list[Persona]:
        idx = self._load_index()
        raw_ids = idx.get("personas") or []
        ids = raw_ids if isinstance(raw_ids, list) else []
        out: list[Persona] = []
        for pid in ids:
            p = self.get(str(pid))
            if p:
                out.append(p)
        # Also pick up orphan files not in index
        for fp in sorted(self.root.glob("*.json")):
            if fp.name == "index.json":
                continue
            pid = fp.stem
            if any(x.persona_id == pid for x in out):
                continue
            p = self.get(pid)
            if p:
                out.append(p)
        return out

    def get(self, persona_id: str) -> Persona | None:
        fp = self._path_for(persona_id)
        if not fp.is_file():
            return None
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            return Persona.from_dict(data) if isinstance(data, dict) else None
        except Exception:
            return None

    def save(self, persona: Persona) -> Persona:
        persona.updated_at = _utc_now_iso()
        if not persona.created_at:
            persona.created_at = persona.updated_at
        fp = self._path_for(persona.persona_id)
        fp.write_text(json.dumps(persona.to_dict(), indent=2) + "\n", encoding="utf-8")
        ids = [p.persona_id for p in self.list() if p.persona_id != persona.persona_id]
        ids.append(persona.persona_id)
        # de-dupe preserve order
        seen: set[str] = set()
        ordered: list[str] = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        self._save_index(ordered)
        return persona

    def delete(self, persona_id: str) -> bool:
        fp = self._path_for(persona_id)
        existed = fp.is_file()
        if existed:
            fp.unlink()
        ids = [p.persona_id for p in self.list() if p.persona_id != persona_id]
        self._save_index(ids)
        return existed

    def ensure_defaults(self) -> None:
        """Create a couple of starter personas if the store is empty."""
        if self.list():
            return
        self.save(
            Persona(
                persona_id="default",
                name="default",
                description="No special GitHub write; standard env.",
                github_write=False,
            )
        )
        self.save(
            Persona(
                persona_id="github-writer",
                name="github-writer",
                description=(
                    "Enable gh/git push. Set PAT (or github_token_env) on this persona; "
                    "host GH_TOKEN/GITHUB_TOKEN used only as launch fallback."
                ),
                github_write=True,
            )
        )
