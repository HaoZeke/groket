"""Batch evaluation runner -- run eval tasks through Docker orchestrator.

Provides the EvalTask dataclass, task loading from YAML, and an async
run_batch() function that spins up containers for each (task, model) pair.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..constants import DEFAULT_MAX_TURNS, normalize_max_turns
from ..models import JsonObject
from ..paths import default_work_dir, eval_results_path, user_models_path

logger = logging.getLogger(__name__)

# Lazy docker imports: model catalog helpers must work without python_on_whales.


def _docker_types():
    from ..docker.orchestrator import ContainerConfig, DockerOrchestrator

    return ContainerConfig, DockerOrchestrator


# Fallback only when ~/.grok/models_cache.json is missing (prefer live catalog).
MODELS = [
    "v9-pizzaparty",
    "v9-dietcoke",
    "grok-build",
]

MODEL_SHORTS = {
    "v9": "v9",
    "grok-build": "build",
    "dietcoke": "dietcoke",
    "v9-dietcoke": "dietcoke",
    "pizzaparty": "pizzaparty",
    "v9-pizzaparty": "pizzaparty",
}

_USER_MODELS_PATH = user_models_path()
_GROK_MODELS_CACHE = Path.home() / ".grok" / "models_cache.json"

REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "xhigh"
# Grok CLI ``--effort`` / ``--reasoning-effort`` (0.2.x) accepts only these.
CLI_REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high")


def split_model_effort(token: str) -> tuple[str, str]:
    """Split ``model`` or ``model:effort`` into ``(model_id, effort)``."""
    raw = (token or "").strip()
    if not raw:
        return "", ""
    if ":" in raw:
        model, _, effort = raw.rpartition(":")
        model_s = model.strip()
        effort_l = effort.strip().lower()
        if model_s and effort_l in REASONING_EFFORTS:
            return model_s, effort_l
    return raw, ""


def join_model_effort(model: str, effort: str = "") -> str:
    """Format a model token, appending ``:effort`` when *effort* is set."""
    mid = (model or "").strip()
    eff = (effort or "").strip().lower()
    return f"{mid}:{eff}" if mid and eff in REASONING_EFFORTS else mid


def normalize_reasoning_effort(effort: str | None, *, default: str = "") -> str:
    """Return a known product effort level, or *default* (may be empty)."""
    eff = (effort or "").strip().lower()
    if eff in REASONING_EFFORTS:
        return eff
    d = (default or "").strip().lower()
    return d if d in REASONING_EFFORTS else (default or "")


def cli_reasoning_effort(effort: str | None) -> str:
    """Map product effort to a value accepted by Grok CLI ``--effort``.

    Product tokens include ``xhigh`` / ``max`` (model:effort UI). Current Grok
    Build CLI only accepts ``low`` | ``medium`` | ``high``; higher product
    levels map to ``high``. Empty / unknown → ``""`` (omit the flag).
    """
    eff = normalize_reasoning_effort(effort)
    if not eff:
        return ""
    if eff in CLI_REASONING_EFFORTS:
        return eff
    if eff in ("xhigh", "max"):
        return "high"
    return ""


def _read_models_cache() -> dict:
    """Raw ``~/.grok/models_cache.json`` (same source ``grok models`` uses)."""
    if not _GROK_MODELS_CACHE.exists():
        return {}
    try:
        data = json.loads(_GROK_MODELS_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _models_block(data: dict | None = None) -> dict:
    data = data if data is not None else _read_models_cache()
    block = data.get("models") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def _entry_info(entry: JsonObject | str) -> JsonObject:
    if not isinstance(entry, dict):
        return {}
    info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
    return info if isinstance(info, dict) else {}


def active_model_catalog() -> dict[str, dict]:
    """Active models from cache: canonical_id → {id, name, aliases}.

    Prefer this over stale built-in lists; retired variants (e.g. bottlerocket)
    simply disappear from the catalog when Grok removes them.
    """
    block = _models_block()
    out: dict[str, dict] = {}
    for key, entry in block.items():
        info = _entry_info(entry)
        mid = str(info.get("model") or key).strip()
        if not mid:
            continue
        name = str(info.get("name") or "").strip()
        aliases: set[str] = {mid.lower(), str(key).strip().lower()}
        if name:
            aliases.add(name.lower())
            # v9-pizzaparty → pizzaparty already in name; also accept short tail
            if "-" in mid:
                aliases.add(mid.split("-", 1)[-1].lower())
        rec = out.setdefault(mid, {"id": mid, "name": name or mid, "aliases": set()})
        rec["aliases"] |= aliases
        if name and not rec.get("name"):
            rec["name"] = name
    # Freeze alias sets as sorted lists for callers that want JSON-safe data
    for rec in out.values():
        rec["aliases"] = sorted(rec["aliases"])
    return out


def active_model_ids() -> list[str]:
    """Canonical model ids currently available (order from models_cache.json)."""
    block = _models_block()
    if not block:
        return list(MODELS)
    ids: list[str] = []
    seen: set[str] = set()
    for key, entry in block.items():
        info = _entry_info(entry)
        mid = str(info.get("model") or key).strip()
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids if ids else list(MODELS)


def models_catalog_help_text() -> str:
    """Short hint for Runner UI: active models from cache."""
    ids = active_model_ids()
    if not ids:
        return "no models_cache.json — run `grok models` once on the host"
    return "active: " + ", ".join(ids)


def _read_user_models_yaml() -> list[str] | None:
    """Optional preferred model tokens from ``~/.groket/models.yaml`` (not a hard override)."""
    if not _USER_MODELS_PATH.exists():
        return None
    try:
        data = yaml.safe_load(_USER_MODELS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "models" in data:
            return [str(m).strip() for m in (data["models"] or []) if str(m).strip()]
        if isinstance(data, list):
            return [str(m).strip() for m in data if str(m).strip()]
    except Exception:
        pass
    return None


def load_models() -> list[str]:
    """Active models from ``~/.grok/models_cache.json`` (same as ``grok models``).

    Optional ``models.yaml`` only *orders / prefers* entries that still resolve in the
    live catalog; retired ids (bottlerocket, bare ``v9``, etc.) are dropped, not shown.
    Full catalog is always returned so the Runner UI cannot get stuck on a stale override.
    """
    live = active_model_ids()
    if not live:
        return list(MODELS)

    preferred = _read_user_models_yaml()
    if not preferred:
        return live

    ordered: list[str] = []
    seen: set[str] = set()
    for token in preferred:
        # resolve_model_id is defined below; use catalog lookup here to avoid forward issues.
        hit = _catalog_lookup(token)
        if hit and hit not in seen:
            ordered.append(hit)
            seen.add(hit)
    for mid in live:
        if mid not in seen:
            ordered.append(mid)
            seen.add(mid)
    return ordered if ordered else live


# Static aliases only applied when the target still exists in the live catalog.
_MODEL_ALIASES: dict[str, str] = {
    "pizzaparty": "v9-pizzaparty",
    "dietcoke": "v9-dietcoke",
    "btnb": "v9",  # may fail active check if bare v9 is gone
}


def _catalog_lookup(raw: str) -> str | None:
    """Map user input to a canonical active model id, or None if not in catalog."""
    cat = active_model_catalog()
    if not cat:
        return None
    low = (raw or "").strip().lower()
    if not low:
        return None
    # Exact canonical id
    for mid in cat:
        if mid.lower() == low:
            return mid
    # Alias / display name
    for mid, rec in cat.items():
        aliases = rec.get("aliases") or []
        if low in {a.lower() for a in aliases}:
            return mid
        name = str(rec.get("name") or "").strip().lower()
        if name and name == low:
            return mid
    return None


def default_model_id() -> str:
    """Default model: config.toml [models].default if active, else first in catalog."""
    import tomllib

    ids = active_model_ids()
    id_set = {i.lower(): i for i in ids}
    cfg = Path.home() / ".grok" / "config.toml"
    if cfg.exists():
        try:
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            val = data.get("models", {}).get("default", "")
            if val:
                if val in ids:
                    return val
                hit = _catalog_lookup(val)
                if hit:
                    return hit
                low = val.lower()
                if low in id_set:
                    return id_set[low]
        except Exception:
            pass
    return ids[0] if ids else "v9-pizzaparty"


def resolve_model_id(model: str, *, require_active: bool = False) -> str:
    """Normalize a launch token to a canonical id, preserving ``:effort`` when set.

    Accepts bare ``model`` or ``model:effort`` (low|medium|high|xhigh|max).
    Uses ``~/.grok/models_cache.json`` (refresh on host with ``grok models``).
    When *require_active* is True, returns "" if the **base model** is not in
    the catalog — effort suffixes must not fail the active check.
    """
    mid, effort = split_model_effort(model)
    raw = mid or (model or "").strip()
    if not raw:
        return raw

    # Live catalog first (source of truth for what works in containers)
    hit = _catalog_lookup(raw)
    if hit:
        return join_model_effort(hit, effort)

    low = raw.lower()
    if low in _MODEL_ALIASES:
        alias_target = _MODEL_ALIASES[low]
        hit2 = _catalog_lookup(alias_target)
        if hit2:
            return join_model_effort(hit2, effort)
        if require_active:
            return ""
        return join_model_effort(alias_target, effort)

    # Bare "v9" with no catalog entry: use account default if available
    if low == "v9":
        default = default_model_id()
        if default and (not require_active or _catalog_lookup(default)):
            return join_model_effort(default, effort)

    if require_active:
        return ""
    return join_model_effort(raw, effort)


def resolve_model_ids(models: list[str]) -> list[str]:
    """Resolve a list; preserve order, drop empties. Does not filter inactive."""
    out: list[str] = []
    for m in models:
        r = resolve_model_id(m, require_active=False)
        if r:
            out.append(r)
    return out


def validate_models_for_launch(
    models: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve and keep only models active in models_cache.json.

    Returns ``(active_canonical_ids, skip_messages)``.
    Empty active list means nothing to launch (caller should error).
    """
    active: list[str] = []
    skips: list[str] = []
    seen: set[str] = set()
    catalog_ids = active_model_ids()
    catalog_hint = ", ".join(catalog_ids) if catalog_ids else "(empty — run: grok models)"

    for m in models:
        raw = (m or "").strip()
        if not raw:
            continue
        resolved = resolve_model_id(raw, require_active=True)
        if not resolved:
            # Try non-strict resolve for a clearer message
            attempted = resolve_model_id(raw, require_active=False) or raw
            skips.append(
                f"{raw!r} → {attempted!r} is not in the active model list "
                f"(models_cache.json / `grok models`). Active: {catalog_hint}"
            )
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        active.append(resolved)

    return active, skips


AUTH_JSON = Path.home() / ".grok" / "auth.json"
GROK_CONFIG = Path.home() / ".grok" / "config.toml"
WORK_DIR = default_work_dir()

# EvalTask dataclass


@dataclass
class EvalTask:
    """A single evaluation task: prompt + optional repo + optional initial commands.

    ``repo_url`` may be empty for no-repo jobs (scratch workspace + initial commands only).
    ``repo_path`` bind-mounts a host directory as ``/workspace`` (live tree; no clone).
    ``setup_instructions`` / ``initial_commands`` are multiline shell run *before* grok
    starts (after an optional git clone into /workspace).
    Extra authoring fields (``turns``, ``persona_id``, ``models``, …) come from the
    tasks schema (:mod:`groket.runs.task_schema`).
    """

    task_id: str
    prompt: str
    repo_url: str = ""
    repo_branch: str = ""
    repo_path: str = ""
    setup_instructions: str = ""
    docker_image: str = "fully-loaded"
    description: str = ""
    category: str = "regular"
    domain: str = "general-swe"  # firmware, devops, general-swe, ml-data, web-vibe, exploratory
    horizon: str = "long"  # short, long, autonomous
    persona_id: str = ""
    models: list[str] | None = None
    tags: list[str] | None = None
    env: dict[str, str] | None = None
    turns: list[str] | None = None
    success_hints: list[str] | None = None
    # Fork from ended session (TUI f parity): host path + parent Grok session id.
    resume_session_dir: str = ""
    resume_session_id: str = ""
    # Grok agent steps per prompt (``--max-turns``); default :data:`~groket.constants.DEFAULT_MAX_TURNS`.
    max_turns: int = DEFAULT_MAX_TURNS
    # Opt-in: ``grok --yolo`` (default false → --always-approve).
    yolo: bool = False

    @property
    def has_repo(self) -> bool:
        return bool((self.repo_url or "").strip() or (self.repo_path or "").strip())

    @property
    def has_local_path(self) -> bool:
        return bool((self.repo_path or "").strip())

    @property
    def has_resume(self) -> bool:
        return bool((self.resume_session_dir or "").strip())


# Task loading


def _task_setup_from_entry(entry: dict) -> str:
    """Accept setup_instructions or initial_commands (multiline YAML | / > supported)."""
    for key in ("initial_commands", "setup_instructions", "setup"):
        val = entry.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            # Allow list-of-lines form in YAML
            return "\n".join(str(x) for x in val)
        return str(val)
    return ""


def load_tasks(
    path: Path,
    category: str | None = None,
    *,
    strict: bool = False,
) -> list[EvalTask]:
    """Load eval tasks from a YAML file. There is no built-in default catalog.

    Validates via :mod:`groket.runs.task_schema`. ``repo_url`` is optional — omit
    it for no-repo jobs (prompt and/or initial commands in an empty /workspace).
    """
    from .task_schema import load_task_file, task_definition_to_eval_task

    doc = load_task_file(path)
    _ = strict  # reserved: unknown-key policy when model_config is tightened
    tasks: list[EvalTask] = [
        task_definition_to_eval_task(definition) for definition in doc.resolved_tasks()
    ]

    if category:
        tasks = [t for t in tasks if t.category == category]

    return tasks


# Helpers


def model_suffix(model: str) -> str:
    """Return a short filesystem/Docker-safe label for a model (or ``model:effort``)."""
    from ..utils import slug_text

    mid, effort = split_model_effort(model)
    if model in MODEL_SHORTS:
        base = MODEL_SHORTS[model]
    elif mid in MODEL_SHORTS:
        base = MODEL_SHORTS[mid]
    else:
        base = (mid or model)[:10]
    if effort:
        return slug_text(f"{base}-{effort}", max_len=14, fallback="model")
    return slug_text(base, max_len=10, fallback="model")


def eval_container_model_tag(model: str) -> str:
    """Short unique-ish tag for ``groket-{run_id}-{tag}`` container names.

    Uses the model id tail (e.g. ``tomato`` from ``v9-tomato``) plus effort when
    set, so two ``*:xhigh`` launch tokens do not both collapse to ``xhigh`` and
    require an opaque ``x2`` disambiguator that drops effort on read-back.
    """
    from ..utils import slug_text

    mid, effort = split_model_effort(model)
    raw = (mid or model or "").strip()
    parts = [
        p
        for p in raw.replace("_", "-").replace("/", "-").replace(":", "-").split("-")
        if p and p.lower() != "v9"
    ]
    tail = (parts[-1] if parts else "model")[:10]
    if len(parts) >= 2 and parts[-1].isdigit():
        tail = parts[-2][:10]
    if effort:
        return slug_text(f"{tail}-{effort}", max_len=16, fallback="model")
    return slug_text(tail, max_len=12, fallback="model")


# Batch runner


def models_for_task(task: EvalTask, batch_models: list[str]) -> list[str]:
    """Resolve models for one task: task YAML list wins when non-empty.

    :param task: Task that may set ``models:``.
    :param batch_models: Already-resolved CLI / catalog default list.
    :returns: Resolved model ids for this task only.
    """
    own = [str(m).strip() for m in (task.models or []) if str(m).strip()]
    if own:
        return resolve_model_ids(own)
    return list(batch_models)


def _run_single_task(
    task: EvalTask,
    models: list[str],
    work_dir: Path,
    task_num: int,
    total_tasks: int,
) -> list[dict]:
    """Run a single task across all models. Thread-safe."""

    ContainerConfig, DockerOrchestrator = _docker_types()
    # Orchestrator roots at ``<work>/runs`` so traces land in ``runs/traces/``.
    orch = DockerOrchestrator(Path(work_dir) / "runs")
    results: list[dict] = []
    models = models_for_task(task, models)
    if not models:
        raise RuntimeError(f"task {task.task_id!r}: no models after resolve")

    tag = f"[{task_num}/{total_tasks} {task.task_id}]"
    logger.info(f"\n{tag} START — {task.description}")
    logger.info(f"{tag} Models: {models}")
    if task.has_local_path:
        logger.info(f"{tag} Workspace: local path {task.repo_path}")
    elif task.has_repo:
        logger.info(f"{tag} Repo: {task.repo_url} ({task.repo_branch or 'default branch'})")
    else:
        logger.info(f"{tag} Repo: (none — no-repo job)")
    if task.setup_instructions.strip():
        n_lines = task.setup_instructions.strip().count("\n") + 1
        logger.info(f"{tag} Initial commands: {n_lines} line(s)")
    batch_start = time.time()

    configs = []
    follow_ups = [str(t).strip() for t in (task.turns or []) if str(t).strip()]
    task_env = dict(task.env or {})
    resume_src = (task.resume_session_dir or "").strip()
    resume_sid = (task.resume_session_id or "").strip()
    if resume_src:
        from ..session.resume import can_resume_session, resume_session_id

        resume_path = Path(resume_src).expanduser().resolve()
        if not resume_path.is_dir():
            raise FileNotFoundError(
                f"task {task.task_id!r}: resume_session_dir not found: {resume_path}"
            )
        if not can_resume_session(resume_path):
            raise ValueError(
                f"task {task.task_id!r}: resume_session_dir has no chat/events/summary: "
                f"{resume_path}"
            )
        if not resume_sid:
            resume_sid = resume_session_id(resume_path)
        resume_src = str(resume_path)
        logger.info(f"{tag} Fork-resume from {resume_src} (parent id={resume_sid})")
    effective_repo_path = (task.repo_path or "").strip()
    if effective_repo_path:
        from ..session.workspace import resolve_repo_path

        try:
            effective_repo_path = str(resolve_repo_path(effective_repo_path))
        except (OSError, ValueError, FileNotFoundError) as exc:
            raise RuntimeError(f"task {task.task_id!r}: repo_path invalid: {exc}") from exc
        if len(models) > 1:
            raise RuntimeError(
                f"task {task.task_id!r}: repo_path mounts a live host directory — "
                f"use a single model (got {len(models)})"
            )
    for model in models:
        suffix = model_suffix(model)
        name = f"groket-{task.task_id}-{suffix}"
        mid, effort = split_model_effort(model)
        cfg = ContainerConfig(
            model=mid or model,
            reasoning_effort=effort,
            prompt=task.prompt,
            container_name=name,
            docker_image=task.docker_image,
            repo_url=task.repo_url,
            repo_branch=task.repo_branch,
            repo_path=effective_repo_path,
            setup_instructions=task.setup_instructions,
            persona_id=(task.persona_id or "").strip(),
            env_vars=task_env,
            follow_up_prompts=follow_ups,
            resume_source_dir=resume_src,
            resume_session_id=resume_sid,
            max_turns=normalize_max_turns(task.max_turns, default=DEFAULT_MAX_TURNS),
            yolo=bool(task.yolo),
        )
        configs.append(cfg)

    def on_status(status):
        logger.info(f"{tag} [{model_suffix(status.model):>5}] {status.status}")

    def on_log(container_name, line):
        if line.startswith(">>>") or "error" in line.lower()[:50]:
            short = container_name.split("-")[-1]
            logger.info(f"{tag} [{short:>5}] {line[:120]}")

    try:
        statuses = orch.run_parallel_evaluations(
            configs,
            AUTH_JSON,
            GROK_CONFIG,
            on_status=on_status,
            on_log=on_log,
        )

        sessions_map = {s.container_name: str(s.session_dir) for s in statuses if s.session_dir}
        manifest = {
            "run_id": f"batch-{task.task_id}",
            "task_id": task.task_id,
            "created_at": datetime.now(UTC).isoformat(),
            "prompt": task.prompt,
            "repo_url": task.repo_url,
            "repo_branch": task.repo_branch,
            "repo_path": effective_repo_path or task.repo_path or "",
            "docker_image": task.docker_image,
            "setup_instructions": task.setup_instructions,
            "domain": task.domain,
            "horizon": task.horizon,
            "models": models,
            "sessions": sessions_map,
        }
        for s in statuses:
            if s.session_dir and s.session_dir.is_dir():
                try:
                    (s.session_dir / "run.json").write_text(json.dumps(manifest, indent=2))
                except Exception:
                    pass

        for status in statuses:
            entry = {
                "task_id": task.task_id,
                "model": status.model,
                "container": status.container_name,
                "status": status.status,
                "session_dir": str(status.session_dir) if status.session_dir else None,
                "error": (status.error or "")[:800],
            }
            results.append(entry)
            sfx = model_suffix(status.model)
            if status.status == "completed":
                logger.info(f"{tag} [{sfx:>5}] DONE -> {status.session_dir}")
            else:
                err = (status.error or "").replace("\n", " | ")
                logger.error(f"{tag} [{sfx:>5}] FAILED: {err[:400]}")

    except Exception as e:
        logger.error(f"{tag} ERROR: {e}")
        for model in models:
            results.append(
                {
                    "task_id": task.task_id,
                    "model": model,
                    "container": f"groket-{task.task_id}-{model_suffix(model)}",
                    "status": "failed",
                    "session_dir": None,
                    "error": str(e)[:200],
                }
            )

    elapsed = time.time() - batch_start
    ok = sum(1 for r in results if r["status"] == "completed")
    logger.info(f"{tag} FINISHED in {elapsed:.0f}s ({ok}/{len(results)} ok)")
    return results


def run_batch(
    tasks: list[EvalTask],
    work_dir: Path | None = None,
    models: list[str] | None = None,
    parallelism: int = 1,
) -> list[dict]:
    """Run evaluation tasks through Docker orchestrator.

    Args:
        tasks: List of tasks to run.
        work_dir: Working directory for outputs.
        models: Model IDs to evaluate (default: load_models()).
        parallelism: Number of tasks to run concurrently (default: 1).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    work_dir = work_dir or WORK_DIR
    models_in = list(models or load_models())
    # Same normalization as TUI RunManager: friendly names → CLI -m ids
    # (bottlerocket → v9-bottlerocket; pizzaparty → v9-pizzaparty).
    models = resolve_model_ids(models_in)
    if not models:
        logger.error("ERROR: no models after resolve_model_ids()")
        return []

    _, DockerOrchestrator = _docker_types()
    orch = DockerOrchestrator(Path(work_dir) / "runs")
    if not orch.check_docker_available():
        logger.error("ERROR: Docker is not available")
        return []

    total_tasks = len(tasks)
    per_task_counts = [len(models_for_task(t, models)) for t in tasks]
    total_containers = sum(per_task_counts)

    logger.info(f"\n{'=' * 70}")
    logger.info("  Batch Evaluation Runner")
    logger.info(
        f"  {total_tasks} task(s), {total_containers} container(s) "
        f"(batch default models × tasks that omit models:)"
    )
    logger.info(f"  Parallelism: {parallelism} concurrent tasks")
    if models_in != models:
        logger.info("  Batch default model ids (resolved for grok -m):")
        for a, b in zip(models_in, models):
            if a != b:
                logger.info(f"    {a!r}  →  {b!r}")
            else:
                logger.info(f"    {b!r}")
    else:
        logger.info(f"  Batch default model ids: {models}")
    for t in tasks:
        tm = models_for_task(t, models)
        if t.models:
            logger.info(f"  task {t.task_id}: models from YAML → {tm}")
    logger.info(f"{'=' * 70}")

    all_results: list[dict] = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {
            pool.submit(
                _run_single_task,
                task,
                models,
                work_dir,
                i + 1,
                total_tasks,
            ): task
            for i, task in enumerate(tasks)
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
            except Exception as e:
                logger.error(f"[{task.task_id}] UNHANDLED ERROR: {e}")

    # Save results log
    log_file = eval_results_path(work_dir)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as f:
        json.dump(all_results, f, indent=2)

    elapsed = time.time() - start
    ok = sum(1 for r in all_results if r["status"] == "completed")
    fail = len(all_results) - ok

    logger.info(f"\n{'=' * 70}")
    logger.info(f"  COMPLETE in {elapsed:.0f}s: {ok} succeeded, {fail} failed")
    logger.info(f"  Results: {log_file}")
    logger.info(f"{'=' * 70}")

    return all_results
