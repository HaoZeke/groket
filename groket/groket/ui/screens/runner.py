"""Session runner screen — configure and launch parallel grok evaluations.

Runs are owned by the app-level ``RunManager`` so you can leave this screen
(Esc) while containers keep building/running in the background.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape as rich_escape
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from ...capabilities.merge import merge_capabilities
from ...constants import DEFAULT_MAX_TURNS, normalize_max_turns
from ...docker.base_profiles import DEFAULT_DOCKER_IMAGE
from ...docker.orchestrator import ContainerStatus
from ...models import json_as_str_list
from ...runs.run_manager import BackgroundRun, RunManager
from ...utils import fmt_duration as _format_duration
from .. import text as U
from ..bindings import RUNNER, ChromeActions
from ..forms import (
    PERSONA_NONE,
    docker_select_options,
    docker_select_value_or_default,
    model_selection_items,
    normalize_docker_choice,
    normalize_persona_id,
    persona_select_options,
    persona_select_value,
    select_value_str,
    selection_list_selected_ids,
)
from ..i18n import join_ui, t
from ..panel_render import TipSurface
from ..tab_panes import TabPaneNavigation

logger = logging.getLogger(__name__)


@dataclass
class RunnerPrefill:
    """Pre-fill data for the runner form, e.g. when re-running a session."""

    prompt: str = ""
    setup_instructions: str = ""
    docker_image: str = ""
    repo_url: str = ""
    repo_branch: str = ""
    # Host directory bind-mounted as /workspace (live; no clone).
    repo_path: str = ""
    models: list[str] = field(default_factory=list)
    persona_id: str = ""
    run_mcp_servers: list[str] = field(default_factory=list)
    run_mcp_definitions: list = field(default_factory=list)
    run_skills: list[str] = field(default_factory=list)
    run_plugins: list[str] = field(default_factory=list)
    run_env_vars: dict = field(default_factory=dict)
    # Run-only inline skills: (id, SKILL.md body)
    run_inline_skills: list[tuple[str, str]] = field(default_factory=list)
    # Resume ended session as a new interactive multi-turn launch.
    interactive: bool = False
    resume_session_id: str = ""
    resume_source_dir: str = ""
    # Parent ``head_commit`` for post-clone checkout / ``--restore-code``.
    repo_commit: str = ""
    restore_code: bool = False
    # Grok ``--max-turns`` (agent steps per prompt); 0 / unset → default 50.
    max_turns: int = 0
    # Opt-in ``grok --yolo`` (default false → --always-approve).
    yolo: bool = False


class RunnerScreen(TabPaneNavigation, ChromeActions):
    """Screen for configuring and launching evaluation runs."""

    BINDINGS = list(RUNNER)
    TAB_CONTENT_ID = "runner-tabs"
    TAB_PANES = (
        "runner-tab-recipe",
        "runner-tab-runtime",
        "runner-tab-extras",
    )

    def action_tab_recipe(self) -> None:
        self.activate_tab_pane("runner-tab-recipe")

    def action_tab_runtime(self) -> None:
        self.activate_tab_pane("runner-tab-runtime")

    def action_tab_extras(self) -> None:
        self.activate_tab_pane("runner-tab-extras")

    class StatusUpdate(Message):
        """A container status change."""

        def __init__(self, status: ContainerStatus) -> None:
            super().__init__()
            self.status = status

    class RunFinished(Message):
        """The evaluation run completed."""

        def __init__(self, run: BackgroundRun) -> None:
            super().__init__()
            self.run = run

    def __init__(
        self,
        work_dir: Path,
        run_manager: RunManager | None = None,
        prefill: RunnerPrefill | None = None,
        config_id: str | None = None,
        config_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.work_dir = work_dir
        self.prefill = prefill
        self._config_id = config_id
        self._config_name = config_name
        self._run_manager = run_manager
        self._known_rows: set[str] = set()
        self._subscribed = False
        self._last_run_id: str | None = None
        self._restoring_status = False
        self._persona_id: str = (prefill.persona_id or "").strip() if prefill is not None else ""
        if prefill is not None:
            self._run_mcp_ids: list[str] = list(prefill.run_mcp_servers or [])
            self._run_mcp_definitions: list[dict] = [
                dict(d) for d in prefill.run_mcp_definitions or [] if isinstance(d, dict)
            ]
            self._run_skills_ids: list[str] = list(prefill.run_skills or [])
            self._run_plugins_ids: list[str] = list(getattr(prefill, "run_plugins", None) or [])
            self._run_env_vars: dict[str, str] = {
                str(k): str(v) for k, v in (prefill.run_env_vars or {}).items() if k
            }
            self._run_inline_skills: list[tuple[str, str]] = [
                (str(n), str(b))
                for n, b in (getattr(prefill, "run_inline_skills", None) or [])
                if (n or "").strip()
            ]
            self._resume_session_id = (prefill.resume_session_id or "").strip()
            self._resume_source_dir = (prefill.resume_source_dir or "").strip()
            self._repo_commit = (prefill.repo_commit or "").strip()
            self._restore_code = bool(prefill.restore_code) or bool(
                self._resume_session_id or self._resume_source_dir
            )
            self._prefill_interactive = bool(prefill.interactive)
        else:
            self._run_mcp_ids = []
            self._run_mcp_definitions = []
            self._run_skills_ids = []
            self._run_plugins_ids = []
            self._run_env_vars = {}
            self._run_inline_skills = []
            self._resume_session_id = ""
            self._resume_source_dir = ""
            self._repo_commit = ""
            self._restore_code = False
            self._prefill_interactive = False
        self._pending_model_skips: list[str] | None = None
        self._clean_snapshot: tuple[str, ...] | None = None

    @property
    def run_manager(self) -> RunManager:
        if self._run_manager is not None:
            return self._run_manager
        app_rm = getattr(self.app, "run_manager", None)
        if app_rm is not None:
            return app_rm
        self._run_manager = RunManager(self.work_dir)
        return self._run_manager

    def compose(self) -> ComposeResult:
        from ..brand_mark import AppChrome, AppFooter

        yield AppChrome()
        with Vertical(id="runner-screen"):
            with Vertical(id="runner-main"):
                yield Static(f"[bold]{U.runner_title()}[/bold]", id="runner-title")
                with TabbedContent(id="runner-tabs"):
                    with TabPane(U.runner_tab_recipe(), id="runner-tab-recipe"):
                        with VerticalScroll(classes="runner-pane"):
                            yield Label(U.config_name_label())
                            yield Input(
                                value=self._config_name or "",
                                placeholder=U.config_name_placeholder(),
                                id="config-name-input",
                            )
                            yield Label(U.prompt_label())
                            yield TextArea(id="prompt-input", language="markdown")
                            yield Label(U.setup_commands_label())
                            yield TextArea(
                                id="setup-input", language="bash", classes="runner-setup"
                            )
                            yield Checkbox(
                                t("ui-interactive-multi-turn-follow-ups-until-done"),
                                id="interactive-multi-turn",
                                value=True,
                            )
                            yield Checkbox(
                                t("ui-yolo-auto-approve-tools"),
                                id="yolo-mode",
                                value=False,
                            )
                            yield Static(
                                "",
                                id="resume-session-hint",
                                classes="runner-status-line",
                            )
                            yield Label(U.repository_label())
                            with Horizontal(id="repo-row", classes="runner-inline-row"):
                                yield Input(
                                    placeholder=U.repo_url_placeholder(),
                                    id="repo-url-input",
                                    classes="runner-repo-url",
                                )
                                yield Input(
                                    placeholder=U.branch_placeholder(),
                                    id="repo-branch-input",
                                    classes="runner-repo-branch",
                                )
                            yield Label(U.repo_path_label())
                            yield Input(
                                placeholder=U.repo_path_placeholder(),
                                id="repo-path-input",
                                classes="runner-repo-path",
                            )
                            yield Static("", id="github-write-hint", classes="runner-status-line")
                    with TabPane(U.runner_tab_runtime(), id="runner-tab-runtime"):
                        with VerticalScroll(classes="runner-pane"):
                            with Horizontal(id="runtime-top-row", classes="runner-inline-row"):
                                with Vertical(classes="runner-col"):
                                    yield Label(U.container_image_label())
                                    yield Select(
                                        options=docker_select_options(),
                                        value=DEFAULT_DOCKER_IMAGE,
                                        id="docker-image-select",
                                        allow_blank=False,
                                        classes="field-select",
                                    )
                                with Vertical(classes="runner-col"):
                                    yield Label(U.persona_label())
                                    with Horizontal(id="persona-row"):
                                        yield Select(
                                            options=persona_select_options(self.work_dir),
                                            value=PERSONA_NONE,
                                            id="persona-select",
                                            allow_blank=False,
                                            classes="field-select",
                                        )
                                        yield Button(
                                            t("ui-new"),
                                            id="persona-new-btn",
                                            classes="runner-inline-btn",
                                        )
                                        yield Button(
                                            t("ui-edit"),
                                            id="persona-builder-btn",
                                            classes="runner-inline-btn",
                                        )
                            yield Static("", id="docker-profile-hint", classes="runner-status-line")
                            yield Label(U.max_turns_label())
                            yield Input(
                                value=str(DEFAULT_MAX_TURNS),
                                placeholder=U.max_turns_placeholder(),
                                id="max-turns-input",
                                classes="runner-max-turns",
                            )
                            yield Static(
                                "",
                                id="runtime-launch-panel",
                                classes="runner-launch-panel",
                            )
                            yield Label(U.models_heading())
                            yield TipSurface(
                                U.tip_runner_models(),
                                id="runner-models-tip",
                            )
                            yield SelectionList[str](
                                *model_selection_items(None), id="models-select"
                            )
                            yield Static("", id="models-catalog-hint", classes="runner-status-line")
                    with TabPane(U.runner_tab_extras(), id="runner-tab-extras"):
                        with VerticalScroll(classes="runner-pane", id="runner-run-caps"):
                            yield Static(U.this_launch_only_dim(), classes="runner-field-hint")
                            yield Static(
                                "",
                                id="run-caps-persona-hint",
                                classes="runner-status-line",
                            )
                            with Horizontal(id="run-caps-actions", classes="runner-inline-row"):
                                yield Button(U.mcp_btn(), id="run-mcp-pick-btn", variant="primary")
                                yield Button(U.skills_btn(), id="run-skills-pick-btn")
                                yield Button(U.plugins_btn(), id="run-plugins-pick-btn")
                                yield Button(U.env_btn(), id="run-env-edit-btn")
                                yield Button(U.inline_skill_btn(), id="run-inline-skill-btn")
                                yield Button(
                                    U.clear_btn(),
                                    id="run-caps-clear-btn",
                                    classes="runner-secondary-btn",
                                )
                            yield Input(placeholder=U.search_mcp_placeholder(), id="run-mcp-search")
                            yield Label(U.added_to_this_run())
                            yield Static(U.em_dash_dim(), id="run-caps-summary")
                            yield Static("", id="run-caps-help", classes="runner-status-line")
            with Horizontal(id="runner-toolbar"):
                with Horizontal(id="runner-toolbar-primary"):
                    yield Button(U.launch(), variant="primary", id="launch-btn")
                    yield Button(U.save_config(), id="save-config-btn")
                yield TipSurface(U.tip_runner_toolbar(), id="runner-toolbar-hint")
        yield AppFooter()

    def on_mount(self) -> None:
        if self.prefill:
            pf = self.prefill
            if pf.prompt:
                self.query_one("#prompt-input", TextArea).load_text(pf.prompt)
            if pf.setup_instructions:
                self.query_one("#setup-input", TextArea).load_text(pf.setup_instructions)
            if pf.docker_image:
                try:
                    self.query_one(
                        "#docker-image-select", Select
                    ).value = docker_select_value_or_default(pf.docker_image)
                except Exception:
                    logger.debug(t("ui-failed-to-set-docker-image-prefill"), exc_info=True)
            if pf.repo_url:
                self.query_one("#repo-url-input", Input).value = pf.repo_url
            if pf.repo_branch:
                self.query_one("#repo-branch-input", Input).value = pf.repo_branch
            if pf.repo_path:
                self.query_one("#repo-path-input", Input).value = pf.repo_path
            if pf.models:
                self._set_models_selection(pf.models)
            if pf.persona_id is not None:
                self._persona_id = (pf.persona_id or "").strip()
                try:
                    self.query_one("#persona-select", Select).value = persona_select_value(
                        self._persona_id
                    )
                except Exception:
                    logger.debug(t("ui-failed-to-set-persona-prefill"), exc_info=True)
            if self._prefill_interactive or self._resume_session_id:
                try:
                    self.query_one("#interactive-multi-turn", Checkbox).value = True
                except Exception:
                    logger.debug("failed to set interactive prefill", exc_info=True)
            if pf.yolo:
                try:
                    self.query_one("#yolo-mode", Checkbox).value = True
                except Exception:
                    logger.debug("failed to set yolo prefill", exc_info=True)
            if pf.max_turns and int(pf.max_turns) > 0:
                try:
                    self.query_one("#max-turns-input", Input).value = str(
                        normalize_max_turns(pf.max_turns, default=DEFAULT_MAX_TURNS)
                    )
                except Exception:
                    logger.debug("failed to set max-turns prefill", exc_info=True)
            if self._resume_session_id:
                self._set_status_line(
                    "resume-session-hint",
                    t(
                        "runner-resume-session-hint",
                        sid=self._resume_session_id,
                    ),
                )
            else:
                self._set_status_line("resume-session-hint", "")
        for wid in (
            "docker-profile-hint",
            "models-catalog-hint",
            "run-caps-help",
        ):
            self._set_status_line(wid, "")
        # Refresh persona options after mount (compose may have raced empty store).
        self.call_after_refresh(
            lambda: self._refresh_persona_select(select_id=self._persona_id or None)
        )
        self.call_after_refresh(self._rebuild_run_capability_lists)
        self.call_after_refresh(self._restore_run_state)
        self.call_after_refresh(
            lambda: self.call_after_refresh(lambda: self.activate_tab_pane("runner-tab-recipe"))
        )
        # After models / persona widgets settle — baseline for Esc discard.
        self.call_after_refresh(lambda: self.call_after_refresh(self._capture_clean_snapshot))

    def _persona_obj(self):
        """Loaded persona for the current selection, or None."""
        pid = self._persona_id_from_form()
        if not pid:
            return None
        try:
            from ...runs.personas import PersonaStore

            return PersonaStore(self.work_dir).get(pid)
        except Exception:
            logger.debug(t("ui-failed-to-load-persona-s"), pid, exc_info=True)
            return None

    def _persona_github_write(self) -> bool:
        """GitHub write is persona-only (not a run/form property)."""
        p = self._persona_obj()
        return bool(p and p.github_write)

    def _persona_github_token(self) -> str:
        """Token from selected persona only (stored or github_token_env on host)."""
        p = self._persona_obj()
        if not p:
            return ""
        try:
            return p.resolve_github_token()
        except Exception:
            return ""

    def _persona_status_line(self) -> tuple[str, bool, str]:
        """Persona id line for Runtime panel + whether GH write is on and token label."""
        p = self._persona_obj()
        pid = (self._persona_id_from_form() or self._persona_id or "").strip() or "(none)"
        gh = bool(p is not None and p.github_write)
        tok = self._persona_github_token()
        if p and (p.github_token or "").strip():
            tok_src = t("ui-stored-on-persona")
        elif p and (p.github_token_env or "").strip():
            tok_src = join_ui(t("ui-host-env"), p.github_token_env)
        elif tok:
            tok_src = t("runner-token-resolved")
        else:
            tok_src = t("runner-token-none")
        try:
            from ...docker.orchestrator import describe_github_write_token_status

            tok_status = describe_github_write_token_status(ui_token=tok)
        except Exception:
            tok_status = t("ui-token-status-unknown")
        mcp_n = len(p.mcp_servers or []) if p else 0
        sk_n = len(p.skills or []) if p else 0
        pl_n = len(p.plugins or []) if p else 0
        caps = t(
            "runner-persona-caps-counts",
            mcp=mcp_n,
            skills=sk_n,
            plugins=pl_n,
        )
        if gh:
            line = t(
                "runner-persona-status-gh-on",
                pid=pid,
                token=tok_src,
                caps=caps,
            )
            if tok_status:
                line = join_ui(line, "·", tok_status)
        else:
            line = t("runner-persona-status-gh-off", pid=pid, caps=caps)
        return line, gh, tok_src

    def _sync_persona_github_hint(self) -> None:
        """Refresh persona/GH chrome on Recipe + Runtime tabs."""
        persona_line, gh, tok_src = self._persona_status_line()
        if gh:
            self._set_status_line(
                "github-write-hint",
                t("runner-github-write-hint-on", token=tok_src),
            )
        else:
            self._set_status_line("github-write-hint", "")
        self._update_runtime_launch_panel(persona_line=persona_line)

    def _rebuild_models_selection(
        self, selected: list[str] | None = None, *, default_select_all: bool = False
    ) -> None:
        """Replace model options from live ``grok models`` catalog; re-apply ticks."""
        try:
            sl = self.query_one("#models-select", SelectionList)
        except Exception:
            return
        items = model_selection_items(selected, default_select_all=default_select_all)
        with suppress(Exception):
            sl.clear_options()
        from textual.widgets.selection_list import Selection

        for label, token, is_on in items:
            try:
                # Explicit Selection + id=token avoids Textual prompt.split()[0] collisions
                # when labels contain spaces (effort variants for the same model).
                sl.add_option(Selection(label, token, is_on, id=token))
            except Exception:
                with suppress(Exception):
                    sl.add_option((label, token, is_on))
                if is_on:
                    with suppress(Exception):
                        sl.select(token)
                continue
            # initial_state on Selection is applied at mount; select() if already mounted
            if is_on:
                with suppress(Exception):
                    sl.select(token)
        try:
            from ...runs.batch import models_catalog_help_text

            hint = models_catalog_help_text()
            self._set_status_line("models-catalog-hint", f"[dim]{hint}[/dim]" if hint else "")
        except Exception:
            pass

    def _set_status_line(self, widget_id: str, text: str) -> None:
        """Update a status Static and hide it when empty (saves a row in small terminals)."""
        with suppress(Exception):
            w = self.query_one(f"#{widget_id}", Static)
            body = (text or "").strip()
            w.update(body)
            w.display = bool(body)

    def _set_models_selection(self, models: list[str]) -> None:
        """Set model SelectionList ticks; rebuilds options if catalog drifted."""
        try:
            sl = self.query_one("#models-select", SelectionList)
        except Exception:
            return
        from ...session.models_catalog import normalize_model_selection_tokens

        want = normalize_model_selection_tokens([m.strip() for m in models if (m or "").strip()])
        from ..forms import load_model_launch_options

        launch_tokens = {tok for _, tok in load_model_launch_options()}
        existing_ids: set[str] = set()
        try:
            for opt in sl._options:
                vid = getattr(opt, "id", None) or getattr(opt, "value", None)
                if vid is not None:
                    existing_ids.add(str(vid))
        except Exception:
            existing_ids = set()
        if existing_ids != launch_tokens:
            self._rebuild_models_selection(want, default_select_all=False)
            return
        try:
            for sid in list(sl.selected):
                try:
                    sl.deselect(sid)
                except Exception:
                    pass
        except Exception:
            pass
        for tok in sorted(set(want) - existing_ids):
            with suppress(Exception):
                sl.add_option((f"{tok} [not in catalog]", tok))
        for tok in want:
            with suppress(Exception):
                sl.select(tok)

    def _persona_id_from_form(self) -> str:
        """Persona id from the form Select (empty string = None / no persona).

        Empty selection must clear a previous persona — do not fall back to a
        stale ``_persona_id`` (that ignored the "None" option).
        """
        try:
            sel = self.query_one("#persona-select", Select)
            pid = normalize_persona_id(sel.value)
            self._persona_id = pid
            return pid
        except Exception:
            return self._persona_id or ""

    @on(Select.Changed, "#persona-select")
    def _persona_changed(self, event: Select.Changed) -> None:
        # Prefer the event value — more reliable than re-query mid-update.
        # Empty string is intentional (None persona); do not restore the prior id.
        self._persona_id = normalize_persona_id(event.value)
        self._sync_persona_github_hint()
        self._update_run_caps_persona_hint()
        self._update_run_caps_summary()

    def on_unmount(self) -> None:
        pass

    def _persona_capability_snapshot(
        self,
    ) -> tuple[list[str], list[str], list[str], dict[str, str]]:
        """Persona base: MCP ids, skills, plugins, env vars."""
        pid = self._persona_id_from_form()
        if not pid:
            return ([], [], [], {})
        try:
            from ...runs.personas import PersonaStore

            p = PersonaStore(self.work_dir).get(pid)
            if not p:
                return ([], [], [], {})
            return (
                list(p.mcp_servers or []),
                list(p.skills or []),
                list(p.plugins or []),
                dict(p.env_vars or {}),
            )
        except Exception:
            logger.debug(t("ui-failed-to-load-persona-capabilities-for-s"), pid, exc_info=True)
            return ([], [], [], {})

    def _rebuild_run_capability_lists(self) -> None:
        """Refresh persona hint + effective launch summary (+ Runtime compact line)."""
        self._sync_persona_github_hint()
        self._update_run_caps_persona_hint()
        self._update_run_caps_summary()

    def _run_mcp_display_rows(self) -> list[tuple[str, str, str]]:
        """(id, title, transport) for each run-extra MCP, defs preferred for title."""
        defs_by_id: dict[str, dict] = {}
        for d in self._run_mcp_definitions or []:
            if not isinstance(d, dict):
                continue
            did = str(d.get("id") or "").strip()
            if did:
                defs_by_id[did] = d
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for mid in self._run_mcp_ids or []:
            mid = (mid or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            d = defs_by_id.get(mid) or {}
            title = str(d.get("title") or d.get("registry_name") or "").strip()
            transport = str(d.get("transport") or "").strip()
            rows.append((mid, title, transport))
        for did, d in defs_by_id.items():
            if did in seen:
                continue
            title = str(d.get("title") or d.get("registry_name") or "").strip()
            transport = str(d.get("transport") or "").strip()
            rows.append((did, title, transport))
        return rows

    def _effective_launch_caps(
        self,
    ) -> tuple[list[str], list[str], list[str], dict[str, str], list[str]]:
        """Merged MCP / skills / plugins / env keys + inline skill ids for this launch."""
        p_mcp, p_skills, p_plugins, p_env = self._persona_capability_snapshot()
        run_mcp = [mid for mid, _t, _tr in self._run_mcp_display_rows() if (mid or "").strip()]
        run_skills = [s for s in self._run_skills_ids or [] if (s or "").strip()]
        run_plugins = [s for s in self._run_plugins_ids or [] if (s or "").strip()]
        run_env = dict(self._run_env_vars or {})
        inline_ids = [
            str(n).strip() for n, _b in (self._run_inline_skills or []) if (n or "").strip()
        ]
        p_defs = []
        try:
            p = self._persona_obj()
            if p is not None:
                p_defs = list(p.mcp_definitions or [])
        except Exception:
            p_defs = []
        merged = merge_capabilities(
            persona_mcp_servers=p_mcp,
            persona_mcp_definitions=p_defs,
            persona_skills=p_skills,
            persona_plugins=p_plugins,
            run_mcp_servers=run_mcp,
            run_mcp_definitions=list(self._run_mcp_definitions or []),
            run_skills=run_skills,
            run_plugins=run_plugins,
        )
        eff_mcp = [rich_escape(x) for x in json_as_str_list(merged.get("mcp_servers"))]
        eff_skills = [rich_escape(x) for x in json_as_str_list(merged.get("skills"))]
        eff_plugins = [rich_escape(x) for x in json_as_str_list(merged.get("plugins"))]
        eff_env = {**p_env, **run_env}
        return (
            eff_mcp,
            eff_skills,
            eff_plugins,
            eff_env,
            [rich_escape(x) for x in inline_ids],
        )

    def _update_runtime_launch_panel(self, *, persona_line: str | None = None) -> None:
        """Unified Runtime panel: persona/GH + effective plugins/skills/MCP/inline/env."""
        try:
            w = self.query_one("#runtime-launch-panel", Static)
        except Exception:
            return
        if persona_line is None:
            persona_line, _gh, _tok = self._persona_status_line()

        eff_mcp, eff_skills, eff_plugins, eff_env, inline = self._effective_launch_caps()

        def _bullets(items: list[str]) -> list[str]:
            if not items:
                return [t("runner-caps-none")]
            return [t("runner-caps-item", name=name) for name in items]

        def _env_keys(env: dict[str, str]) -> str:
            if not env:
                return "—"
            keys = sorted(env)
            body = ", ".join(keys[:12])
            return body + ("…" if len(keys) > 12 else "")

        lines = [
            t("runner-runtime-panel-heading"),
            persona_line,
            "",
            t("runner-caps-section-plugins", n=len(eff_plugins)),
            *_bullets(eff_plugins),
            t("runner-caps-section-skills", n=len(eff_skills)),
            *_bullets(eff_skills),
            t("runner-caps-section-inline", n=len(inline)),
            *_bullets(inline),
            t("runner-caps-section-mcp", n=len(eff_mcp)),
            *_bullets(eff_mcp),
            t("runner-caps-section-env", n=len(eff_env), keys=_env_keys(eff_env)),
        ]
        w.update("\n".join(lines))
        w.display = True

    def _update_run_caps_summary(self) -> None:
        """Show persona base + this-run extras + effective (merged) launch config."""
        try:
            w = self.query_one("#run-caps-summary", Static)
        except Exception:
            return
        p_mcp, p_skills, p_plugins, p_env = self._persona_capability_snapshot()
        run_mcp = [mid for mid, _t, _tr in self._run_mcp_display_rows() if (mid or "").strip()]
        run_skills = [s for s in self._run_skills_ids or [] if (s or "").strip()]
        run_plugins = [s for s in self._run_plugins_ids or [] if (s or "").strip()]
        run_env = dict(self._run_env_vars or {})
        inline = [str(n).strip() for n, _b in (self._run_inline_skills or []) if (n or "").strip()]
        eff_mcp, eff_skills, eff_plugins, eff_env, eff_inline = self._effective_launch_caps()

        def _bullet_list(items: list[str], *, empty: str) -> list[str]:
            if not items:
                return [empty]
            return [t("runner-caps-item", name=name) for name in items]

        def _esc_list(items: list[str]) -> list[str]:
            return [rich_escape(x) for x in items]

        def _env_keys(env: dict[str, str]) -> str:
            if not env:
                return "—"
            keys = sorted(env)
            body = ", ".join(keys[:12])
            return body + ("…" if len(keys) > 12 else "")

        lines: list[str] = [
            t("runner-caps-effective-heading"),
            t("runner-caps-section-plugins", n=len(eff_plugins)),
            *_bullet_list(eff_plugins, empty=t("runner-caps-none")),
            t("runner-caps-section-skills", n=len(eff_skills)),
            *_bullet_list(eff_skills, empty=t("runner-caps-none")),
            t("runner-caps-section-inline", n=len(eff_inline)),
            *_bullet_list(eff_inline, empty=t("runner-caps-none")),
            t("runner-caps-section-mcp", n=len(eff_mcp)),
            *_bullet_list(eff_mcp, empty=t("runner-caps-none")),
            t("runner-caps-section-env", n=len(eff_env), keys=_env_keys(eff_env)),
            "",
            t("runner-caps-persona-heading"),
            t("runner-caps-section-plugins", n=len(p_plugins)),
            *_bullet_list(_esc_list(p_plugins), empty=t("runner-caps-none")),
            t("runner-caps-section-skills", n=len(p_skills)),
            *_bullet_list(_esc_list(p_skills), empty=t("runner-caps-none")),
            t("runner-caps-section-mcp", n=len(p_mcp)),
            *_bullet_list(_esc_list(p_mcp), empty=t("runner-caps-none")),
            t("runner-caps-section-env", n=len(p_env), keys=_env_keys(p_env)),
            "",
            t("runner-caps-run-heading"),
            t("runner-caps-section-plugins", n=len(run_plugins)),
            *_bullet_list(_esc_list(run_plugins), empty=t("runner-caps-none")),
            t("runner-caps-section-skills", n=len(run_skills)),
            *_bullet_list(_esc_list(run_skills), empty=t("runner-caps-none")),
            t("runner-caps-section-inline", n=len(inline)),
            *_bullet_list(_esc_list(inline), empty=t("runner-caps-none")),
            t("runner-caps-section-mcp", n=len(run_mcp)),
            *_bullet_list(_esc_list(run_mcp), empty=t("runner-caps-none")),
            t("runner-caps-section-env", n=len(run_env), keys=_env_keys(run_env)),
        ]
        w.update("\n".join(lines))
        self._update_runtime_launch_panel()

    def _run_mcp_query_from_form(self) -> str:
        try:
            return (self.query_one("#run-mcp-search", Input).value or "").strip()
        except Exception:
            return ""

    def _open_run_mcp_picker(
        self, *, initial_query: str = "", auto_registry: bool | None = None
    ) -> None:
        """Open the same McpPickerModal as Personas (registry search + configure)."""
        from .personas import McpPickerModal

        current = list(self._run_mcp_ids or [])
        defs = list(self._run_mcp_definitions or [])
        q = (initial_query or "").strip()
        if auto_registry is None:
            auto_registry = bool(q)

        def _done(result) -> None:
            if result is None:
                return
            ids, new_defs, env_add, skills_add = result
            self._run_mcp_ids = list(ids or [])
            self._run_mcp_definitions = [dict(d) for d in new_defs or [] if isinstance(d, dict)]
            if env_add:
                for k, v in env_add.items():
                    kk = (k or "").strip()
                    if not kk:
                        continue
                    if (
                        kk not in self._run_env_vars
                        or not (self._run_env_vars.get(kk) or "").strip()
                    ):
                        self._run_env_vars[kk] = str(v if v is not None else "")
            if skills_add:
                for s in skills_add:
                    ss = (s or "").strip()
                    if ss and ss not in self._run_skills_ids:
                        self._run_skills_ids.append(ss)
            self._update_run_caps_summary()
            self.notify(
                join_ui(
                    t("ui-run-extras"),
                    len(self._run_mcp_ids),
                    t("ui-mcp-2"),
                    len(self._run_skills_ids),
                    t("ui-skills-4"),
                    len(self._run_plugins_ids),
                    t("ui-plugins-persona-unchanged"),
                ),
                severity="information",
                timeout=6,
            )

        self.app.push_screen(
            McpPickerModal(
                self.work_dir,
                current,
                defs,
                initial_query=q,
                auto_registry=auto_registry,
                heading=t("ui-mcp"),
                keep_hint=t("ui-save-run-config-launch-to-keep-persona-unchanged"),
            ),
            _done,
        )

    @on(Input.Submitted, "#run-mcp-search")
    def _run_mcp_search_submitted(self, event: Input.Submitted) -> None:
        q = (event.value or "").strip()
        self._open_run_mcp_picker(initial_query=q, auto_registry=bool(q))

    @on(Button.Pressed, "#run-mcp-pick-btn")
    def _run_mcp_pick_btn(self) -> None:
        q = self._run_mcp_query_from_form()
        self._open_run_mcp_picker(initial_query=q, auto_registry=bool(q))

    @on(Button.Pressed, "#run-skills-pick-btn")
    def _run_skills_pick_btn(self) -> None:
        from .personas import SkillsPickerModal

        current = list(self._run_skills_ids or [])

        def _done(result: list[str] | None) -> None:
            if result is None:
                return
            self._run_skills_ids = list(result or [])
            self._update_run_caps_summary()

        self.app.push_screen(SkillsPickerModal(self.work_dir, current), _done)

    @on(Button.Pressed, "#run-plugins-pick-btn")
    def _run_plugins_pick_btn(self) -> None:
        from .personas import PluginPickerModal

        current = list(self._run_plugins_ids or [])

        def _done(result: list[str] | None) -> None:
            if result is None:
                return
            self._run_plugins_ids = list(result or [])
            self._update_run_caps_summary()

        self.app.push_screen(PluginPickerModal(self.work_dir, current), _done)

    @on(Button.Pressed, "#run-env-edit-btn")
    def _run_env_edit_btn(self) -> None:
        from ..env_modals import EnvEditorModal

        def _done(result: dict[str, str] | None) -> None:
            if result is None:
                return
            self._run_env_vars = dict(result)
            self._update_run_caps_summary()
            self.notify(
                t("ui-run-env-saved", count=len(self._run_env_vars)),
                severity="information",
                timeout=4,
            )

        self.app.push_screen(
            EnvEditorModal(self._run_env_vars, title=t("env-editor-run-title")),
            _done,
        )

    @on(Button.Pressed, "#run-inline-skill-btn")
    def _run_inline_skill_btn(self) -> None:
        from ..env_modals import InlineSkillModal

        def _done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            name, body = result
            updated: list[tuple[str, str]] = []
            replaced = False
            for n, b in self._run_inline_skills or []:
                if n == name:
                    updated.append((name, body))
                    replaced = True
                else:
                    updated.append((n, b))
            if not replaced:
                updated.append((name, body))
            self._run_inline_skills = updated
            self._update_run_caps_summary()
            self.notify(
                t("ui-inline-skill-saved", name=name),
                severity="information",
                timeout=4,
            )

        self.app.push_screen(InlineSkillModal(), _done)

    @on(Button.Pressed, "#run-caps-clear-btn")
    def _run_caps_clear_btn(self) -> None:
        self._run_mcp_ids = []
        self._run_mcp_definitions = []
        self._run_skills_ids = []
        self._run_plugins_ids = []
        self._run_env_vars = {}
        self._run_inline_skills = []
        self._update_run_caps_summary()
        self.notify(
            t("ui-cleared-run-only-mcp-skills-plugins-extras"), severity="information", timeout=4
        )

    def _update_run_caps_persona_hint(self) -> None:
        p_mcp, p_skills, p_plugins, _p_env = self._persona_capability_snapshot()
        pid = self._persona_id_from_form() or "(none)"
        body = t(
            "runner-caps-persona-hint",
            pid=pid,
            mcp=", ".join(p_mcp[:6]) + ("…" if len(p_mcp) > 6 else "") or "—",
            skills=", ".join(p_skills[:6]) + ("…" if len(p_skills) > 6 else "") or "—",
            plugins=", ".join(p_plugins[:6]) + ("…" if len(p_plugins) > 6 else "") or "—",
        )
        self._set_status_line("run-caps-persona-hint", body)

    def _run_mcp_from_form(self) -> list[str]:
        return list(self._run_mcp_ids or [])

    def _run_skills_from_form(self) -> list[str]:
        return list(self._run_skills_ids or [])

    def _run_plugins_from_form(self) -> list[str]:
        return list(self._run_plugins_ids or [])

    def _run_mcp_definitions_from_form(self) -> list[dict]:
        return [dict(d) for d in self._run_mcp_definitions or [] if isinstance(d, dict)]

    def _run_env_from_form(self) -> dict[str, str]:
        return dict(self._run_env_vars or {})

    def _run_inline_skills_from_form(self) -> list[tuple[str, str]]:
        return [(n, b) for n, b in (self._run_inline_skills or []) if (n or "").strip()]

    def refresh_tip_surfaces(self) -> None:
        """Re-apply all ``TipSurface`` widgets after global show_tips toggle."""
        from ..panel_render import refresh_tip_surfaces_in

        refresh_tip_surfaces_in(self)

    def action_open_jobs(self) -> None:
        from .jobs import JobsModal

        self.app.push_screen(JobsModal(self.run_manager, work_dir=self.work_dir))

    @on(Button.Pressed, "#persona-new-btn")
    def _persona_new_btn(self) -> None:
        self.action_new_persona_from_runner()

    @on(Button.Pressed, "#persona-builder-btn")
    def _persona_builder_btn(self) -> None:
        """In-recipe control (not the removed bottom toolbar Personas button)."""
        self.action_open_persona_builder()

    def _refresh_persona_select(self, *, select_id: str | None = None) -> None:
        """Reload persona options; optionally select a persona_id."""
        try:
            from ...runs.personas import PersonaStore

            PersonaStore(self.work_dir).ensure_defaults()
        except Exception:
            logger.debug(t("ui-failed-to-ensure-persona-defaults"), exc_info=True)
        try:
            ps = self.query_one("#persona-select", Select)
            target = persona_select_value(
                select_id if select_id is not None else normalize_persona_id(ps.value)
            )
            ps.set_options(persona_select_options(self.work_dir))
            try:
                ps.value = target
            except Exception:
                try:
                    ps.value = PERSONA_NONE
                except Exception:
                    pass
            self._persona_id = normalize_persona_id(ps.value)
            self._sync_persona_github_hint()
        except Exception:
            pass

    def action_new_persona_from_runner(self) -> None:
        """Create a persona inline from the Runner (editor modal only)."""
        from .personas import PersonaEditorModal

        def _done(result: object | None) -> None:
            if result is None:
                return
            pid = getattr(result, "persona_id", None) or ""
            self._refresh_persona_select(select_id=str(pid) if pid else None)
            if pid:
                self.notify(
                    join_ui(t("ui-persona-ready"), pid, t("ui-selected-5")),
                    severity="information",
                    timeout=6,
                )
            else:
                self.notify(U.persona_saved(), severity="information", timeout=4)

        self.app.push_screen(PersonaEditorModal(self.work_dir, persona=None, is_new=True), _done)

    def action_open_persona_builder(self) -> None:
        """Open full persona builder; refresh dropdown on return."""
        from .personas import PersonasScreen

        def _back(_: object | None = None) -> None:
            try:
                self._refresh_persona_select()
            except Exception:
                with suppress(Exception):
                    self.action_refresh_context()

        self.app.push_screen(PersonasScreen(self.work_dir), _back)

    def _restore_run_state(self) -> None:
        """Re-enable launch; progress lives in Jobs (``j``), not on this form."""
        if self._restoring_status:
            return
        self._restoring_status = True
        try:
            self._set_launch_enabled(True)
            latest = self.run_manager.latest()
            if latest is not None:
                self._last_run_id = latest.run_id
        finally:
            self._restoring_status = False

    def action_check_docker(self) -> None:
        """Bound to `d` / palette — not a toolbar button."""
        self._do_docker_check()

    @work(thread=True)
    def _do_docker_check(self) -> None:
        available = self.run_manager.orchestrator.check_docker_available()
        msg = (
            t("ui-docker-is-available-and-running")
            if available
            else t("ui-docker-is-not-available-install-docker-or-start")
        )
        from typing import Literal

        sev: Literal["information", "error"] = "information" if available else "error"

        from ..threads import call_ui

        call_ui(self.app, lambda: self.notify(msg, severity=sev))

    @on(Button.Pressed, "#launch-btn")
    def _launch_evaluation(self) -> None:
        self.action_run_evaluation()

    @on(Button.Pressed, "#save-config-btn")
    def _save_config_btn(self) -> None:
        self.action_save_config_only()

    def action_refresh_context(self) -> None:
        """Re-read models/personas and container snapshot (F5)."""
        with suppress(Exception):
            current = selection_list_selected_ids(self.query_one("#models-select", SelectionList))
            self._rebuild_models_selection(current, default_select_all=False)
        with suppress(Exception):
            self._refresh_persona_select()
        with suppress(Exception):
            self._rebuild_run_capability_lists()
        with suppress(Exception):
            self._restore_run_state()
        with suppress(Exception):
            active = self.run_manager.list_active()
            n = len(active)
            if n:
                ids = ", ".join(r.run_id for r in active[-4:])
                more = join_ui(" (+", n - 4, t("ui-more-2")) if n > 4 else ""
                self._set_banner("running", join_ui(n, t("ui-run-s-active-1"), ids, more))
            else:
                self._set_banner("idle", t("ui-no-active-runs-fill-the-form-and-press-launch"))

    def action_run_evaluation(self) -> None:
        self._do_launch()

    def action_export_task_yaml(self) -> None:
        """Export the current runner form as a batch tasks YAML (choose path)."""
        fields = self._read_form(require_models=False)
        if fields is None:
            return
        (
            prompt,
            setup,
            docker_image,
            repo_url,
            repo_branch,
            repo_path,
            models,
            name,
            persona_id,
            _run_mcp,
            _run_mcp_defs,
            run_skills,
            run_plugins,
            run_env,
            _run_inline,
            max_turns,
            yolo,
        ) = fields
        if not (prompt or "").strip():
            self.notify(t("export-task-no-prompt"), severity="error")
            return
        from ...runs.task_export import (
            TaskExportSource,
            default_task_export_path,
            slug_task_id,
            write_task_export,
        )
        from ..path_input_modal import PathInputModal

        tid = slug_task_id(name or prompt[:40] or "exported-task")
        initial = str(default_task_export_path(tid))
        src = TaskExportSource(
            prompt=prompt.strip(),
            task_id=tid,
            description=(name or "").strip(),
            docker_image=docker_image or "fully-loaded",
            repo_url=repo_url or "",
            repo_branch=repo_branch or "",
            repo_path=repo_path or "",
            setup_instructions=setup or "",
            persona_id=persona_id or "",
            models=tuple(models or ()),
            max_turns=max_turns,
            yolo=bool(yolo),
            env=dict(run_env or {}),
            run_plugins=tuple(run_plugins or ()),
            run_skills=tuple(run_skills or ()),
            run_mcp_servers=tuple(_run_mcp or ()),
        )

        def _done(path_raw: str | None) -> None:
            if not path_raw:
                return
            try:
                written = write_task_export(Path(path_raw), src)
            except Exception as exc:
                self.notify(t("export-task-failed", exc=str(exc)), severity="error")
                return
            self.notify(t("export-task-saved", path=str(written)), severity="information")

        self.app.push_screen(
            PathInputModal(
                title=t("export-task-title"),
                initial=initial,
                placeholder=t("export-task-placeholder"),
                hint=t("export-task-hint"),
            ),
            _done,
        )

    def action_save_config_only(self) -> None:
        """Persist the form as a run config without launching Docker."""
        fields = self._read_form(require_models=False)
        if fields is None:
            return
        (
            prompt,
            setup,
            docker_image,
            repo_url,
            repo_branch,
            repo_path,
            models,
            name,
            persona_id,
            run_mcp,
            run_mcp_defs,
            run_skills,
            run_plugins,
            run_env,
            run_inline,
            max_turns,
            yolo,
        ) = fields
        if not prompt:
            self.notify(U.prompt_required_save(), severity="error")
            return
        try:
            from ...runs.run_configs import RunConfigStore, normalize_run_inline_skills

            store = RunConfigStore(self.work_dir)
            # Same normalization as launch autosave (tuples or maps → {id, content}).
            inline_dicts = normalize_run_inline_skills(run_inline)
            if self._config_id:
                existing = store.get(self._config_id)
                if existing:
                    existing.prompt = prompt
                    existing.setup_instructions = setup
                    existing.docker_image = docker_image
                    existing.repo_url = repo_url
                    existing.repo_branch = repo_branch
                    existing.repo_path = repo_path
                    existing.persona_id = persona_id
                    existing.github_write = False
                    existing.run_mcp_servers = list(run_mcp)
                    existing.run_mcp_definitions = list(run_mcp_defs)
                    existing.run_skills = list(run_skills)
                    existing.run_plugins = list(run_plugins)
                    existing.run_env_vars = dict(run_env)
                    existing.run_inline_skills = list(inline_dicts)
                    if models:
                        existing.models = models
                    existing.parallelism = 1
                    existing.max_turns = max_turns
                    existing.yolo = bool(yolo)
                    if name:
                        existing.name = name
                    store.save(existing)
                    extra = ""
                    if run_mcp or run_skills or run_plugins or run_env or run_inline:
                        extra = t(
                            "runner-save-extras-summary",
                            mcp=len(run_mcp),
                            skills=len(run_skills),
                            plugins=len(run_plugins),
                            inline=len(run_inline),
                            env=len(run_env),
                        )
                    self.notify(
                        join_ui(
                            t("ui-updated-config"),
                            existing.config_id,
                            " (",
                            existing.display_name(),
                            ")",
                        )
                        + (join_ui(t("ui-persona-1"), persona_id) if persona_id else "")
                        + extra,
                        severity="information",
                    )
                    self._mark_form_clean()
                    return
            cfg = store.create(
                prompt=prompt,
                setup_instructions=setup,
                docker_image=docker_image,
                repo_url=repo_url,
                repo_branch=repo_branch,
                repo_path=repo_path,
                models=models,
                parallelism=1,
                name=name,
                github_write=False,
                max_turns=max_turns,
                yolo=bool(yolo),
            )
            cfg.persona_id = persona_id or ""
            cfg.run_mcp_servers = list(run_mcp)
            cfg.run_mcp_definitions = list(run_mcp_defs)
            cfg.run_skills = list(run_skills)
            cfg.run_plugins = list(run_plugins)
            cfg.run_env_vars = dict(run_env)
            cfg.run_inline_skills = list(inline_dicts)
            cfg.yolo = bool(yolo)
            store.save(cfg)
            self._config_id = cfg.config_id
            self.notify(
                join_ui(t("ui-saved-config"), cfg.config_id, " → ", store.root),
                severity="information",
                timeout=8,
            )
            self._mark_form_clean()
        except Exception as exc:
            self.notify(join_ui(t("ui-save-failed"), exc), severity="error")

    def _read_form(
        self, *, require_models: bool = True
    ) -> (
        tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            list[str],
            str,
            str,
            list[str],
            list[dict],
            list[str],
            list[str],
            dict[str, str],
            list[tuple[str, str]],
            int,
            bool,
        ]
        | None
    ):
        prompt = self.query_one("#prompt-input", TextArea).text.strip()
        setup = self.query_one("#setup-input", TextArea).text.strip()
        try:
            docker_raw = select_value_str(
                self.query_one("#docker-image-select", Select).value, default=DEFAULT_DOCKER_IMAGE
            )
        except Exception:
            docker_raw = DEFAULT_DOCKER_IMAGE
        docker_image = normalize_docker_choice(docker_raw)
        try:
            from ...docker.base_profiles import resolve_docker_base

            _r = resolve_docker_base(docker_image)
            docker_image = _r.stored
        except Exception:
            logger.debug(t("ui-failed-to-resolve-docker-base-for-s"), docker_image, exc_info=True)
        repo_url = self.query_one("#repo-url-input", Input).value.strip()
        repo_branch = self.query_one("#repo-branch-input", Input).value.strip()
        try:
            repo_path = self.query_one("#repo-path-input", Input).value.strip()
        except Exception:
            repo_path = ""
        try:
            models = selection_list_selected_ids(self.query_one("#models-select", SelectionList))
        except Exception:
            models = []
        _model_skips: list[str] = []
        try:
            from ...runs.batch import validate_models_for_launch

            models, _model_skips = validate_models_for_launch(models)
        except Exception:
            try:
                from ...runs.batch import resolve_model_ids

                models = resolve_model_ids(models)
            except Exception:
                logger.debug(t("ui-failed-to-validate-resolve-models-for-launch"), exc_info=True)
        _seen: set[str] = set()
        _deduped: list[str] = []
        for m in models:
            if m and m not in _seen:
                _seen.add(m)
                _deduped.append(m)
        models = _deduped
        if _model_skips:
            self._pending_model_skips = _model_skips
        try:
            name = self.query_one("#config-name-input", Input).value.strip()
        except Exception:
            name = self._config_name or ""
        persona_id = self._persona_id_from_form()
        self._persona_id = persona_id
        run_mcp = self._run_mcp_from_form()
        run_mcp_defs = self._run_mcp_definitions_from_form()
        run_skills = self._run_skills_from_form()
        run_plugins = self._run_plugins_from_form()
        run_env = self._run_env_from_form()
        run_inline = self._run_inline_skills_from_form()
        try:
            max_turns_raw = self.query_one("#max-turns-input", Input).value.strip()
        except Exception:
            max_turns_raw = str(DEFAULT_MAX_TURNS)
        max_turns = normalize_max_turns(
            max_turns_raw or DEFAULT_MAX_TURNS, default=DEFAULT_MAX_TURNS
        )
        try:
            yolo = bool(self.query_one("#yolo-mode", Checkbox).value)
        except Exception:
            yolo = False
        if require_models and (not models):
            self.notify(U.select_at_least_one_model(), severity="error")
            return None
        return (
            prompt,
            setup,
            docker_image,
            repo_url,
            repo_branch,
            repo_path,
            models,
            name,
            persona_id,
            run_mcp,
            run_mcp_defs,
            run_skills,
            run_plugins,
            run_env,
            run_inline,
            max_turns,
            yolo,
        )

    def _do_launch(self) -> None:
        try:
            if not self.run_manager.orchestrator.check_docker_available():
                self.notify(
                    t("ui-docker-is-not-running-start-the-docker-daemon-an"),
                    severity="error",
                    timeout=10,
                )
                return
        except Exception:
            self.notify(
                t("ui-could-not-reach-docker-is-the-daemon-installed-a"),
                severity="error",
                timeout=10,
            )
            return
        fields = self._read_form(require_models=True)
        if fields is None:
            return
        (
            prompt,
            setup,
            docker_image,
            repo_url,
            repo_branch,
            repo_path,
            models,
            name,
            persona_id,
            run_mcp,
            run_mcp_defs,
            run_skills,
            run_plugins,
            run_env,
            run_inline,
            max_turns,
            yolo,
        ) = fields
        if not prompt:
            self.notify(U.prompt_required(), severity="error")
            return
        github_write = self._persona_github_write()
        github_token = self._persona_github_token()
        skips = self._pending_model_skips or []
        if skips:
            self.notify(
                join_ui(
                    t("ui-skipping"),
                    len(skips),
                    t("ui-inactive-model-s-not-in-grok-models-models-cache"),
                    ", ".join(models) or "(none)",
                ),
                severity="warning",
                timeout=12,
            )
            for msg in skips[:4]:
                self.notify(msg[:200], severity="warning", timeout=10)
            self._pending_model_skips = []
        if not models:
            self.notify(
                t("ui-no-active-models-to-launch-edit-the-models-field"),
                severity="error",
                timeout=12,
            )
            return
        if repo_path and len(models) > 1:
            self.notify(
                t("ui-repo-path-requires-single-model"),
                severity="error",
                timeout=12,
            )
            return
        if github_write and (not repo_url) and (not repo_path):
            self.notify(
                t("ui-github-write-is-on-but-repo-url-is-empty-set-htt"),
                severity="warning",
                timeout=10,
            )
        if github_write and (not github_token):
            try:
                from ...docker.orchestrator import describe_github_write_token_status

                st = describe_github_write_token_status(ui_token="")
                if "no token" in st:
                    self.notify(
                        t("ui-persona-has-github-write-on-but-no-token-set-pat"),
                        severity="warning",
                        timeout=12,
                    )
            except Exception:
                pass
        auth_json = Path.home() / ".grok" / "auth.json"
        grok_config = Path.home() / ".grok" / "config.toml"
        if not auth_json.exists():
            self._set_banner("error", join_ui(t("ui-auth-file-not-found"), auth_json))
            return
        already = self.run_manager.active_count
        interactive = False
        try:
            interactive = bool(self.query_one("#interactive-multi-turn", Checkbox).value)
        except Exception:
            interactive = False
        resume_sid = (self._resume_session_id or "").strip()
        resume_src = (self._resume_source_dir or "").strip()
        if resume_sid or resume_src:
            # Resume launches continue as multi-turn so further follow-ups work.
            interactive = True
        try:
            bg = self.run_manager.start_run(
                prompt=prompt,
                interactive=interactive,
                setup_instructions=setup,
                docker_image=docker_image,
                models=models,
                parallelism=1,
                repo_url=repo_url,
                repo_branch=repo_branch,
                repo_path=repo_path,
                auth_json=auth_json,
                grok_config=grok_config,
                prune_exited=True,
                save_config=True,
                config_name=name,
                existing_config_id=self._config_id,
                persona_id=persona_id,
                github_token="",
                repo_commit=(self._repo_commit or "").strip(),
                restore_code=bool(self._restore_code) or bool(resume_sid or resume_src),
                run_mcp_servers=run_mcp,
                run_mcp_definitions=run_mcp_defs,
                run_skills=run_skills,
                run_plugins=run_plugins,
                run_env_vars=run_env,
                run_inline_skills=run_inline,
                resume_session_id=resume_sid,
                resume_source_dir=resume_src,
                max_turns=max_turns,
                yolo=bool(yolo),
            )
        except RuntimeError as exc:
            self.notify(str(exc), severity="warning")
            return
        try:
            from ...runs.run_configs import RunConfigStore

            for c in RunConfigStore(self.work_dir).list_configs():
                if c.source_run_id == bg.run_id:
                    self._config_id = c.config_id
                    break
        except Exception:
            pass
        self._last_run_id = bg.run_id
        n = len(models)
        rid = (bg.run_id or "")[:12]
        more = join_ui(" (+", already, t("ui-already-running")) if already else ""
        self.notify(
            join_ui(t("ui-launched"), n, t("ui-model-s"), more)
            + (f" · {rid}" if rid else "")
            + t("ui-jobs-for-logs-esc-closes-jobs-run-keeps-going"),
            severity="information",
            timeout=10,
        )
        rm = self.run_manager
        wd = self.work_dir

        def _leave_to_jobs() -> None:
            with suppress(Exception):
                self.app.pop_screen()
            try:
                from .jobs import JobsModal

                self.app.push_screen(JobsModal(rm, work_dir=wd))
            except Exception:
                pass

        self.call_after_refresh(_leave_to_jobs)

    def _apply_finished_banner(self, run: BackgroundRun) -> None:
        elapsed_str = _format_duration(run.elapsed_s)
        if run.error:
            self._set_banner("error", join_ui(t("ui-run-crashed"), rich_escape(run.error)))
            return
        completed = sum(1 for r in run.results if r.status == "completed")
        failed_count = sum(1 for r in run.results if r.status == "failed")
        if failed_count:
            failed_results = [r for r in run.results if r.status == "failed"]
            error_summary = "  |  ".join(
                f"{r.container_name}: {r.error[:120]}" for r in failed_results
            )
            self._set_banner(
                "error",
                join_ui(
                    t("ui-run-2"),
                    run.run_id,
                    t("ui-finished-in-1"),
                    elapsed_str,
                    " — ",
                    completed,
                    t("ui-succeeded"),
                    failed_count,
                    t("ui-failed-2"),
                    rich_escape(error_summary),
                ),
            )
        else:
            self._set_banner(
                "success",
                join_ui(
                    t("ui-run-2"),
                    run.run_id,
                    t("ui-completed-in"),
                    elapsed_str,
                    " — ",
                    completed,
                    len(run.results),
                    t("ui-succeeded-1"),
                ),
            )

    def _set_banner(self, level: str, text: str) -> None:
        """Surface launch/run feedback as a toast (no embedded status panel)."""
        plain = (
            str(text)
            .replace("[/bold]", "")
            .replace("[bold]", "")
            .replace("[/green]", "")
            .replace("[green]", "")
            .replace("[/dim]", "")
            .replace("[dim]", "")
            .replace("[/]", "")
        )
        if level == "error":
            self.notify(plain, severity="error", timeout=8)
        elif level == "running":
            self.notify(plain, severity="information", timeout=5)
        elif level == "success":
            self.notify(plain, severity="information", timeout=5)
        # idle: no toast spam when opening the form

    def _scroll_to_status(self) -> None:
        return

    def _set_launch_enabled(self, enabled: bool) -> None:
        btn = self.query_one("#launch-btn", Button)
        btn.disabled = not enabled

    def _update_status_row(self, status: ContainerStatus) -> None:
        _ = status

    def _upsert_status_row(self, table: DataTable, status: ContainerStatus) -> None:
        _ = table, status

    def _form_snapshot(self) -> tuple[str, ...]:
        """Serializable form fields for dirty detection (no model validation)."""
        with suppress(Exception):
            prompt = self.query_one("#prompt-input", TextArea).text
            setup = self.query_one("#setup-input", TextArea).text
            name = self.query_one("#config-name-input", Input).value
            repo_url = self.query_one("#repo-url-input", Input).value
            repo_branch = self.query_one("#repo-branch-input", Input).value
            try:
                repo_path = self.query_one("#repo-path-input", Input).value
            except Exception:
                repo_path = ""
            try:
                docker = select_value_str(
                    self.query_one("#docker-image-select", Select).value,
                    default=DEFAULT_DOCKER_IMAGE,
                )
            except Exception:
                docker = DEFAULT_DOCKER_IMAGE
            try:
                models = tuple(
                    selection_list_selected_ids(self.query_one("#models-select", SelectionList))
                )
            except Exception:
                models = ()
            try:
                interactive = bool(self.query_one("#interactive-multi-turn", Checkbox).value)
            except Exception:
                interactive = False
            try:
                yolo = bool(self.query_one("#yolo-mode", Checkbox).value)
            except Exception:
                yolo = False
            try:
                max_turns = self.query_one("#max-turns-input", Input).value.strip()
            except Exception:
                max_turns = str(DEFAULT_MAX_TURNS)
            persona = self._persona_id_from_form()
            run_mcp = tuple(self._run_mcp_ids or [])
            run_skills = tuple(self._run_skills_ids or [])
            run_plugins = tuple(self._run_plugins_ids or [])
            run_env = tuple(sorted((self._run_env_vars or {}).items()))
            run_inline = tuple(
                (n, b) for n, b in (self._run_inline_skills or []) if (n or "").strip()
            )
            return tuple(
                str(x)
                for x in (
                    name,
                    prompt,
                    setup,
                    docker,
                    repo_url,
                    repo_branch,
                    repo_path,
                    models,
                    interactive,
                    yolo,
                    max_turns,
                    persona,
                    run_mcp,
                    run_skills,
                    run_plugins,
                    run_env,
                    run_inline,
                )
            )
        return ()

    def _capture_clean_snapshot(self) -> None:
        self._clean_snapshot = self._form_snapshot()

    def _mark_form_clean(self) -> None:
        self._capture_clean_snapshot()

    def form_is_dirty(self) -> bool:
        if self._clean_snapshot is None:
            return False
        return self._form_snapshot() != self._clean_snapshot

    def _finish_leave(self) -> None:
        n = self.run_manager.active_count
        if n:
            self.notify(
                join_ui(n, t("ui-run-s-keep-going-in-docker-j-jobs-logs-quit-anyt")),
                severity="information",
                timeout=6,
            )
        super()._finish_leave()
