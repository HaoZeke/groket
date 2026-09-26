"""Docker orchestrator for spinning up parallel grok evaluation sessions.

Manages the lifecycle of Docker containers running grok with different models
and prompts, extracts traces when done.

Uses python-on-whales for typed Docker interaction
calls.  All methods are synchronous — the caller (screens/runner.py) already
runs Docker work in a dedicated worker thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

from python_on_whales import Container, DockerClient

from ..constants import DEFAULT_MAX_TURNS, normalize_max_turns
from ..utils import slug_text
from .base_profiles import (
    DEFAULT_DOCKER_IMAGE,
    build_run_dockerfile,
    build_shared_base_dockerfile,
    resolve_docker_base,
    shared_base_build_dirname,
    shared_base_image_tag,
)
from .resources import empty_setup_sh, entrypoint_sh, find_primary_session_py, share_once_py

# Serialize builds of the same shared base tag (parallel batch members share one base).
_SHARED_BASE_LOCKS: dict[str, threading.Lock] = {}
_SHARED_BASE_LOCKS_GUARD = threading.Lock()


def _lock_for_shared_tag(tag: str) -> threading.Lock:
    with _SHARED_BASE_LOCKS_GUARD:
        if tag not in _SHARED_BASE_LOCKS:
            _SHARED_BASE_LOCKS[tag] = threading.Lock()
        return _SHARED_BASE_LOCKS[tag]


@dataclass
class ContainerConfig:
    """Configuration for a single evaluation container."""

    model: str
    prompt: str
    container_name: str = ""
    # Base image or profile alias (``fully-loaded`` default, ``minimal``, ``debian@fully-loaded``).
    docker_image: str = DEFAULT_DOCKER_IMAGE
    repo_url: str = ""
    repo_branch: str = ""
    # Host directory bind-mounted as /workspace (no CoW/clone). Empty = use
    # repo_url clone or empty under runs/checkouts/<container>/.
    repo_path: str = ""
    setup_instructions: str = ""
    # Opt-in: inject host GH_TOKEN and wire git→gh credentials for push.
    github_write: bool = False
    # One-shot token from Runner UI (never persisted to run_configs). Overrides host env.
    github_token: str = ""
    # Persona id (operator reference); env/github flags should already be applied by caller.
    persona_id: str = ""
    # Persona MCP / skills / Grok plugins applied at start_container.
    mcp_servers: list[str] = field(default_factory=list)
    mcp_definitions: list = field(default_factory=list)
    mcp_replace_host: bool = True
    mcp_extra_toml: str = ""
    skills: list[str] = field(default_factory=list)
    skills_disabled: list[str] = field(default_factory=list)
    # Run-only SKILL.md packs: (skill_id, body text)
    inline_skills: list[tuple[str, str]] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    # Run-only capability extras (not persona); stored in run.json for fork prefill.
    run_plugins: list[str] = field(default_factory=list)
    run_skills: list[str] = field(default_factory=list)
    run_mcp_servers: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, str] = field(default_factory=dict)
    # Multi-turn: entrypoint waits for host follow-ups on the sessions volume.
    interactive: bool = False
    # Extra prompts after the primary ``prompt`` (batch scripted turns).
    follow_up_prompts: list[str] = field(default_factory=list)
    # Optional run id (logging / launch meta); turn gate is always ``.groket-turn``.
    run_id: str = ""
    # Grok reasoning effort (low|medium|high|xhigh|max); empty uses host/default.
    reasoning_effort: str = ""
    # Resume an ended session: host path to seed + parent Grok session id for turn 1.
    resume_source_dir: str = ""
    resume_session_id: str = ""
    # Optional UUID for ``grok --fork-session --session-id`` (host-generated if empty).
    resume_fork_session_id: str = ""
    # After clone, best-effort ``git checkout`` this SHA (from parent ``head_commit``).
    repo_commit: str = ""
    # Pass Grok ``--restore-code`` on resume/fork (checkout session commit when possible).
    restore_code: bool = False
    # Grok agent steps per prompt (``--max-turns``); default 50.
    max_turns: int = DEFAULT_MAX_TURNS
    # Opt-in: pass ``grok --yolo`` (aggressive auto-approve; default is --always-approve only).
    yolo: bool = False

    def __post_init__(self) -> None:
        self.max_turns = normalize_max_turns(self.max_turns)
        if not self.container_name:
            short_id = uuid.uuid4().hex[:8]
            mid = self.model.split(":")[0] if ":" in self.model else self.model
            safe_model = slug_text(mid, max_len=30, fallback="model")
            self.container_name = f"groket-{safe_model}-{short_id}"
        else:
            # Docker + Textual ids reject ``:`` and other punctuation from model:effort.
            self.container_name = slug_text(self.container_name, max_len=128, fallback="groket-run")

    def resolved_base(self):
        """Resolve :attr:`docker_image` to base image + profile (fully-loaded, …)."""
        return resolve_docker_base(self.docker_image)


@dataclass
class ContainerStatus:
    """Runtime status of a container."""

    container_name: str
    model: str
    status: str = "pending"  # pending, building, running, extracting, completed, failed
    container_id: str = ""
    error: str = ""
    session_dir: Path | None = None
    # Grok share URL from groket-share.json (in-container ``grok share`` only).
    share_url: str = ""
    started_at: str = ""
    finished_at: str = ""


def _force_ui_yolo(text: str, *, yolo: bool) -> str:
    """Ensure ``[ui] yolo = true|false`` matches the launch option."""
    body = text or ""
    val = "true" if yolo else "false"
    if re.search(r"(?im)^\s*yolo\s*=", body):
        return re.sub(r"(?im)^(\s*yolo\s*=\s*)\S+", rf"\g<1>{val}", body, count=1)
    if re.search(r"(?im)^\s*\[ui\]\s*$", body):
        return re.sub(
            r"(?im)^(\s*\[ui\]\s*)$",
            rf"\1\nyolo = {val}",
            body,
            count=1,
        )
    return body.rstrip() + f"\n\n[ui]\nyolo = {val}\n"


def _eval_config_toml(
    host_config: Path,
    *,
    primary_model: str,
    yolo: bool = False,
) -> str:
    """Build config.toml for an eval container.

    Pins ``models.default`` and ``fork_secondary_model`` to the launch model so
    host prefs do not call ``grok-build``. Reasoning effort is not written here;
    the entrypoint passes ``--effort`` from ``REASONING_EFFORT`` / the launch
    token. Host ``default_reasoning_effort`` lines are omitted so they cannot
    override the CLI flag. ``yolo`` is forced from the launch option.
    """
    from ..runs.batch import split_model_effort

    mid, _ = split_model_effort(primary_model)
    model = (mid or primary_model or "v9").strip() or "v9"
    secondary = "v9" if model == "grok-build" else model

    base = ""
    if host_config and Path(host_config).exists():
        try:
            base = Path(host_config).read_text(encoding="utf-8", errors="replace")
        except OSError:
            base = ""

    if not base.strip():
        return (
            "[cli]\n"
            'installer = "internal"\n'
            "auto_update = false\n"
            "\n"
            "[ui]\n"
            f'fork_secondary_model = "{secondary}"\n'
            f"yolo = {'true' if yolo else 'false'}\n"
            'permission_mode = "always-approve"\n'
            "\n"
            "[models]\n"
            f'default = "{model}"\n'
            "\n"
            "[dashboard]\n"
            "enabled = true\n"
        )

    lines = base.splitlines()
    out: list[str] = []
    section = ""
    saw_fork = False
    saw_default = False
    saw_auto_update = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            out.append(line)
            continue
        if section == "cli" and stripped.startswith("auto_update"):
            out.append("auto_update = false")
            saw_auto_update = True
            continue
        if section == "ui" and stripped.startswith("fork_secondary_model"):
            out.append(f'fork_secondary_model = "{secondary}"')
            saw_fork = True
            continue
        if section == "models" and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key == "default":
                out.append(f'default = "{model}"')
                saw_default = True
                continue
            if key == "default_reasoning_effort":
                continue
        out.append(line)

    text = "\n".join(out)
    if saw_auto_update or re.search(r"(?im)^\s*auto_update\s*=", text):
        text = re.sub(
            r"(?im)^(\s*auto_update\s*=\s*)\S+",
            r"\1false",
            text,
            count=1,
        )
    elif "[cli]" in text:
        text = text.replace("[cli]", "[cli]\nauto_update = false", 1)
    else:
        text = "[cli]\nauto_update = false\n\n" + text
    if not saw_fork:
        if "[ui]" in text:
            text = text.replace(
                "[ui]",
                f'[ui]\nfork_secondary_model = "{secondary}"',
                1,
            )
        else:
            text += f'\n\n[ui]\nfork_secondary_model = "{secondary}"\n'
    if not saw_default:
        if "[models]" in text:
            text = text.replace(
                "[models]",
                f'[models]\ndefault = "{model}"',
                1,
            )
        else:
            text += f'\n\n[models]\ndefault = "{model}"\n'
    if not text.endswith("\n"):
        text += "\n"
    # Always enable agent dashboard in eval containers so live sessions can be
    # monitored (host dashboard / share flow + traces on the bind mount).
    if "[dashboard]" not in text:
        text += "\n[dashboard]\nenabled = true\n"
    elif re.search(r"(?im)^\s*enabled\s*=\s*false\s*$", text):
        # Host may have disabled it; force on for eval monitoring.
        text = re.sub(
            r"(?im)^(\s*enabled\s*=\s*)false(\s*)$",
            r"\1true\2",
            text,
            count=1,
        )
    return _force_ui_yolo(text, yolo=yolo)


def _build_setup_script(setup_instructions: str) -> str:
    """Turn multiline (or single-line) initial commands into an executable bash script."""
    body = (setup_instructions or "").strip()
    if not body:
        return empty_setup_sh()
    # Preserve user newlines; ensure shebang + errexit for real setups.
    # If the user already provided a shebang, keep their script as-is.
    if body.startswith("#!"):
        return body if body.endswith("\n") else body + "\n"
    return f"#!/bin/bash\nset -euo pipefail\n\n{body}\n"


def _token_from_host_gh_cli() -> str:
    """If the operator already ran ``gh auth login`` on the host, reuse that token.

    Avoids requiring a separate ``export GH_TOKEN=...`` when ``gh`` is installed and
    logged in locally. Non-interactive; fails closed (empty string) on any error.
    """
    try:
        import shutil
        import subprocess

        if not shutil.which("gh"):
            return ""
        r = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "GH_PAGER": "cat"},
        )
        if r.returncode != 0:
            return ""
        tok = (r.stdout or "").strip()
        # Basic sanity: tokens are non-trivial; avoid injecting pager noise
        if len(tok) < 8 or "\n" in tok:
            return ""
        return tok
    except Exception:
        return ""


def _host_gh_env_for_container(
    *,
    github_write: bool = False,
    github_token: str = "",
) -> dict[str, str]:
    """Build GitHub-related env for an eval container.

    **Default (``github_write=False``):** do **not** inject any token. Most evals
    should not have push access; agents pivot to local git / fixtures.

    **Write mode (``github_write=True``):** inject a token so ``gh`` and ``git``
    (via entrypoint ``gh auth setup-git``) can push to the runner's ``repo_url``.
    Resolution order (first non-empty wins for ``GH_TOKEN``):
      0. ``github_token`` arg — persona PAT / persona ``github_token_env`` resolved at launch
      1. ``GH_TOKEN`` / ``GITHUB_TOKEN`` on the host (explicit / CI)
      2. ``gh auth token`` on the host (broader; last resort)

    No token is injected unless write is on or a persona token was resolved.
    """
    ui_tok = (github_token or "").strip()
    if not github_write and not ui_tok:
        return {}

    out: dict[str, str] = {
        "GH_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "GITHUB_WRITE": "1",
    }

    gh_token = (os.environ.get("GH_TOKEN") or "").strip()
    ghub_token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    chosen = ui_tok or gh_token or ghub_token
    if not chosen:
        chosen = _token_from_host_gh_cli()
    if chosen:
        out["GH_TOKEN"] = chosen
        out["GITHUB_TOKEN"] = chosen

    host = (os.environ.get("GH_HOST") or "").strip()
    if host:
        out["GH_HOST"] = host

    if "GH_TOKEN" not in out and "GITHUB_TOKEN" not in out:
        return {}
    return out


def describe_github_write_token_status(*, ui_token: str = "") -> str:
    """Short human status for Runner UI (never includes the token itself).

    *ui_token* is the persona-resolved token for this launch (not a Runner field).
    """
    if (ui_token or "").strip():
        return "persona token resolved for this launch"
    if (os.environ.get("GH_TOKEN") or "").strip() or (os.environ.get("GITHUB_TOKEN") or "").strip():
        return "GH_TOKEN/GITHUB_TOKEN set on host (fallback)"
    if _token_from_host_gh_cli():
        return "host `gh auth` token available (broader than ideal)"
    return "no token — set PAT on the persona, or GH_TOKEN/GITHUB_TOKEN on the host"


class DockerOrchestrator:
    """Manages Docker containers for parallel trace evaluation.

    All public methods are synchronous.  The caller (screens/runner.py)
    runs them in a worker thread with ``@work(thread=True)``.
    """

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.containers: dict[str, ContainerStatus] = {}
        self._build_dir = work_dir / "docker-build"
        self._docker = DockerClient()
        # Set on TUI quit so wait loops and parallel workers return promptly.
        self._abort = threading.Event()

    def request_abort(self) -> None:
        """Signal in-flight evaluations to stop waiting (containers keep running)."""
        self._abort.set()

    def clear_abort(self) -> None:
        """Allow a new launch after a previous abort (same process)."""
        self._abort.clear()

    @property
    def abort_requested(self) -> bool:
        return self._abort.is_set()

    def check_docker_available(self) -> bool:
        """Check if Docker is available and the daemon is running."""
        try:
            self._docker.info()
            return True
        except Exception:
            return False

    def _persona_for_config(self, config: ContainerConfig):
        """Load persona by config.persona_id from work_dir parent (runs/ → work_dir)."""
        pid = (config.persona_id or "").strip()
        if not pid:
            return None
        try:
            from ..runs.personas import PersonaStore

            # Orchestrator is usually rooted at ``<work>/runs``; personas live under APP_HOME.
            work = self.work_dir.parent if self.work_dir.name == "runs" else self.work_dir
            return PersonaStore(work).get(pid)
        except Exception:
            logger.warning("Failed to load persona %r for container config", pid, exc_info=True)
            return None

    def _apply_persona_capabilities_config(
        self,
        cfg_text: str,
        config: ContainerConfig,
        *,
        grok_config: Path,
        traces_vol: Path,
    ) -> str:
        """Apply persona MCP, standalone skills, and Grok plugins separately."""
        persona = self._persona_for_config(config)
        pid = (config.persona_id or "").strip()
        # Prefer fields copied onto ContainerConfig at launch (works even if persona file changes).
        if persona is None and (
            config.mcp_servers or config.skills or config.mcp_extra_toml or config.plugins
        ):
            from ..runs.personas import Persona

            persona = Persona(
                persona_id=pid or "inline",
                mcp_servers=list(config.mcp_servers or []),
                mcp_definitions=list(config.mcp_definitions or []),
                mcp_replace_host=bool(config.mcp_replace_host),
                mcp_extra_toml=str(config.mcp_extra_toml or ""),
                skills=list(config.skills or []),
                skills_disabled=list(config.skills_disabled or []),
                plugins=list(config.plugins or []),
            )
        elif persona is None and pid:
            # Fail loud: task/runner asked for a persona that is not installed under
            # ~/.groket/personas — silent skip left agents claiming "no MCP".
            raise FileNotFoundError(
                f"persona {pid!r} not found under ~/.groket/personas "
                f"(install with: cp examples/personas/{pid}.json ~/.groket/personas/ "
                "or create it in the Personas screen)"
            )
        elif persona is not None:
            # Overlay launch-time lists from config when set (run_manager copies persona onto config).
            if config.mcp_servers:
                persona.mcp_servers = list(config.mcp_servers)
            if config.mcp_definitions:
                persona.mcp_definitions = list(config.mcp_definitions)
            if config.skills:
                persona.skills = list(config.skills)
            if config.skills_disabled:
                persona.skills_disabled = list(config.skills_disabled)
            if config.plugins:
                persona.plugins = list(config.plugins)
            if config.mcp_extra_toml:
                persona.mcp_extra_toml = str(config.mcp_extra_toml)
            persona.mcp_replace_host = bool(config.mcp_replace_host)

        work = self.work_dir.parent if self.work_dir.name == "runs" else self.work_dir
        host_text = ""
        try:
            if grok_config and Path(grok_config).is_file():
                host_text = Path(grok_config).read_text(encoding="utf-8", errors="replace")
        except OSError:
            host_text = ""

        try:
            from ..capabilities import (
                apply_persona_mcp_to_config_toml,
                apply_persona_plugins_to_config_toml,
                apply_persona_skills_to_config_toml,
                prepare_persona_plugins_dir,
                prepare_persona_skills_dir,
            )

            cfg_text = apply_persona_mcp_to_config_toml(
                cfg_text,
                persona,
                work_dir=work,
                host_config_text=host_text,
            )
            # Stage beside the sessions volume (not inside it) so:
            # - session discovery does not walk huge plugin trees (superpowers, …)
            # - bind-mount of sessions stays session-only on the host path
            stage_root = traces_vol.parent / f"{traces_vol.name}.stage"
            skills_stage = stage_root / "skills"
            prepare_persona_skills_dir(
                skills_stage,
                persona,
                work_dir=work,
                inline_skills=list(config.inline_skills or []),
            )
            cfg_text = apply_persona_skills_to_config_toml(cfg_text, persona)
            # Host materializes checkouts + plugin skills once; container only installs.
            # Fail the launch if a selected plugin cannot be staged (do not enable
            # plugins that will not install).
            prepare_persona_plugins_dir(
                stage_root / "plugins",
                persona,
                work_dir=work,
                skills_dest=skills_stage,
            )
            cfg_text = apply_persona_plugins_to_config_toml(cfg_text, persona)
        except FileNotFoundError:
            # Persona id set but not installed — fail the container start.
            raise
        except Exception as exc:
            from ..capabilities.apply import PluginStageError

            if isinstance(exc, PluginStageError):
                raise
            logger.warning(
                "Failed to apply persona capabilities for %s",
                config.persona_id,
                exc_info=True,
            )
        return cfg_text

    def _docker_build_quiet(self, context: Path, *, tags: list[str]) -> None:
        """Build an image without streaming BuildKit progress to the TTY.

        Default ``progress='auto'`` prints hundreds of layer lines onto the same
        terminal as Textual and floods the screen. ``progress=False`` captures
        stderr internally (python-on-whales); we only surface a short tail on failure.
        """
        self._docker.build(
            context,
            tags=tags,
            progress=False,
        )

    def _image_exists(self, tag: str) -> bool:
        try:
            return bool(self._docker.image.exists(tag))
        except Exception:
            try:
                # Older python-on-whales / docker edge cases
                self._docker.image.inspect(tag)
                return True
            except Exception:
                return False

    def ensure_shared_base(
        self,
        *,
        base_image: str,
        fully_loaded: bool,
        profile_id: str = "",
        on_log: Callable | None = None,
    ) -> str:
        """Build or reuse the heavy ``groket-base:…`` image (packages + grok CLI).

        Fixed build context under ``docker-build/_shared/<tag-slug>/`` so Docker
        layer cache and the named tag survive across eval runs. Per-run images
        only ``FROM`` this tag and add setup/entrypoint.
        """
        tag = shared_base_image_tag(
            base_image=base_image,
            fully_loaded=fully_loaded,
            profile_id=profile_id,
        )
        lock = _lock_for_shared_tag(tag)
        with lock:
            if self._image_exists(tag):
                if on_log:
                    on_log("_shared", f">>> Reusing cached base image {tag}")
                return tag

            slug = shared_base_build_dirname(
                base_image=base_image,
                fully_loaded=fully_loaded,
                profile_id=profile_id,
            )
            build_dir = self._build_dir / "_shared" / slug
            build_dir.mkdir(parents=True, exist_ok=True)
            df = build_shared_base_dockerfile(base_image=base_image, fully_loaded=fully_loaded)
            (build_dir / "Dockerfile").write_text(df, encoding="utf-8")
            (build_dir / "groket-base-profile.txt").write_text(
                f"tag={tag}\nbase_image={base_image}\nfully_loaded={fully_loaded}\n"
                f"profile_id={profile_id}\n",
                encoding="utf-8",
            )

            if on_log:
                kind = "fully-loaded" if fully_loaded else "minimal"
                on_log(
                    "_shared",
                    f">>> Building shared {kind} base once: {tag} "
                    f"(subsequent runs reuse this; edit base_profiles.py → new hash → rebuild once)",
                )

            try:
                # Quiet: do not stream BuildKit progress over the TUI terminal.
                self._docker_build_quiet(build_dir, tags=[tag])
            except Exception as exc:
                err = str(exc)
                err_lines = [ln for ln in err.splitlines() if ln.strip()]
                tail = "\n".join(err_lines[-20:]) if err_lines else err
                if on_log:
                    on_log("_shared", f">>> Shared base build FAILED ({tag})")
                    for ln in err_lines[-8:]:
                        on_log("_shared", f"    {ln[:200]}")
                raise RuntimeError(f"Shared base image build failed ({tag}):\n{tail}") from exc

            if on_log:
                on_log("_shared", f">>> Shared base ready: {tag}")
            return tag

    def _prepare_run_build_context(self, config: ContainerConfig, *, shared_tag: str) -> Path:
        """Thin context: only Dockerfile (FROM shared) + setup.sh + entrypoint."""
        build_dir = self._build_dir / config.container_name
        build_dir.mkdir(parents=True, exist_ok=True)

        resolved = config.resolved_base()
        dockerfile = build_run_dockerfile(shared_base_tag=shared_tag)
        (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (build_dir / "groket-base-profile.txt").write_text(
            f"stored={resolved.stored}\n"
            f"base_image={resolved.base_image}\n"
            f"profile_id={resolved.profile_id}\n"
            f"fully_loaded={resolved.fully_loaded}\n"
            f"shared_base={shared_tag}\n",
            encoding="utf-8",
        )
        (build_dir / "entrypoint.sh").write_text(entrypoint_sh(), encoding="utf-8")
        (build_dir / "groket-share-once.py").write_text(share_once_py(), encoding="utf-8")
        (build_dir / "groket_find_primary_session.py").write_text(
            find_primary_session_py(), encoding="utf-8"
        )
        (build_dir / "setup.sh").write_text(
            _build_setup_script(config.setup_instructions), encoding="utf-8"
        )
        return build_dir

    def build_image(
        self,
        config: ContainerConfig,
        *,
        on_log: Callable | None = None,
    ) -> str:
        """Build eval image: ensure shared base (cached), then thin per-run layer."""
        image_tag = f"groket-eval:{config.container_name}"
        resolved = config.resolved_base()

        shared_tag = self.ensure_shared_base(
            base_image=resolved.base_image,
            fully_loaded=resolved.fully_loaded,
            profile_id=resolved.profile_id,
            on_log=on_log,
        )

        build_dir = self._prepare_run_build_context(config, shared_tag=shared_tag)

        if on_log:
            on_log(
                config.container_name,
                f">>> Building thin eval image {image_tag} (FROM {shared_tag})",
            )

        try:
            self._docker_build_quiet(build_dir, tags=[image_tag])
        except Exception as exc:
            err = str(exc)
            err_lines = [ln for ln in err.splitlines() if ln.strip()]
            tail = "\n".join(err_lines[-15:]) if err_lines else err
            if on_log:
                on_log(config.container_name, f">>> Eval image build FAILED ({image_tag})")
                for ln in err_lines[-8:]:
                    on_log(config.container_name, f"    {ln[:200]}")
            raise RuntimeError(
                f"Docker build failed (shared={shared_tag}, profile={resolved.profile_id}):\n{tail}"
            ) from exc

        if on_log:
            on_log(config.container_name, f">>> Eval image ready: {image_tag}")
        return image_tag

    def start_container(
        self,
        config: ContainerConfig,
        image_tag: str,
        auth_json: Path,
        grok_config: Path,
    ) -> str:
        """Start a container and return its short ID."""
        traces_vol = self.work_dir / "traces" / config.container_name
        traces_vol.mkdir(parents=True, exist_ok=True)

        resume_sid = (config.resume_session_id or "").strip()
        resume_src = (config.resume_source_dir or "").strip()
        fork_sid = (config.resume_fork_session_id or "").strip()
        if resume_src:
            from ..session.resume import seed_resume_into_traces_vol

            try:
                seeded_id = seed_resume_into_traces_vol(traces_vol, Path(resume_src))
                resume_sid = resume_sid or seeded_id
            except (OSError, ValueError, FileNotFoundError) as exc:
                logger.warning("Failed to seed resume session from %s: %s", resume_src, exc)
                raise
        if resume_sid and not fork_sid:
            fork_sid = str(uuid.uuid4())
            config.resume_session_id = resume_sid
            config.resume_fork_session_id = fork_sid

        # Host-owned /workspace: external path mount, CoW parent on fork, or clone/empty.
        from ..session.workspace import (
            parent_checkout_for_session,
            prepare_host_checkout,
            resolve_repo_path,
        )

        external_ws = False
        repo_path_raw = (config.repo_path or "").strip()
        if repo_path_raw:
            # Live operator directory: bind-mount as-is (no CoW into checkouts/).
            workspace_host = resolve_repo_path(repo_path_raw)
            external_ws = True
            logger.info(
                "Mounting external workspace %s → /workspace (container %s)",
                workspace_host,
                config.container_name,
            )
        else:
            parent_ws: Path | None = None
            if resume_src:
                parent_ws = parent_checkout_for_session(self.work_dir, Path(resume_src))
            workspace_host = prepare_host_checkout(
                self.work_dir,
                config.container_name,
                repo_url=config.repo_url or "",
                repo_branch=config.repo_branch or "",
                repo_commit=config.repo_commit or "",
                parent_checkout=parent_ws,
            )

        from ..runs.launch_meta import write_launch_meta_for_config
        from ..runs.run_recipe import write_run_recipe_for_config

        try:
            write_launch_meta_for_config(traces_vol, config)
        except OSError:
            logger.warning("Failed to write launch meta under %s", traces_vol, exc_info=True)
        # Recipe (persona / plugins / skills / MCP) at start so fork/re-run work
        # before the container finishes (interactive multi-turn often lives for hours).
        write_run_recipe_for_config(traces_vol, config)

        # Write prompt to a host file and mount it — avoids Docker env newline/quoting
        # issues and `-p` argparse eating values that start with `-`.
        prompt_host = traces_vol / "groket-prompt.txt"
        prompt_host.write_text(config.prompt or "", encoding="utf-8")

        # Per-run config.toml: host config often has fork_secondary_model = "grok-build",
        # which triggers a second API call that fails without a Grok subscription even
        # when the primary -m is v9-pizzaparty / v9-bottlerocket. Pin secondary to the
        # eval model (or v9) so the container stays on experimental variants.
        # Persona MCP/skills applied separately (tools vs prompt packages).
        eval_config = traces_vol / "groket-config.toml"
        cfg_text = _eval_config_toml(
            grok_config,
            primary_model=config.model,
            yolo=bool(config.yolo),
        )
        cfg_text = self._apply_persona_capabilities_config(
            cfg_text,
            config,
            grok_config=grok_config,
            traces_vol=traces_vol,
        )
        # Re-apply after persona merge so host/persona cannot leave yolo wrong.
        cfg_text = _force_ui_yolo(cfg_text, yolo=bool(config.yolo))
        eval_config.write_text(cfg_text, encoding="utf-8")

        from ..runs.batch import REASONING_EFFORTS, cli_reasoning_effort, split_model_effort

        model_id, effort_tok = split_model_effort(config.model)
        product_effort = (config.reasoning_effort or effort_tok or "").strip().lower()
        if product_effort not in REASONING_EFFORTS:
            product_effort = ""
        # CLI --effort only accepts low|medium|high (xhigh/max → high).
        effort = cli_reasoning_effort(product_effort)
        envs = {
            "MODEL": model_id or config.model,
            "REPO_URL": config.repo_url or "",
            "REPO_BRANCH": config.repo_branch or "",
            # Keep PROMPT as fallback only; entrypoint prefers /groket-prompt.txt
            "PROMPT": config.prompt or "",
            # Opt-in write: persona token / GH_TOKEN + entrypoint wires git→gh.
            **_host_gh_env_for_container(
                github_write=bool(config.github_write),
                github_token=str(config.github_token or ""),
            ),
            **config.env_vars,
        }
        # Skip in-container ``grok share`` loop when host account cannot share
        # (avoids multi-hour retry spam). Override with env_vars:
        #   SHARE_DISABLE=1 force off · SHARE_DISABLE=0 force on (skip probe).
        _share_flag = str(envs.get("SHARE_DISABLE", "")).strip().lower()
        if _share_flag not in ("0", "false", "no", "1", "true", "yes"):
            try:
                from ..runs.live_share import probe_host_share_capability

                cap = probe_host_share_capability()
                if not cap.available:
                    envs["SHARE_DISABLE"] = "1"
                    logger.info(
                        "SHARE_DISABLE=1 (host share unavailable: %s)",
                        cap.reason,
                    )
            except Exception:
                logger.debug("Share capability probe failed", exc_info=True)
        if external_ws:
            # Entrypoint reclaims only root-owned paths under /workspace (not
            # a full recursive chown of the operator's tree).
            envs["WORKSPACE_EXTERNAL"] = "1"
        # So entrypoint can chown bind-mounted sessions (prompt_history.jsonl, …)
        # back to the host user (containers write as root otherwise).
        try:
            envs.setdefault("HOST_UID", str(os.getuid()))
            envs.setdefault("HOST_GID", str(os.getgid()))
        except AttributeError:
            pass
        if effort:
            envs["REASONING_EFFORT"] = effort
        commit = (config.repo_commit or "").strip()
        if commit:
            envs["REPO_COMMIT"] = commit
        if config.restore_code or bool(resume_sid):
            # Fork/resume: ask Grok to restore the session commit when the CLI can.
            envs["RESTORE_CODE"] = "1"
        if config.interactive:
            envs["INTERACTIVE"] = "1"
        if config.yolo:
            envs["YOLO"] = "1"
        envs["MAX_TURNS"] = str(normalize_max_turns(config.max_turns))
        if resume_sid:
            # Seeded parent history + first-turn fork so each resume branch gets a new Grok id.
            envs["RESUME_SESSION_ID"] = resume_sid
            envs["RESUME_FORK"] = "1"
            if fork_sid:
                envs["FORK_SESSION_ID"] = fork_sid
            # Parent chat embeds absolute installed-plugins/<id>/ paths. Collect
            # those basenames so the entrypoint can symlink them after install.
            if resume_src:
                from ..session.resume import collect_installed_plugin_dir_aliases

                aliases = collect_installed_plugin_dir_aliases(Path(resume_src))
                if aliases:
                    # Parent chat hardcodes installed-plugins/<id>/…; entrypoint
                    # recreates those basenames after install (one fork path).
                    envs["RESUME_PLUGIN_DIR_ALIASES"] = ",".join(aliases)
        # One turn gate per container traces volume (entrypoint TURN_DIR).
        envs["TURN_DIR"] = "/root/.grok/sessions/.groket-turn"
        scripted = [str(p) for p in (config.follow_up_prompts or []) if str(p).strip()]
        turn_dir = traces_vol / ".groket-turn"
        try:
            turn_dir.mkdir(parents=True, exist_ok=True)
            (turn_dir / "scripted-turns.json").write_text(
                json.dumps(scripted) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.debug("Could not write scripted turns under %s", turn_dir, exc_info=True)

        volumes: list[tuple[str, str] | tuple[str, str, str]] = [
            (str(auth_json), "/root/.grok/auth.json", "ro"),
            (str(eval_config), "/root/.grok/config.toml", "ro"),
            (str(traces_vol), "/root/.grok/sessions"),
            (str(prompt_host), "/groket-prompt.txt", "ro"),
            # Host checkout (CoW on fork); entrypoint skips clone when non-empty.
            (str(workspace_host), "/workspace"),
        ]

        # Skills / plugins: only ``*.stage/{skills,plugins}`` (one staging root).
        stage_root = traces_vol.parent / f"{traces_vol.name}.stage"
        skills_host = stage_root / "skills"
        if skills_host.is_dir() and any(skills_host.iterdir()):
            volumes.append((str(skills_host), "/groket-skills-stage", "ro"))
        plugins_stage = stage_root / "plugins"
        if (plugins_stage / "plugins-manifest.json").is_file():
            volumes.append((str(plugins_stage), "/groket-plugins", "ro"))

        # Share additional config files the CLI needs for model resolution
        grok_home = auth_json.parent
        for extra in ("models_cache.json", "managed_config_cache.json"):
            extra_path = grok_home / extra
            if extra_path.exists():
                volumes.append((str(extra_path), f"/root/.grok/{extra}", "ro"))
        for host_path, container_path in config.volumes.items():
            volumes.append((str(host_path), str(container_path)))

        detach: Literal[True] = True
        container: Container = self._docker.run(
            image_tag,
            name=config.container_name,
            envs=envs,
            volumes=volumes,
            detach=detach,
        )
        return str(container.id)[:12]

    def wait_for_container(self, container_name: str) -> int:
        """Wait for a container to finish and return its exit code."""
        try:
            return self._docker.wait(container_name)
        except Exception:
            return -1

    def get_container_logs(self, container_name: str, tail: int = 50) -> str:
        """Get the last ``tail`` lines of container logs."""
        try:
            logs = self._docker.logs(container_name, tail=tail, stream=False)
            if isinstance(logs, str):
                return logs
            return ""
        except Exception:
            return ""

    def stream_container_logs(
        self,
        container_name: str,
        on_log: Callable,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Stream live logs from a running container.

        Calls ``on_log(container_name, line)`` for every line.  Returns
        when the container stops or *stop_event* is set.
        """
        try:
            stream = self._docker.logs(container_name, follow=True, tail=100, stream=True)
            for line in stream:
                if stop_event and stop_event.is_set():
                    break
                if isinstance(line, str):
                    text = line
                elif isinstance(line, bytes):
                    text = line.decode(errors="replace")
                elif isinstance(line, tuple) and len(line) >= 2:
                    chunk = line[1]
                    text = (
                        chunk.decode(errors="replace") if isinstance(chunk, bytes) else str(chunk)
                    )
                else:
                    text = str(line)
                for sub in text.rstrip("\n").split("\n"):
                    on_log(container_name, sub)
        except Exception:
            pass  # container gone or stream error — both fine

    def fix_traces_ownership(self, traces_dir: Path | str) -> bool:
        """chown traces_dir (and children) to host uid:gid via python-on-whales.

        Eval containers run as root and bind-mount ``runs/traces/<name>`` at
        ``/root/.grok/sessions``, so session files are often root-owned on the
        host. Delegates to :func:`~groket.runs.run_configs.chown_path_to_host_user`
        (same whales client path as eval ``run`` / build).
        """
        traces_dir = Path(traces_dir)
        if not traces_dir.exists():
            return True
        from ..runs.run_configs import chown_path_to_host_user

        try:
            return chown_path_to_host_user(traces_dir)
        except Exception:
            logger.debug("fix_traces_ownership failed for %s", traces_dir, exc_info=True)
            return False

    def peek_session_dir(self, container_name: str) -> Path | None:
        """Locate a live session under the bind-mounted traces dir (no chown).

        Eval containers write incrementally to ``runs/traces/<container>/``.
        Call while the container is running so the TUI/Jobs can open mid-run.
        Ownership is fixed by the entrypoint (``HOST_UID``) and
        :meth:`extract_traces` — never on the peek hot path (that runs often
        while waiting and would spawn alpine containers each time).
        """
        traces_dir = self.work_dir / "traces" / container_name
        if not traces_dir.exists():
            return None
        from ..parser import find_sessions

        sessions = find_sessions(traces_dir)
        if not sessions:
            return None
        # Prefer the session with the newest trace mtime (most recently active).
        from ..parser import session_trace_mtime

        return max(sessions, key=lambda p: session_trace_mtime(p))

    def extract_traces(self, container_name: str) -> Path | None:
        """Finalize traces after a container run: fix ownership, locate session dir."""
        traces_dir = self.work_dir / "traces" / container_name
        if not traces_dir.exists():
            return None

        # Always attempt chown — interrupted/killed runs skip normal success paths
        # but files are already on the bind mount.
        self.fix_traces_ownership(traces_dir)

        from ..parser import find_sessions, session_trace_mtime

        sessions = find_sessions(traces_dir)
        if not sessions:
            return None
        # Same rule as peek_session_dir: newest primary (subagents already excluded).
        return max(sessions, key=lambda p: session_trace_mtime(p))

    def wait_for_container_with_session_peek(
        self,
        container_name: str,
        status: ContainerStatus,
        on_status: Callable | None = None,
        *,
        peek_interval_s: float = 2.5,
    ) -> int:
        """Wait for container exit while periodically discovering live session_dir.

        Updates ``status.session_dir`` as soon as summary/updates appear on the
        bind mount so listeners (app sessions list, Jobs modal) can open the
        browser without waiting for the run to finish.

        Returns early with ``-1`` if :meth:`request_abort` was called (TUI quit)
        so non-daemon work cannot pin the process; the container keeps running.
        """
        if self._abort.is_set():
            return -1

        stop = threading.Event()
        exit_holder: list[int] = [-1]

        def _wait() -> None:
            try:
                exit_holder[0] = self.wait_for_container(container_name)
            except Exception:
                exit_holder[0] = -1
            finally:
                stop.set()

        waiter = threading.Thread(
            target=_wait,
            name=f"groket-wait-{container_name[:24]}",
            daemon=True,
        )
        waiter.start()

        interval = max(0.5, float(peek_interval_s))
        while not stop.wait(timeout=interval):
            if self._abort.is_set():
                # Leave docker.wait on the daemon waiter; do not block shutdown.
                return -1
            if status.session_dir is not None:
                continue
            try:
                sd = self.peek_session_dir(container_name)
            except Exception:
                sd = None
            if sd is None:
                continue
            status.session_dir = sd
            if on_status:
                try:
                    on_status(status)
                except Exception:
                    pass

        if self._abort.is_set():
            return -1

        waiter.join(timeout=0.2)
        # Final peek in case session appeared in the last interval window.
        if status.session_dir is None:
            try:
                sd = self.peek_session_dir(container_name)
                if sd is not None:
                    status.session_dir = sd
                    if on_status:
                        try:
                            on_status(status)
                        except Exception:
                            pass
            except Exception:
                pass
        return exit_holder[0]

    def cleanup_container(self, container_name: str) -> None:
        """Stop and remove a container (ignoring errors)."""
        try:
            self._docker.stop(container_name, time=3)
        except Exception:
            pass
        try:
            self._docker.remove(container_name, force=True)
        except Exception:
            pass

    def list_running_eval_container_names(
        self,
        *,
        name_prefixes: tuple[str, ...] | None = None,
    ) -> list[str]:
        """Names of still-running eval containers (``groket-*`` by default).

        Used after TUI restart so activity counters reflect Docker state when
        no in-process :class:`~groket.runs.run_manager.BackgroundRun` remains.
        """
        from ..paths import RUN_PREFIX

        prefixes = name_prefixes if name_prefixes is not None else (RUN_PREFIX,)
        names: list[str] = []
        try:
            containers = self._docker.container.list(all=False)
        except Exception:
            return names
        for c in containers:
            try:
                raw = getattr(c, "name", None) or ""
                if isinstance(raw, (list, tuple)):
                    cname = (raw[0] if raw else "") or ""
                else:
                    cname = str(raw)
                cname = cname.lstrip("/")
                if not cname or not any(cname.startswith(pfx) for pfx in prefixes):
                    continue
                state = ""
                try:
                    state = (c.state.status or "").lower()
                except Exception:
                    state = str(getattr(c, "status", "") or "").lower()
                if state in ("running", "restarting", "paused") or not state:
                    # list(all=False) is running-only; include if status unreadable.
                    names.append(cname)
            except Exception:
                continue
        return names

    def count_running_eval_containers(
        self,
        *,
        name_prefixes: tuple[str, ...] | None = None,
    ) -> int:
        """Count running eval *containers* (each usually maps to one session)."""
        return len(self.list_running_eval_container_names(name_prefixes=name_prefixes))

    def count_running_eval_runs(
        self,
        *,
        name_prefixes: tuple[str, ...] | None = None,
    ) -> int:
        """Count distinct eval *runs* still active in Docker.

        Container names are ``groket-<run_id>-…``; unique ``run_id`` segments
        map to one launch (one launch may use several containers/sessions).
        """
        names = self.list_running_eval_container_names(name_prefixes=name_prefixes)
        if not names:
            return 0
        run_ids: set[str] = set()
        for cname in names:
            parts = cname.split("-")
            # groket-<12hex run_id>-<model short>…
            if len(parts) >= 3 and parts[0] == "groket" and len(parts[1]) >= 8:
                run_ids.add(parts[1])
            else:
                run_ids.add(cname)
        return len(run_ids)

    def prune_eval_containers(
        self,
        *,
        remove_exited: bool = True,
        remove_running: bool = False,
        protect_names: set[str] | None = None,
        name_prefixes: tuple[str, ...] | None = None,
        name_prefix: str | None = None,
    ) -> dict[str, int]:
        """Best-effort cleanup of evaluation containers from prior launches.

        Intended for relaunch / app restart: remove finished eval containers
        that docker still has around, without touching live tracked runs.

        Args:
            remove_exited: remove stopped/exited containers matching prefix
            remove_running: also stop+remove still-running containers (dangerous;
                only use for explicit user cleanup, not normal relaunch)
            protect_names: container names to never remove (active runs)
            name_prefixes: only touch containers whose name starts with one of these
                (default: groket-). ``name_prefix`` is accepted as a single-prefix alias.

        Returns counts: ``{"exited_removed": n, "running_removed": n}``
        """
        protect = protect_names or set()
        stats = {"exited_removed": 0, "running_removed": 0}
        prefixes: tuple[str, ...]
        if name_prefixes is None:
            if name_prefix is not None:
                prefixes = (name_prefix,)
            else:
                from ..paths import RUN_PREFIX

                prefixes = (RUN_PREFIX,)
        else:
            prefixes = tuple(name_prefixes)
        try:
            containers = self._docker.container.list(all=True)
        except Exception:
            return stats

        for c in containers:
            try:
                names = getattr(c, "name", None) or ""
                # python_on_whales may expose .name or names list
                if isinstance(names, (list, tuple)):
                    cname = (names[0] if names else "") or ""
                else:
                    cname = str(names)
                cname = cname.lstrip("/")
                if not any(cname.startswith(pfx) for pfx in prefixes):
                    continue
                if cname in protect:
                    continue
                state = ""
                try:
                    state = (c.state.status or "").lower()
                except Exception:
                    try:
                        state = str(getattr(c, "status", "") or "").lower()
                    except Exception:
                        state = ""

                is_running = state in ("running", "restarting", "paused")
                if is_running:
                    if not remove_running:
                        continue
                    try:
                        self._docker.stop(cname, time=2)
                    except Exception:
                        pass
                    try:
                        self._docker.remove(cname, force=True)
                        stats["running_removed"] += 1
                    except Exception:
                        pass
                else:
                    if not remove_exited:
                        continue
                    try:
                        self._docker.remove(cname, force=True)
                        stats["exited_removed"] += 1
                    except Exception:
                        pass
            except Exception:
                continue
        return stats

    def _cleanup_image(self, image_tag: str) -> None:
        """Remove a *per-run* eval image only. Never delete ``groket-base:*`` shared bases."""
        if not image_tag:
            return
        # Protect shared agent bases — they are intentionally long-lived caches.
        if image_tag.startswith("groket-base:") or image_tag.startswith("groket-base/"):
            return
        try:
            self._docker.image.remove(image_tag, force=True)
        except Exception:
            pass

    def run_evaluation(
        self,
        config: ContainerConfig,
        auth_json: Path,
        grok_config: Path,
        on_status: Callable | None = None,
        on_log: Callable | None = None,
        *,
        start_delay_s: float = 0.0,
    ) -> ContainerStatus:
        """Run a complete evaluation: build, start, wait, extract."""
        status = ContainerStatus(
            container_name=config.container_name,
            model=config.model,
        )
        self.containers[config.container_name] = status

        def _update(new_status: str, error: str = ""):
            status.status = new_status
            status.error = error
            if on_status:
                on_status(status)

        log_stop = threading.Event()
        log_thread: threading.Thread | None = None

        try:
            if self._abort.is_set():
                _update("aborted", "TUI detached; container may still be running")
                status.finished_at = datetime.now(UTC).isoformat()
                return status

            # Clean up any leftover container with the same name
            self.cleanup_container(config.container_name)

            _update("building")
            status.started_at = datetime.now(UTC).isoformat()
            image_tag = self.build_image(config, on_log=on_log)

            # Stagger multi-model starts so we don't thundering-herd the API / updater.
            if start_delay_s and start_delay_s > 0:
                if on_log:
                    on_log(
                        config.container_name,
                        f">>> Stagger start delay {start_delay_s:.1f}s (multi-model)",
                    )
                # Interruptible sleep so quit during stagger does not hang.
                if self._abort.wait(timeout=float(start_delay_s)):
                    _update("aborted", "TUI detached during start stagger")
                    status.finished_at = datetime.now(UTC).isoformat()
                    return status

            if self._abort.is_set():
                _update("aborted", "TUI detached; container may still be running")
                status.finished_at = datetime.now(UTC).isoformat()
                return status

            _update("running")
            if on_log:
                on_log(
                    config.container_name,
                    f">>> Starting container model={config.model!r} image={image_tag}",
                )
            container_id = self.start_container(config, image_tag, auth_json, grok_config)
            status.container_id = container_id

            # Stream logs in a background thread while the container runs
            if on_log:
                log_thread = threading.Thread(
                    target=self.stream_container_logs,
                    args=(config.container_name, on_log, log_stop),
                    daemon=True,
                )
                log_thread.start()

            # Poll traces mount so session_dir is known mid-run (TUI/browser monitor).
            exit_code = self.wait_for_container_with_session_peek(
                config.container_name,
                status,
                on_status=on_status,
            )

            # Detach path: leave container running; do not extract/cleanup images.
            if self._abort.is_set():
                log_stop.set()
                if log_thread is not None:
                    log_thread.join(timeout=0.2)
                _update("aborted", "TUI detached; container still running for resume")
                status.finished_at = datetime.now(UTC).isoformat()
                return status

            # Let log streaming drain remaining output
            if log_thread is not None:
                log_thread.join(timeout=5.0)
                if log_thread.is_alive():
                    log_stop.set()
                    log_thread.join(timeout=2.0)

            _update("extracting")
            session_dir = self.extract_traces(config.container_name)
            if session_dir is not None:
                status.session_dir = session_dir

            if exit_code != 0 and status.session_dir is None:
                logs = self.get_container_logs(config.container_name)
                # Prefer the tail of logs (clone/setup errors land late; banners early).
                tail = (logs or "")[-1200:].strip()
                _update("failed", f"Exit code {exit_code}. Logs (tail): {tail}")
            else:
                _update("completed")

            status.finished_at = datetime.now(UTC).isoformat()

        except Exception as e:
            if log_thread is not None and log_thread.is_alive():
                log_stop.set()
                log_thread.join(timeout=2.0)
            # Still fix ownership if the container wrote anything before failing/kill
            try:
                traces_dir = self.work_dir / "traces" / config.container_name
                self.fix_traces_ownership(traces_dir)
                if status.session_dir is None:
                    sd = self.peek_session_dir(config.container_name)
                    if sd is not None:
                        status.session_dir = sd
            except Exception:
                pass
            _update("failed", str(e))
            status.finished_at = datetime.now(UTC).isoformat()

        # Clean up container and image
        if on_log:
            on_log(config.container_name, ">>> Cleaning up container...")
        self.cleanup_container(config.container_name)
        if on_log:
            on_log(config.container_name, ">>> Cleaning up image...")
        self._cleanup_image(f"groket-eval:{config.container_name}")
        if on_log:
            on_log(config.container_name, ">>> Cleanup done.")

        return status

    def run_parallel_evaluations(
        self,
        configs: list[ContainerConfig],
        auth_json: Path,
        grok_config: Path,
        on_status: Callable | None = None,
        on_log: Callable | None = None,
    ) -> list[ContainerStatus]:
        """Run multiple evaluations in parallel (one thread per container config).

        Each *config* is typically one model; the runner passes one config per model.
        Exceptions in a single worker are captured inside ``run_evaluation`` (failed
        status) so sibling models still run to completion.

        Starts are staggered (~4s apart) so parallel models do not all hit the API
        and internal auto-update at the exact same instant.
        """
        if not configs:
            return []
        n = max(1, len(configs))
        stagger = 4.0 if n > 1 else 0.0
        # Daemon threads only — ThreadPoolExecutor workers are non-daemon and
        # block process exit while stuck in docker.wait (TUI quit hang).
        slots: list[ContainerStatus | None] = [None] * n
        errors: list[BaseException | None] = [None] * n

        def _run_one(index: int, cfg: ContainerConfig) -> None:
            try:
                slots[index] = self.run_evaluation(
                    cfg,
                    auth_json,
                    grok_config,
                    on_status,
                    on_log,
                    start_delay_s=stagger * index,
                )
            except BaseException as exc:  # noqa: BLE001 — capture for slot; rare
                errors[index] = exc

        threads = [
            threading.Thread(
                target=_run_one,
                args=(i, cfg),
                name=f"groket-eval-{cfg.container_name[:20]}",
                daemon=True,
            )
            for i, cfg in enumerate(configs)
        ]
        for th in threads:
            th.start()

        # Poll joins so abort can return without waiting on docker.
        while any(th.is_alive() for th in threads):
            if self._abort.is_set():
                break
            for th in threads:
                th.join(timeout=0.25)

        out: list[ContainerStatus] = []
        for i, cfg in enumerate(configs):
            slot = slots[i]
            if slot is not None:
                out.append(slot)
                continue
            err = errors[i]
            if err is not None:
                out.append(
                    ContainerStatus(
                        container_name=cfg.container_name,
                        model=cfg.model,
                        status="failed",
                        error=str(err),
                    )
                )
            elif self._abort.is_set():
                out.append(
                    ContainerStatus(
                        container_name=cfg.container_name,
                        model=cfg.model,
                        status="aborted",
                        error="TUI detached; container may still be running",
                    )
                )
            else:
                out.append(
                    ContainerStatus(
                        container_name=cfg.container_name,
                        model=cfg.model,
                        status="failed",
                        error="evaluation thread ended without status",
                    )
                )
        return out
