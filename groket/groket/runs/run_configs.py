"""Persisted evaluation run configs — separate from trace sessions.

Sessions under ``runs/traces/`` are outcomes (logs, events, feedback).
Run configs under ``runs/run_configs/`` are reusable *recipes* you can
re-launch with different models without needing the old session around.

Layout (per work_dir, same root as traces/feedback_cache):
  <work_dir>/runs/run_configs/
    index.json
    <config_id>.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..constants import DEFAULT_MAX_TURNS, normalize_max_turns
from ..models import (
    JsonObject,
    as_json_object,
    json_as_int,
    json_as_list,
    json_as_object,
    json_as_str,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def run_configs_dir(work_dir: Path) -> Path:
    d = Path(work_dir).expanduser() / "runs" / "run_configs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "").strip().lower())
    s = s.strip("-")[:max_len].strip("-")
    return s or "config"


def normalize_run_inline_skills(raw: object) -> list[JsonObject]:
    """Normalize inline skills from disk, launch, or runner form.

    Accepts:
    - ``[{"id": "…", "content": "…"}, …]`` (persisted / save_from_launch)
    - ``[("id", "content"), …]`` (runner / :class:`RunnerPrefill`)
    """
    out: list[JsonObject] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            sid = str(item.get("id") or "").strip()
            if not sid:
                continue
            out.append({"id": sid, "content": str(item.get("content") or "")})
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            sid = str(item[0] or "").strip()
            if not sid:
                continue
            out.append({"id": sid, "content": str(item[1] or "")})
    return out


@dataclass
class RunConfig:
    """Reusable evaluation recipe (not a session outcome)."""

    config_id: str
    name: str = ""
    prompt: str = ""
    setup_instructions: str = ""
    docker_image: str = "fully-loaded"
    repo_url: str = ""
    repo_branch: str = ""
    # Host directory bind-mounted as /workspace (live tree; no CoW/clone).
    repo_path: str = ""
    # Serialised field; persona controls GitHub write access at launch.
    github_write: bool = False
    # Persona id (see ``personas`` package); env + github_write applied at launch from persona only.
    persona_id: str = ""
    # Per-run MCP/skills/plugins extras (merged onto persona at launch; not on persona disk).
    run_mcp_servers: list[str] = field(default_factory=list)
    run_mcp_definitions: list[JsonObject] = field(default_factory=list)
    run_skills: list[str] = field(default_factory=list)
    run_plugins: list[str] = field(default_factory=list)
    # Env vars added only for this run (e.g. from MCP configure); not written to persona.
    run_env_vars: dict[str, str] = field(default_factory=dict)
    # Run-only inline skills: {id, content} maps (SKILL.md body); merged at launch.
    run_inline_skills: list[JsonObject] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    parallelism: int = 1
    # Grok agent steps per prompt (``--max-turns``).
    max_turns: int = DEFAULT_MAX_TURNS
    # Opt-in: launch with ``grok --yolo`` (default false → --always-approve).
    yolo: bool = False
    notes: str = ""
    # Catalog metadata (task_id / category / label from imported tasks or user)
    wave: int = 0
    task_id: str = ""
    category: str = ""
    label: str = ""  # short column label
    source_run_id: str = ""
    source_session_id: str = ""
    source_session_dir: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_launched_at: str = ""
    launch_count: int = 0

    def display_name(self) -> str:
        if self.name.strip():
            return self.name.strip()
        if (self.repo_path or "").strip():
            base = Path(self.repo_path).expanduser().name
            if base:
                return base
        if self.repo_url:
            repo = self.repo_url.rstrip("/").split("/")[-1]
            if repo:
                return repo
        first = (self.prompt or "").strip().splitlines()[0] if self.prompt else ""
        first = first[:50].strip()
        return first or self.config_id

    def catalog_label(self) -> str:
        """Short tag for the table (label, category, or task_id)."""
        if self.label.strip():
            return self.label.strip()
        if self.category:
            return self.category
        if self.task_id:
            return self.task_id
        return "—"

    def prompt_preview(self, n: int = 60) -> str:
        t = (self.prompt or "").replace("\n", " ").strip()
        return (t[: n - 1] + "…") if len(t) > n else t

    def to_dict(self) -> JsonObject:
        data = asdict(self)
        # Always emit run extras explicitly so older recipes upgraded on save keep keys.
        data["run_inline_skills"] = normalize_run_inline_skills(self.run_inline_skills)
        data["run_env_vars"] = {str(k): str(v) for k, v in (self.run_env_vars or {}).items()}
        data["run_mcp_servers"] = list(self.run_mcp_servers or [])
        data["run_mcp_definitions"] = [
            {str(k): v for k, v in d.items()}
            for d in (self.run_mcp_definitions or [])
            if isinstance(d, dict)
        ]
        data["run_skills"] = list(self.run_skills or [])
        data["run_plugins"] = list(self.run_plugins or [])
        data["persona_id"] = self.persona_id or ""
        return data

    @classmethod
    def from_dict(cls, data: JsonObject) -> RunConfig:
        from ..models import (
            json_as_bool,
            json_as_object,
            json_as_str,
            json_as_str_list,
        )

        env_raw = json_as_object(data.get("run_env_vars"))
        env_vars = {str(k): json_as_str(v) for k, v in env_raw.items()}
        defs: list[JsonObject] = []
        defs_raw = data.get("run_mcp_definitions")
        if isinstance(defs_raw, list):
            for item in defs_raw:
                if isinstance(item, dict):
                    defs.append({str(k): v for k, v in item.items()})
        inline = normalize_run_inline_skills(data.get("run_inline_skills"))
        return cls(
            config_id=json_as_str(data.get("config_id")),
            name=json_as_str(data.get("name")),
            prompt=json_as_str(data.get("prompt")),
            setup_instructions=json_as_str(data.get("setup_instructions")),
            docker_image=json_as_str(data.get("docker_image")) or "fully-loaded",
            models=json_as_str_list(data.get("models")),
            persona_id=json_as_str(data.get("persona_id")),
            repo_url=json_as_str(data.get("repo_url")),
            repo_branch=json_as_str(data.get("repo_branch")),
            repo_path=json_as_str(data.get("repo_path")),
            github_write=json_as_bool(data.get("github_write"), False),
            run_mcp_servers=json_as_str_list(data.get("run_mcp_servers")),
            run_mcp_definitions=defs,
            run_skills=json_as_str_list(data.get("run_skills")),
            run_plugins=json_as_str_list(data.get("run_plugins")),
            run_env_vars=env_vars,
            run_inline_skills=inline,
            parallelism=json_as_int(data.get("parallelism"), 1),
            max_turns=normalize_max_turns(
                data.get("max_turns"),
                default=DEFAULT_MAX_TURNS,
            ),
            yolo=json_as_bool(data.get("yolo"), False),
            notes=json_as_str(data.get("notes")),
            wave=json_as_int(data.get("wave"), 0),
            task_id=json_as_str(data.get("task_id")),
            category=json_as_str(data.get("category")),
            label=json_as_str(data.get("label")),
            source_run_id=json_as_str(data.get("source_run_id")),
            source_session_id=json_as_str(data.get("source_session_id")),
            source_session_dir=json_as_str(data.get("source_session_dir")),
            created_at=json_as_str(data.get("created_at")),
            updated_at=json_as_str(data.get("updated_at")),
            last_launched_at=json_as_str(data.get("last_launched_at")),
            launch_count=json_as_int(data.get("launch_count"), 0),
        )

    def to_runner_prefill(self, models_override: list[str] | None = None):
        from groket.ui.screens.runner import RunnerPrefill

        inline = normalize_run_inline_skills(self.run_inline_skills)
        return RunnerPrefill(
            prompt=self.prompt,
            setup_instructions=self.setup_instructions,
            docker_image=self.docker_image or "fully-loaded",
            repo_url=self.repo_url,
            repo_branch=self.repo_branch,
            repo_path=self.repo_path,
            models=list(models_override if models_override is not None else self.models),
            persona_id=self.persona_id or "",
            run_mcp_servers=list(self.run_mcp_servers),
            run_mcp_definitions=list(self.run_mcp_definitions),
            run_skills=list(self.run_skills),
            run_plugins=list(self.run_plugins),
            run_env_vars=dict(self.run_env_vars),
            run_inline_skills=[(str(x["id"]), str(x.get("content") or "")) for x in inline],
            max_turns=normalize_max_turns(self.max_turns, default=DEFAULT_MAX_TURNS),
            yolo=bool(self.yolo),
        )


class RunConfigStore:
    """CRUD for run configs under a work_dir."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir).expanduser()
        self.root = run_configs_dir(self.work_dir)
        self.index_path = self.root / "index.json"

    def _cfg_path(self, config_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", config_id)
        return self.root / f"{safe}.json"

    def _paths_for_config_id(self, config_id: str) -> list[Path]:
        """All on-disk files that belong to *config_id* (filename or embedded id)."""
        cid = (config_id or "").strip()
        if not cid:
            return []
        found: list[Path] = []
        primary = self._cfg_path(cid)
        if primary.is_file():
            found.append(primary)
        if not self.root.is_dir():
            return found
        for p in self.root.glob("*.json"):
            if p.name == "index.json" or p.name.startswith("."):
                continue
            if p in found:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if str(data.get("config_id") or "").strip() == cid:
                found.append(p)
        return found

    def list_configs(self) -> list[RunConfig]:
        out: list[RunConfig] = []
        if not self.root.exists():
            return out
        for p in sorted(self.root.glob("*.json")):
            if p.name == "index.json" or p.name.startswith("."):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append(RunConfig.from_dict(data))
            except Exception:
                continue
        out.sort(
            key=lambda c: (
                c.updated_at or c.last_launched_at or c.created_at or "",
                c.task_id or c.name or c.config_id,
            ),
            reverse=True,
        )
        return out

    def get(self, config_id: str) -> RunConfig | None:
        p = self._cfg_path(config_id)
        if not p.exists():
            for c in self.list_configs():
                if c.config_id == config_id:
                    return c
            return None
        try:
            return RunConfig.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, cfg: RunConfig) -> RunConfig:
        now = _utc_now_iso()
        if not cfg.config_id:
            cfg.config_id = uuid.uuid4().hex[:12]
        if not cfg.created_at:
            cfg.created_at = now
        cfg.updated_at = now
        p = self._cfg_path(cfg.config_id)
        p.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
        # Drop any stray duplicate files with the same config_id but different names.
        for extra in self._paths_for_config_id(cfg.config_id):
            if extra.resolve() != p.resolve():
                try:
                    extra.unlink()
                except OSError:
                    pass
        self._touch_index()
        return cfg

    def delete(self, config_id: str) -> bool:
        """Permanently remove recipe file(s). Does not touch traces/sessions."""
        cid = (config_id or "").strip()
        if not cid:
            return False
        paths = self._paths_for_config_id(cid)
        removed = False
        for p in paths:
            try:
                p.unlink()
                removed = True
            except OSError:
                pass
        if removed:
            self._touch_index()
        return removed

    def _touch_index(self) -> None:
        try:
            ids = [c.config_id for c in self.list_configs()]
            self.index_path.write_text(
                json.dumps({"configs": ids, "updated_at": _utc_now_iso()}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def create(
        self,
        *,
        prompt: str,
        setup_instructions: str = "",
        docker_image: str = "fully-loaded",
        repo_url: str = "",
        repo_branch: str = "",
        repo_path: str = "",
        models: list[str] | None = None,
        parallelism: int = 1,
        name: str = "",
        notes: str = "",
        wave: int = 0,
        task_id: str = "",
        category: str = "",
        label: str = "",
        source_run_id: str = "",
        source_session_id: str = "",
        source_session_dir: str = "",
        config_id: str | None = None,
        github_write: bool = False,
        max_turns: object | None = None,
        yolo: bool = False,
    ) -> RunConfig:
        cid = config_id or uuid.uuid4().hex[:12]
        if not name:
            if (repo_path or "").strip():
                base = _slug(Path(repo_path).expanduser().name or "local")
            else:
                base = _slug(repo_url.split("/")[-1] if repo_url else prompt[:30])
            name = f"{base}-{cid[:6]}"
        cfg = RunConfig(
            config_id=cid,
            name=name,
            prompt=prompt,
            setup_instructions=setup_instructions,
            docker_image=docker_image or "fully-loaded",
            repo_url=repo_url,
            repo_branch=repo_branch,
            repo_path=repo_path or "",
            github_write=bool(github_write),
            models=list(models or []),
            parallelism=max(1, int(parallelism or 1)),
            max_turns=normalize_max_turns(max_turns, default=DEFAULT_MAX_TURNS),
            yolo=bool(yolo),
            notes=notes,
            wave=int(wave or 0),
            task_id=task_id or "",
            category=category or "",
            label=label or "",
            source_run_id=source_run_id,
            source_session_id=source_session_id,
            source_session_dir=source_session_dir,
        )
        return self.save(cfg)

    def save_from_launch(
        self,
        *,
        prompt: str,
        setup_instructions: str,
        docker_image: str,
        repo_url: str,
        repo_branch: str,
        models: list[str],
        parallelism: int,
        run_id: str = "",
        name: str = "",
        update_existing_id: str | None = None,
        github_write: bool = False,
        persona_id: str = "",
        run_mcp_servers: list[str] | None = None,
        run_mcp_definitions: list | None = None,
        run_skills: list[str] | None = None,
        run_plugins: list[str] | None = None,
        run_env_vars: dict | None = None,
        run_inline_skills: list | None = None,
        max_turns: object | None = None,
        repo_path: str = "",
        yolo: bool = False,
    ) -> RunConfig:
        """Upsert a config when launching an eval (auto-save recipe).

        Always writes run extras (MCP / skills / plugins / env / inline skills)
        when the caller passes them — including empty lists so clears persist.
        Accepts inline skills as ``{id, content}`` maps or ``(id, content)`` pairs.
        """
        inline = (
            normalize_run_inline_skills(run_inline_skills)
            if run_inline_skills is not None
            else None
        )
        if update_existing_id:
            existing = self.get(update_existing_id)
            if existing:
                existing.prompt = prompt
                existing.setup_instructions = setup_instructions
                existing.docker_image = docker_image
                existing.repo_url = repo_url
                existing.repo_branch = repo_branch
                existing.repo_path = repo_path or ""
                existing.github_write = bool(github_write)
                existing.persona_id = persona_id if persona_id else existing.persona_id
                if run_mcp_servers is not None:
                    existing.run_mcp_servers = list(run_mcp_servers)
                if run_mcp_definitions is not None:
                    existing.run_mcp_definitions = [
                        dict(x) for x in run_mcp_definitions if isinstance(x, dict)
                    ]
                if run_skills is not None:
                    existing.run_skills = list(run_skills)
                if run_plugins is not None:
                    existing.run_plugins = list(run_plugins)
                if run_env_vars is not None:
                    existing.run_env_vars = {str(k): str(v) for k, v in dict(run_env_vars).items()}
                if inline is not None:
                    existing.run_inline_skills = list(inline)
                existing.models = list(models)
                existing.parallelism = parallelism
                if max_turns is not None:
                    existing.max_turns = normalize_max_turns(max_turns, default=DEFAULT_MAX_TURNS)
                existing.yolo = bool(yolo)
                existing.source_run_id = run_id or existing.source_run_id
                existing.last_launched_at = _utc_now_iso()
                existing.launch_count = int(existing.launch_count or 0) + 1
                return self.save(existing)
        cfg = self.create(
            prompt=prompt,
            setup_instructions=setup_instructions,
            docker_image=docker_image,
            repo_url=repo_url,
            repo_branch=repo_branch,
            repo_path=repo_path,
            models=models,
            parallelism=parallelism,
            name=name,
            source_run_id=run_id,
            github_write=bool(github_write),
            max_turns=max_turns,
            yolo=yolo,
        )
        cfg.persona_id = persona_id or ""
        cfg.run_mcp_servers = list(run_mcp_servers or [])
        cfg.run_mcp_definitions = [
            dict(x) for x in (run_mcp_definitions or []) if isinstance(x, dict)
        ]
        cfg.run_skills = list(run_skills or [])
        cfg.run_plugins = list(run_plugins or [])
        cfg.run_env_vars = {str(k): str(v) for k, v in dict(run_env_vars or {}).items()}
        cfg.run_inline_skills = list(inline or [])
        cfg.yolo = bool(yolo)
        cfg.last_launched_at = _utc_now_iso()
        cfg.launch_count = 1
        return self.save(cfg)

    def from_session_fields(
        self,
        *,
        prompt: str,
        setup_instructions: str,
        docker_image: str,
        repo_url: str,
        repo_branch: str,
        models: list[str],
        session_id: str = "",
        session_dir: str = "",
        name: str = "",
        repo_path: str = "",
    ) -> RunConfig:
        return self.create(
            prompt=prompt,
            setup_instructions=setup_instructions,
            docker_image=docker_image,
            repo_url=repo_url,
            repo_branch=repo_branch,
            repo_path=repo_path,
            models=models,
            name=name,
            source_session_id=session_id,
            source_session_dir=session_dir,
        )


# Files that may linger in a groket-* run folder after the real session dir is gone.
# Not enough to count as a real trace — safe to drop with the parent run dir.
_ORPHAN_RUN_ONLY_NAMES = frozenset(
    {
        "session_search.sqlite",
        "session_search.sqlite-wal",
        "session_search.sqlite-shm",
        "groket-prompt.txt",
        "groket-config.toml",
        "run.json",
        "prompt.txt",
    }
)


def _is_session_trace_dir(d: Path) -> bool:
    """True if *d* looks like a real Grok session (has events / chat / summary)."""
    if not d.is_dir():
        return False
    for name in ("events.jsonl", "chat_history.jsonl", "summary.json", "updates.jsonl"):
        if (d / name).is_file():
            return True
    return False


def _run_folder_has_sessions(run_dir: Path) -> bool:
    """Any descendant that looks like a session trace?"""
    try:
        for p in run_dir.rglob("*"):
            if p.is_dir() and _is_session_trace_dir(p):
                return True
    except OSError:
        return True  # be conservative
    return False


def _run_folder_is_orphan(run_dir: Path) -> bool:
    """groket-* (or similar) parent with no real session data — only empty dirs / noise files."""
    if not run_dir.is_dir():
        return False
    if _run_folder_has_sessions(run_dir):
        return False
    try:
        for p in run_dir.rglob("*"):
            if p.is_file() and p.name not in _ORPHAN_RUN_ONLY_NAMES:
                # Unexpected real content — do not auto-delete
                return False
    except OSError:
        return False
    return True


def prune_empty_parents_after_session_delete(
    session_dir: Path,
    *,
    stop_at: Path | None = None,
) -> list[Path]:
    """Remove empty intermediate dirs (e.g. ``%2Fworkspace``) and orphan groket-* run roots.

    Walks parents of the deleted session up to *stop_at* (typically ``runs/traces``),
    never removing *stop_at* itself.
    """
    removed: list[Path] = []
    try:
        # session_dir may already be gone; start from its parent
        cur = Path(session_dir).expanduser()
        if not cur.exists():
            cur = cur.parent
        else:
            cur = cur.parent
        try:
            cur = cur.resolve()
        except OSError:
            pass
        stop: Path | None = None
        if stop_at is not None:
            try:
                stop = Path(stop_at).expanduser().resolve()
            except OSError:
                stop = Path(stop_at).expanduser()
    except OSError:
        return removed

    for _ in range(32):  # safety bound
        if not cur.is_dir():
            break
        if stop is not None and cur == stop:
            break
        if stop is not None:
            try:
                cur.relative_to(stop)
            except ValueError:
                break

        try:
            children = list(cur.iterdir())
        except OSError:
            break

        if not children:
            parent = cur.parent
            try:
                cur.rmdir()
                removed.append(cur)
            except OSError:
                break
            cur = parent
            continue

        name = cur.name
        if name.startswith("groket-") or name.startswith("grok-") or name.startswith("%"):
            if _run_folder_is_orphan(cur):
                parent = cur.parent
                try:
                    rmtree_robust(cur)
                    removed.append(cur)
                except OSError:
                    break
                cur = parent
                continue
        break

    return removed


def prune_orphan_trace_runs(
    traces_root: Path,
    *,
    dry_run: bool = False,
) -> JsonObject:
    """Remove ``groket-*`` (and similar) run folders under *traces_root* with no session data.

    Intended for cleanup after older deletes that only removed the inner session UUID
    and left empty parents behind.
    """
    root = Path(traces_root).expanduser()
    if not root.is_dir():
        return {"removed": [], "kept": 0, "errors": [f"not a dir: {root}"], "dry_run": dry_run}

    removed: list[str] = []
    errors: list[str] = []
    kept = 0

    try:
        candidates = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        return {"removed": [], "kept": 0, "errors": [str(exc)], "dry_run": dry_run}

    for run_dir in candidates:
        if not run_dir.is_dir():
            continue
        # Only prune obvious run containers / workspace shells, not arbitrary dirs
        n = run_dir.name
        if not (n.startswith("groket-") or n.startswith("grok-") or n.startswith("%")):
            kept += 1
            continue
        if not _run_folder_is_orphan(run_dir):
            kept += 1
            continue
        if dry_run:
            removed.append(str(run_dir))
            continue
        try:
            rmtree_robust(run_dir)
            removed.append(str(run_dir))
        except Exception as exc:
            errors.append(f"{run_dir}: {exc}")

    return as_json_object(
        {
            "removed": removed,
            "removed_count": len(removed),
            "kept": kept,
            "errors": errors,
            "dry_run": dry_run,
            "traces_root": str(root),
        }
    )


def _host_uid_gid() -> tuple[int, int] | None:
    """Current process uid/gid, or ``None`` when the platform has no getuid."""
    try:
        return os.getuid(), os.getgid()
    except AttributeError:
        return None


def _docker_run_alpine(host_path: Path, cmd: list[str], *, timeout: int = 120) -> tuple[bool, str]:
    """Run *cmd* in alpine with *host_path* at ``/data`` via python-on-whales.

    Same Docker daemon as :class:`~groket.docker.orchestrator.DockerOrchestrator`
    (not a separate ``subprocess`` ``docker`` CLI path). *timeout* is accepted
    for call-site compatibility; whales ``run`` waits until the container exits.
    """
    _ = timeout
    host_path = Path(host_path).expanduser()
    try:
        host_path = host_path.resolve()
    except OSError:
        pass
    if not host_path.exists():
        return True, ""
    try:
        from python_on_whales import DockerClient
    except ImportError:
        return False, "python-on-whales not available"
    try:
        DockerClient().run(
            "alpine",
            list(cmd),
            volumes=[(str(host_path), "/data")],
            remove=True,
        )
        return True, ""
    except Exception as exc:
        msg = str(exc).strip() or type(exc).__name__
        logger.debug("alpine docker run failed for %s: %s", host_path, msg)
        return False, msg[:500]


def chown_path_to_host_user(path: Path, *, uid: int | None = None, gid: int | None = None) -> bool:
    """chown -R *path* to the host user via python-on-whales + alpine.

    Eval containers write as root into the bind-mounted traces volume; the host
    user cannot delete or edit those files without this. Best-effort.
    """
    path = Path(path).expanduser()
    if not path.exists():
        return True
    if uid is None or gid is None:
        ids = _host_uid_gid()
        if ids is None:
            return False
        uid = uid if uid is not None else ids[0]
        gid = gid if gid is not None else ids[1]
    ok, _err = _docker_run_alpine(path, ["chown", "-R", f"{uid}:{gid}", "/data"])
    return ok


def rmtree_robust(path: Path) -> None:
    """``shutil.rmtree`` with whales/alpine chown and ``rm -rf`` fallback."""
    path = Path(path).expanduser()
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        return
    except PermissionError:
        pass
    except OSError:
        pass

    # 1) Fix ownership then normal rmtree
    chown_path_to_host_user(path)
    try:
        if path.exists():
            shutil.rmtree(path)
        return
    except Exception:
        pass

    # 2) Remove inside alpine as root (mount parent, rm child name)
    parent = path.parent
    name = path.name
    if not parent.exists():
        return
    ok, err = _docker_run_alpine(
        parent,
        ["rm", "-rf", f"/data/{name}"],
        timeout=180,
    )
    if not ok and path.exists():
        raise PermissionError(
            f"cannot delete {path} (root-owned from docker traces?). "
            f"chown/rm via python-on-whales failed: {err or 'unknown'}."
        )
    if path.exists():
        shutil.rmtree(path)


def delete_session_dirs(
    session_dirs: list[Path],
    *,
    also_feedback_cache: bool = True,
    feedback_cache_dir: Path | None = None,
    traces_root: Path | None = None,
    prune_empty_parents: bool = True,
) -> JsonObject:
    """Delete session trace directories. Optionally prune matching feedback_cache entries.

    Also removes empty parent dirs (``%2Fworkspace``) and orphan ``groket-*`` run folders
    when the last session in that run is gone — so ``runs/traces`` does not accumulate
    empty shells after delete.

    Handles root-owned files written by eval containers (bind-mount as root in
    ``/root/.grok/sessions``) via docker chown/rm fallback.

    Does **not** delete run_configs. Returns counts + errors.
    """
    deleted = 0
    errors: list[str] = []
    fb_deleted = 0
    parents_pruned: list[str] = []

    stop_at: Path | None = None
    if traces_root is not None:
        try:
            stop_at = Path(traces_root).expanduser().resolve()
        except OSError:
            stop_at = Path(traces_root)

    for sd in session_dirs:
        p = Path(sd)
        try:
            if not p.exists():
                errors.append(f"missing: {p}")
                continue
            if not p.is_dir():
                errors.append(f"not a dir: {p}")
                continue
            sid = p.name
            parent_before = p.parent
            # Infer traces root from path if not given (…/runs/traces/groket-…/…/sid)
            local_stop = stop_at
            if local_stop is None:
                for anc in p.parents:
                    if anc.name == "traces" and anc.parent.name == "runs":
                        local_stop = anc
                        break
            rmtree_robust(p)
            deleted += 1
            if prune_empty_parents:
                for gone in prune_empty_parents_after_session_delete(
                    parent_before, stop_at=local_stop
                ):
                    parents_pruned.append(str(gone))
            if also_feedback_cache and feedback_cache_dir is not None:
                fbd = Path(feedback_cache_dir) / sid
                if fbd.is_dir():
                    try:
                        rmtree_robust(fbd)
                        fb_deleted += 1
                    except Exception as exc:
                        errors.append(f"feedback_cache {sid}: {exc}")
        except Exception as exc:
            errors.append(f"{p}: {exc}")

    return as_json_object(
        {
            "deleted": deleted,
            "feedback_cache_deleted": fb_deleted,
            "parents_pruned": parents_pruned,
            "parents_pruned_count": len(parents_pruned),
            "errors": errors,
            "requested": len(session_dirs),
        }
    )


def session_dirs_for_delete(session_dirs: list[Path]) -> list[Path]:
    """Normalize + de-dupe paths before delete."""
    seen: set[str] = set()
    out: list[Path] = []
    for sd in session_dirs:
        try:
            key = str(Path(sd).expanduser().resolve())
        except OSError:
            key = str(sd)
        if key in seen:
            continue
        seen.add(key)
        out.append(Path(key))
    return out


from ..constants import INTERRUPTED_MARKER_FILENAME as INTERRUPTED_MARKER


def _session_dirs_under(traces_root: Path) -> list[Path]:
    root = Path(traces_root).expanduser()
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        for p in root.rglob("*"):
            if p.is_dir() and _is_session_trace_dir(p):
                out.append(p)
    except OSError:
        pass
    return out


def _turn_outcome_from_events(session_dir: Path) -> str:
    ev_path = session_dir / "events.jsonl"
    if not ev_path.is_file():
        return ""
    try:
        with ev_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("type") == "turn_ended":
                    return str(o.get("outcome") or "ended")
    except OSError:
        pass
    return ""


def _read_interrupted_marker(session_dir: Path) -> dict | None:
    p = session_dir / INTERRUPTED_MARKER
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        return {"reason": "marker present (unreadable)"}


def _session_trace_age_seconds(session_dir: Path) -> float | None:
    """Seconds since newest trace artifact write; None if unknown."""
    from datetime import datetime

    newest = 0.0
    for name in (
        "events.jsonl",
        "chat_history.jsonl",
        "updates.jsonl",
        "summary.json",
        "signals.json",
    ):
        fp = session_dir / name
        try:
            if fp.is_file():
                newest = max(newest, fp.stat().st_mtime)
        except OSError:
            continue
    if newest <= 0:
        return None
    return datetime.now(UTC).timestamp() - newest


def audit_trace_sessions(traces_root: Path) -> JsonObject:
    """Classify sessions under traces_root: ok / interrupted / empty-shell runs.

    Interrupted = has some session data but no turn_ended (and not already marked),
    or has an interrupted marker file. Sessions with recent trace writes (likely still
    running in the background) are reported as ``running``, not interrupted.
    """
    # Align with parser._INCOMPLETE_STALE_SECONDS (live jobs write incrementally).
    running_grace_s = 20 * 60

    root = Path(traces_root).expanduser()
    interrupted: list[JsonObject] = []
    running: list[JsonObject] = []
    ok: list[JsonObject] = []
    shells: list[str] = []

    if root.is_dir():
        try:
            for run_dir in sorted(root.iterdir(), key=lambda p: p.name):
                if not run_dir.is_dir():
                    continue
                n = run_dir.name
                if n.startswith("groket-"):
                    if _run_folder_is_orphan(run_dir):
                        shells.append(str(run_dir))
        except OSError:
            pass

    for sdir in _session_dirs_under(root):
        marker = _read_interrupted_marker(sdir)
        outcome = _turn_outcome_from_events(sdir)
        has_data = any(
            (sdir / n).is_file() and (sdir / n).stat().st_size > 20
            for n in ("events.jsonl", "chat_history.jsonl", "updates.jsonl", "summary.json")
        )
        entry = as_json_object(
            {
                "session_dir": str(sdir),
                "session_id": sdir.name,
                "turn_outcome": outcome,
                "marker": marker,
            }
        )
        if marker:
            entry["status"] = "interrupted"
            interrupted.append(entry)
        elif not has_data:
            entry["status"] = "empty"
            interrupted.append(entry)
        elif not outcome:
            age = _session_trace_age_seconds(sdir)
            if age is not None and age < running_grace_s:
                entry["status"] = "running"
                entry["turn_outcome"] = "running"
                entry["trace_age_s"] = round(age, 1)
                running.append(entry)
            else:
                entry["status"] = "no_turn_ended"
                interrupted.append(entry)
        else:
            entry["status"] = "ok"
            ok.append(entry)

    return as_json_object(
        {
            "traces_root": str(root),
            "ok": ok,
            "ok_count": len(ok),
            "running": running,
            "running_count": len(running),
            "interrupted": interrupted,
            "interrupted_count": len(interrupted),
            "empty_shells": shells,
            "empty_shell_count": len(shells),
        }
    )


def mark_interrupted_sessions(
    traces_root: Path,
    *,
    reason: str = "container_killed_or_no_turn_ended",
    dry_run: bool = False,
) -> JsonObject:
    """Write interrupted marker for sessions missing turn_ended (idempotent)."""
    from datetime import datetime

    audit = audit_trace_sessions(traces_root)
    marked: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    now = datetime.now(UTC).isoformat()

    for item_raw in json_as_list(audit.get("interrupted")):
        item = json_as_object(item_raw)
        sdir = Path(json_as_str(item.get("session_dir")))
        if item.get("marker"):
            skipped.append(str(sdir))
            continue
        if json_as_str(item.get("status")) == "empty":
            skipped.append(str(sdir))
            continue
        payload = {
            "reason": reason,
            "marked_at": now,
            "session_id": sdir.name,
            "prior_turn_outcome": json_as_str(item.get("turn_outcome")),
            "note": "Session has trace data but no turn_ended (likely killed docker / host interrupt).",
        }
        if dry_run:
            marked.append(str(sdir))
            continue
        try:
            (sdir / INTERRUPTED_MARKER).write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            marked.append(str(sdir))
        except Exception as exc:
            errors.append(f"{sdir}: {exc}")

    return as_json_object(
        {
            **audit,
            "marked": marked,
            "marked_count": len(marked),
            "skipped_already_marked": skipped,
            "errors": errors,
            "dry_run": dry_run,
        }
    )


def prune_feedback_cache_orphans(
    cache_dir: Path,
    *,
    dry_run: bool = False,
    traces_root: Path | None = None,
) -> JsonObject:
    """Remove feedback_cache entries whose session_dir no longer exists on disk.

    If *traces_root* is set, also treat a cache entry as orphan when its session_id
    is not present under traces (even if *session_dir* in meta is stale/wrong).
    Always rebuilds index.json from remaining dirs when not dry_run.
    """
    root = Path(cache_dir).expanduser()
    if not root.is_dir():
        return {"removed": [], "kept": 0, "errors": [f"not a dir: {root}"], "dry_run": dry_run}

    trace_sids: set[str] | None = None
    if traces_root is not None:
        tr = Path(traces_root).expanduser()
        if tr.is_dir():
            trace_sids = {p.name for p in _session_dirs_under(tr)}

    removed: list[str] = []
    errors: list[str] = []
    kept = 0
    try:
        children = list(root.iterdir())
    except OSError as exc:
        return {"removed": [], "kept": 0, "errors": [str(exc)], "dry_run": dry_run}

    for child in children:
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if child.name == "index.json":
            continue
        mp = child / "meta.json"
        is_orphan = False
        reason = ""
        if not mp.is_file():
            # No meta but has artifacts — keep unless traces_root says sid is gone
            if trace_sids is not None and child.name not in trace_sids:
                is_orphan = True
                reason = "no_meta_and_sid_not_in_traces"
            else:
                kept += 1
                continue
        else:
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                sdir = Path(meta.get("session_dir") or "")
            except Exception:
                sdir = Path("")
            if sdir and sdir.exists():
                kept += 1
                continue
            if trace_sids is not None and child.name in trace_sids:
                # Trace exists under traces_root but meta.session_dir is wrong — keep dir
                kept += 1
                continue
            is_orphan = True
            reason = "session_dir_missing"
            if trace_sids is not None and child.name not in trace_sids:
                reason = "sid_not_in_traces_and_session_dir_missing"

        if not is_orphan:
            kept += 1
            continue
        if dry_run:
            removed.append(f"{child} ({reason})" if reason else str(child))
            continue
        try:
            shutil.rmtree(child)
            removed.append(str(child))
        except Exception as exc:
            errors.append(f"{child}: {exc}")

    # Full index rebuild from remaining dirs (fixes 73+ stale index rows)
    index_rebuild: JsonObject = {}
    if not dry_run:
        try:
            index_rebuild = rebuild_feedback_cache_index(root)
        except Exception as exc:
            errors.append(f"index rebuild: {exc}")
    else:
        # dry-run: report how many index entries would drop
        try:
            idx_path = root / "index.json"
            if idx_path.is_file():
                data = json.loads(idx_path.read_text(encoding="utf-8"))
                sessions = data.get("sessions") if isinstance(data, dict) else None
                if not isinstance(sessions, dict):
                    sessions = data if isinstance(data, dict) else {}
                live_dirs = {
                    p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")
                }
                would_drop = [s for s in sessions if s not in live_dirs]
                index_rebuild = {
                    "index_entries_before": len(sessions),
                    "index_entries_after_estimate": len(live_dirs)
                    - len(
                        [r for r in removed if Path(str(r).split()[0]).name in live_dirs or True]
                    ),
                    "index_stale_keys": len(would_drop),
                    "stale_keys_sample": would_drop[:20],
                }
        except Exception as exc:
            errors.append(f"index dry-run: {exc}")

    return as_json_object(
        {
            "removed": removed,
            "removed_count": len(removed),
            "kept": kept,
            "errors": errors,
            "dry_run": dry_run,
            "cache_dir": str(root),
            "traces_root": str(traces_root) if traces_root else "",
            "index_rebuild": index_rebuild,
        }
    )


def rebuild_feedback_cache_index(cache_dir: Path) -> JsonObject:
    """Rewrite feedback_cache/index.json from on-disk session dirs (source of truth)."""
    from datetime import datetime

    root = Path(cache_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    sessions: dict[str, dict] = {}
    skipped: list[str] = []

    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        return {"sessions": 0, "errors": [str(exc)]}

    for child in children:
        if not child.is_dir() or child.name.startswith("_"):
            continue
        mp = child / "meta.json"
        if not mp.is_file():
            skipped.append(child.name)
            continue
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            skipped.append(child.name)
            continue
        if not isinstance(meta, dict):
            skipped.append(child.name)
            continue
        sid = str(meta.get("session_id") or child.name)
        has_report = (child / "report.md").is_file()
        status = str(meta.get("status") or "")
        if has_report and status != "error":
            status = "has_report"
        sessions[sid] = {
            "session_id": sid,
            "session_dir": meta.get("session_dir") or "",
            "fingerprint": meta.get("fingerprint") or "",
            "status": status,
            "analyzed_at": meta.get("analyzed_at") or "",
            "repo": meta.get("repo") or "",
            "task_id": meta.get("task_id") or "",
            "model_id": meta.get("model_id") or "",
            "turn_outcome": meta.get("turn_outcome") or "",
            "has_report": has_report or bool(meta.get("has_report")),
            "error": meta.get("error") or "",
        }

    payload = {
        "version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
        "sessions": sessions,
    }
    (root / "index.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return as_json_object(
        {
            "sessions": len(sessions),
            "skipped_no_meta": skipped,
            "index_path": str(root / "index.json"),
        }
    )


def validate_feedback_cache_sync(
    traces_root: Path,
    cache_dir: Path,
) -> JsonObject:
    """Cross-check traces vs feedback_cache; report orphans, missing, stale index/fp."""
    tr = Path(traces_root).expanduser()
    cd = Path(cache_dir).expanduser()

    trace_dirs = _session_dirs_under(tr)
    trace_sids = {p.name: p for p in trace_dirs}

    cache_sids: dict[str, Path] = {}
    if cd.is_dir():
        for child in cd.iterdir():
            if child.is_dir() and not child.name.startswith("_"):
                cache_sids[child.name] = child

    index_sids: set[str] = set()
    index_path = cd / "index.json"
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            sess = data.get("sessions") if isinstance(data, dict) else None
            if isinstance(sess, dict):
                index_sids = set(sess.keys())
        except Exception:
            pass

    orphans: list[JsonObject] = []
    for sid, cpath in sorted(cache_sids.items()):
        mp = cpath / "meta.json"
        sdir_ok = False
        session_dir_str = ""
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                session_dir_str = str(meta.get("session_dir") or "")
                if session_dir_str and Path(session_dir_str).exists():
                    sdir_ok = True
            except Exception:
                pass
        in_traces = sid in trace_sids
        if not sdir_ok and not in_traces:
            orphans.append(
                as_json_object({"session_id": sid, "reason": "gone", "cache_dir": str(cpath)})
            )
        elif not sdir_ok and in_traces:
            orphans.append(
                as_json_object(
                    {
                        "session_id": sid,
                        "reason": "meta_session_dir_stale",
                        "cache_dir": str(cpath),
                        "actual_session_dir": str(trace_sids[sid]),
                    }
                )
            )

    missing_cache = [
        {"session_id": sid, "session_dir": str(p)}
        for sid, p in sorted(trace_sids.items())
        if sid not in cache_sids
    ]

    index_only = sorted(index_sids - set(cache_sids.keys()))
    dirs_not_in_index = sorted(set(cache_sids.keys()) - index_sids)

    # Status / artifact counts
    status_counts: dict[str, int] = {}
    has_report = 0
    for sid, cpath in cache_sids.items():
        if (cpath / "report.md").is_file():
            has_report += 1
        st = "unknown"
        mp = cpath / "meta.json"
        if mp.is_file():
            try:
                st = str(json.loads(mp.read_text(encoding="utf-8")).get("status") or "unknown")
            except Exception:
                st = "bad_meta"
        else:
            st = "no_meta"
        status_counts[st] = status_counts.get(st, 0) + 1

    return as_json_object(
        {
            "traces_root": str(tr),
            "cache_dir": str(cd),
            "trace_sessions": len(trace_sids),
            "cache_dirs": len(cache_sids),
            "index_entries": len(index_sids),
            "orphans": orphans,
            "orphan_count": len(orphans),
            "missing_cache": missing_cache,
            "missing_cache_count": len(missing_cache),
            "index_only": index_only,
            "index_only_count": len(index_only),
            "dirs_not_in_index": dirs_not_in_index,
            "dirs_not_in_index_count": len(dirs_not_in_index),
            "status_counts": status_counts,
            "has_report_count": has_report,
            # "in_sync" = no dangling/orphan cache or index issues (missing_cache is normal)
            "in_sync": (
                len([o for o in orphans if o.get("reason") == "gone"]) == 0
                and len(index_only) == 0
                and len(dirs_not_in_index) == 0
            ),
            "meta_stale_count": len(
                [o for o in orphans if o.get("reason") == "meta_session_dir_stale"]
            ),
        }
    )
