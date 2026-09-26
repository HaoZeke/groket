# AGENTS.md — groket

Groket is a [Textual](https://github.com/Textualize/textual) TUI for evaluating
Grok Build sessions (Python 3.13+). Similar in spirit to
[posting](https://github.com/darrenburns/posting),
[harlequin](https://github.com/tconbeer/harlequin), and
[toolong](https://github.com/Textualize/toolong).

This file is the contract for humans and coding agents working in the repo.
Describe **current** product behaviour only — no migration history or
rejected-design narration.

---

## 1. Quick start

```bash
make install        # .venv (test+dev) + ``groket`` on PATH (uv tool editable)
groket              # interactive TUI (or: uv run groket)
make test           # pytest (default unit suite; no Docker daemon)
make lint           # ruff + mypy + fluent/typing policy scripts
make ci             # lint + schema-check + hud-check + examples-check + test (local; CI splits these)
```

| CLI | Role |
|-----|------|
| ``groket`` / ``groket tui`` / ``groket PATH`` | Interactive TUI (control client) |
| ``groket serve`` | Control owner (foreground; ``-d`` detach); ``stop`` / ``restart`` / ``status`` |
| ``groket hud`` | Desktop palette (iced; control client) |
| ``groket doctor`` | Host checks (Docker, Grok auth, paths) — no TUI |
| ``groket editor …`` | Packaged Emacs / Neovim client paths |
| ``groket gen …`` | Scaffold under ``~/.groket/`` (detector, rule, plugin, tasks) |
| ``groket batch …`` | Headless Docker from task YAML (``examples/tasks/``) |
| ``groket rules …`` | Validate rules / composites YAML |

Prefer **`uv run …`** so tools match the lockfile.

### Dependencies

**Heavy deps are fine** when they improve DX or correctness. Do not add a
second library that duplicates an existing choice.

| Area | Library | Role |
|------|---------|------|
| TUI | **Textual** (+ **Rich**) | Screens, widgets, themes |
| CLI | **Typer** (+ Click, shellingham) | Subcommands, help, completion |
| i18n | **fluent.runtime** | ``locale/<lang>/main.ftl`` |
| Data | **Pydantic v2**, **PyYAML** | Config / models; rule & task YAML |
| Docker | **python-on-whales** | Container orchestration |

---

## 2. Agent / commit hygiene

**Commit each finished unit of work in the same turn.** A unit is one coherent
change the user could revert alone. Verify, then commit before starting the next.

Before **any** agent commit:

1. **`make lint`** (or equivalent ruff/mypy/fluent/typing checks) green.
2. **`uv run pytest tests/ -q`** green (owning subset first is fine, then full).
3. **`git status`** — stage intended files only; no secrets.
4. Commit with a clear imperative message (why, not only what).
5. If GPG signing fails non-interactively:
   ``git -c commit.gpgsign=false commit …`` and note it.

Re-run tests after the final diff for that commit. Prefer
``make ci`` before claiming a larger slice done.

Coverage: ``pyproject.toml`` sets ``fail_under = 100`` when coverage runs
(``make test-cov`` or ``pytest --cov=groket``). Default ``make test`` / CI do
**not** pass ``--cov``. Prefer closing gaps with domain tests or deleting dead
code when you touch a module; do not lower ``fail_under`` or omit package
source to hide debt.

### No speculative fallbacks

**One clear path** per behaviour (one install method, one Docker client, one
config source). No secondary branches “just in case.”

Fallbacks only when platforms truly diverge (e.g. Windows vs POSIX), with a
short positive comment on each path. If a single path is wrong, fix that path.

### Feature delivery (mandatory for product changes)

A “feature” is any operator-visible capability or launch behaviour (keys,
runner options, batch task fields, Docker entrypoint env, analysis surfaces).
**Do not ship half-finished surfaces.** Implement and document the full path
in the same unit of work (or a tight stack of commits), not “code now, docs
later.”

| Surface | When it must be updated |
|---------|-------------------------|
| **Domain / orchestrator** | Shared path under ``runs/``, ``session/``, ``docker/`` — not a TUI-only fork of the logic |
| **TUI** | Bindings, palette, Fluent, ``help.rich.txt``; keyboard path for every new action |
| **Batch / task YAML** | If the feature applies to headless launches: ``task_schema``, ``schemas/tasks.schema.json`` (``make schema``), ``examples/tasks/``, ``batch`` wiring to the **same** domain APIs |
| **README.md** | Operator-facing: keys, CLI flags, task fields, what TUI vs batch can do |
| **AGENTS.md** | Only when the *contract* for agents changes (architecture, gates, layouts) |
| **Tests** | Domain unit tests + TUI Pilot where UI is involved; batch/schema tests when YAML fields change; no live Docker in default suite |
| **examples/** | New task/rule/plugin packs when the feature is meant to be copied; keep ``make examples-check`` green |

**Parity rules**

1. **One implementation, many front doors.** Runner, run configs, and
   ``groket batch`` call the same launch/merge/orchestrator code. Do not
   reimplement resume, caps, or Docker env in a screen only.
2. **If a surface intentionally cannot do X**, say so in **README** (and
   Fluent help if it is a TUI action). Example: TUI **fork** continues an
   *ended* session; batch multi-turn uses scripted ``turns`` on a *new*
   session — different product paths, both documented.
3. **New launch knobs** (env vars, ``ContainerConfig`` fields, entrypoint
   flags) land with: orchestrator + entrypoint (and embedded assets), tests,
   and every caller that should set them (runner, batch, configs).
4. **Operator docs are part of done.** README key tables / CLI sections and
   in-app help must match bindings. Leaving “only Fluent” or “only code”
   incomplete is a process failure.
5. **Schemas and examples stay honest.** Task/rule schema fields without
   examples or validation are incomplete; examples without CI linkage are
   incomplete (``make examples-check``).

**Definition of done (agent checklist)**

- [ ] Domain API used by all launch paths that need the behaviour
- [ ] TUI: binding + palette + Fluent + ``help.rich.txt`` (if user-facing)
- [ ] Batch/schema/examples updated **or** README states TUI-only / batch-only
- [ ] README updated for operators
- [ ] Tests for domain + UI (and batch if applicable)
- [ ] ``make lint`` and ``uv run pytest tests/ -q`` green; prefer ``make ci``
      for multi-surface work

---

## 3. Architecture

Root modules are **foundational**. Domain logic lives in packages.

```
groket/
  cli.py, models.py, parser.py, paths.py, constants.py, utils.py, flags.py
  event_types.py         # event type sets for filters / segmentation
  fs_watch.py            # TraceTreeWatch (live session / trace FS events)
  job_pools.py           # serial analysis + live-refresh worker pools
  session_inflight.py    # per-session inflight locks (analysis, refresh)
  assets_loader.py       # repo assets/ or wheel-embedded templates
  runs/                  # personas, run_configs, run_manager, batch, live_share,
                         #   launch_meta, services, task_schema
  session/               # turns, turn_gate, usage_stats, workspace_diff,
                         #   context_samples, models_catalog, export_bundle,
                         #   sources, catalog (domain session list for control)
  notes.py               # configurable operator notes (TOML schema + session store)
  integrations/          # control Unix JSON-RPC, daemon (``groket serve``),
                         #   ControlClient, emacs/vim packages
  hud/                   # launches iced palette binary
  session/control_views.py  # wire payloads for session/get|timeline|turns|usage
# Sibling: groket-hud/     # iced Sol-style palette (not part of Python package)
  diagnostics/           # host self-test
  analysis/              # Analyzer protocol, service, registry, cache, inflight, llm/
  engine/                # detectors, rules loader, runner, rule_schema
  capabilities/          # MCP / skills / Grok Build marketplace plugins
  docker/                # orchestrator, base_profiles, resources
  extensions/            # groket gen scaffolds
  locale/                # Fluent .ftl + help.rich.txt
  ui/                    # Textual UI
    app.py               # TraceEvalApp — sessions home
    screens/             # browser, runner, jobs, personas, rules, run_configs
    widgets/             # timeline, detail, help_modal, controls, activity_bar, …
    bindings.py, commands.py, i18n.py, text.py, styles.py, prefs.py
    data_table.py, panel_render.py, render_detail.py, forms.py, fuzzy.py
    session_summary.py, session_status.py, tab_panes.py, threads.py
    delete_confirm.py, env_modals.py, confirm_modal.py, quit_actions.py
    app.tcss

assets/                  # non-Python templates (not coverage source)
  docker/                # entrypoint, Dockerfiles, share helpers
  config/                # empty rules.yaml / composites.yaml stubs

examples/                # supported reference packs (CI: make examples-check) — not auto-loaded
schemas/                 # committed JSON Schema (tasks, rules)
Optional wheel mirror: groket/_embedded_assets/
```

**Data flow:** ``parser`` / ``models`` → ``runs`` | ``session`` | ``analysis`` |
``engine`` → ``ui``. Prefer domain modules for parse and Docker orchestration.
UI may schedule **read-only** live reloads (meta / signals / light timeline) on
worker pools; it must not start eval containers from widgets.

**Local control plane:** headless ``groket serve`` is the sole owner of the
per-user Unix socket (JSON-RPC for Emacs/Neovim/HUD/TUI). Lifecycle: bare
``serve`` starts (foreground; ``-d`` detaches); ``serve stop`` /
``restart`` / ``status``. Domain path: ``session/access`` +
``session/catalog`` / ``control_views`` + notes on disk; serve also warms the
catalog, watches the traces tree, runs analysis jobs (``analysis/run`` /
``analysis/status``), and notifies ``session/changed`` /
``session/selected`` / ``notes/changed`` / ``analysis/changed``.
TUI **never owns** the socket: default is detach-start owner if free
(``--no-serve`` skips spawn; ``--no-socket`` runs offline), then attach,
load home catalog via ``session/list``, and listen for notifications.
TUI exit does not stop the owner.
``session/list`` ``query`` is server-side casefold substring; optional
``offset`` pages the filtered rows (omit it for the first page). Optional
``sinceRevision`` is a cheap unchanged/delta poll. Clients that need the full
catalog drain pages until ``matched`` on first paint only. Do not reimplement
catalog discovery for control outside ``session/catalog`` +
``session/access`` + ``integrations.control`` / ``daemon``.

Static Docker/YAML templates load via :mod:`groket.assets_loader`.

### 3.0 Path layout (product contract)

| Root | Default | Holds |
|------|---------|--------|
| **Config home** (`APP_HOME`) | ``~/.groket`` | ``config.json``, ``hud.log``, personas, detectors, rules, analysis plugins, tasks scaffolds, analysis cache, reports, flag fallbacks, notes_schema.toml, notes fallback, optional ``models.yaml`` |
| **Work dir** | ``~/.groket/work`` (CLI path overrides) | ``runs/traces/``, ``runs/run_configs/``, feedback cache, Docker build contexts, batch ``eval_results.json`` |

- TUI **Eval** catalog = ``work/runs/traces`` (sessions this tool launched via
  Docker). Optional **Host** catalog = ``~/.grok/sessions`` (``H`` / pref
  ``show_host_sessions``); real host paths, no import re-home.
- CLI path chooses work root / traces root and, for a work root, where new runs
  go (:func:`groket.paths.resolve_work_and_traces`). ``~/.grok/sessions`` as
  path keeps the default work root for launches.
- Gitignored trees under a checkout (``/runs/``, ``/flags/``, ``/config.json``,
  ``/_meta_cache.json``) are **local leftovers**, not the install layout.

### 3.1 Live sessions (product behaviour)

- **FS watch** (``fs_watch.TraceTreeWatch``) discovers / reloads live sessions;
  fallback timer when inotify is unavailable.
- **60s read-only heartbeat** re-reads ``signals.json`` (context meter) without
  writing the traces tree or meta cache.
- **Single-flight refresh** per session via ``session_inflight.KIND_REFRESH`` +
  the live-refresh pool; coalesced reruns when events stack.
- **Turn status** on the home list: ``running`` | ``awaiting`` | ``ending`` |
  ``complete`` | ``cancelled`` | ``—`` (:meth:`~groket.models.SessionMeta.list_status_label`).
  **ending** = Done (``e``) or last-turn follow-up still finishing.
- **Context** columns / Summary use session snapshot fields from signals;
  optional in-memory per-turn samples while a browser is open
  (``session.context_samples``). Grok does not export a full per-turn series.
- **Subagent** session directories are excluded from the sessions list.

### 3.2 Localization (mandatory for UI copy)

| Source | Role |
|--------|------|
| ``locale/<lang>/main.ftl`` | All operator-facing UI strings |
| ``locale/<lang>/help.rich.txt`` | Long Rich help for ``?`` only |
| ``ui/text.py`` | ``text.foo_bar()`` → Fluent id ``foo-bar``; ``cmd_*`` palette pairs |
| ``ui/i18n.py`` | ``setup_i18n`` / ``t`` / ``ngettext`` / ``join_ui`` |

Default language: ``en``.

### 3.3 Zero hardcoded user-facing UI strings

Under ``groket/ui/``: **no** hardcoded operator-facing English (or other
language) in Python. Add/reuse Fluent ids; call via ``t("…")`` or ``ui.text`` /
``U.*``.

**User-facing** includes notifications, button labels, placeholders, table
headers, select labels, modal titles, activity bar, follow-up / Done prompts,
Footer and palette descriptions.

**Do not** put TCSS/Rich style tokens, widget ids, logger formats, or docstrings
in FTL. Do not use FTL edge spaces for concatenation (Fluent strips them).

### 3.3a Fluent construction gate

``make lint`` → ``scripts/check_fluent.py`` (exit 1 on violations):

- No f-string embedding ``t(...)``.
- No ``re.compile(t(...))`` / regex message ids in FTL.
- No leading/trailing space on single-line FTL values (except multi-line / placeable-only).

Prefer one Fluent message with ``{$placeholders}``, then ``join_ui``, then
Python Rich styles on a full ``t(...)`` result.

---

## 4. Code conventions

### 4.1 Style

- ``snake_case`` / ``PascalCase`` / ``UPPER_SNAKE``.
- ``from __future__ import annotations`` in every module (ruff).
- Annotate public signatures; ``X | None``, lowercase generics.
- **No ``Any`` / ``object`` value bags** for our JSON, tools, UI state, configs.
  Use ``JsonValue`` / ``JsonObject``, ``ParamBag`` / ``ToolInputBag``,
  concrete types, ``Protocol``, ``TypedDict`` + ``Unpack``.
  Gates: ``mypy groket`` + ``scripts/check_typing_policy.py``.
  Forced third-party signatures: one-line library comment (e.g. ``# Textual``).
- Recursive JSON: PEP 695 ``type`` aliases (3.13+). Prefer
  :func:`~groket.models.as_json_object` when building mappings.
- Detectors:
  ``(tool_calls, messages, params: RuleParams) -> list[Match]``.
- Analyzers:
  ``analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult``.
- ``logger = logging.getLogger(__name__)``. ``print`` / ``typer.echo`` only in
  ``cli.py``.
- Init all instance attrs in ``__init__``. Delete dead code.

### 4.2 Comments and prose

Ship the product as it exists. Document invariants, ownership, and non-obvious
why. Omit design process, agent self-talk, and “vs old layout” stories.
Rationale belongs in the **git commit message**.

### 4.2a Sphinx-style docstrings

Public callables: short summary + reST field lists (``:param:``, ``:returns:``,
``:raises:``). Private helpers may be one line.

### 4.2b Makefile

| Target | Action |
|--------|--------|
| ``make install`` | ``uv sync --group test --group dev`` + editable uv tool |
| ``make lint`` | ruff check/format-check + mypy + ``check_fluent`` + ``check_typing_policy`` |
| ``make lint-fix`` | ruff autofix + format + mypy |
| ``make lint-complexity`` | Size-limit report only (not in ``make ci``); see §4.6 |
| ``make test`` | pytest (no coverage flag) |
| ``make test-cov`` | pytest + coverage report (``fail_under`` applies) |
| ``make schema`` | Regenerate ``schemas/*.schema.json`` |
| ``make schema-check`` | Fail if schemas drift |
| ``make examples-check`` | Validate ``examples/`` packs (hard contract) |
| ``make ci`` | Local full gate: ``lint`` + ``schema-check`` + ``hud-check`` + ``examples-check`` + ``test`` |
| ``make hud-themes`` | Regenerate ``groket-hud/assets/textual-themes.json`` |
| ``make hud-check`` | Theme map + rustfmt + clippy ``-D warnings`` + HUD cargo test (+ llvm-cov fail-under when installed). Clippy/test/cov set ``CARGO_INCREMENTAL=0``. A passing ``hud-cov`` deletes ``groket-hud/target/llvm-cov-target``. |

GitHub Actions (``.github/workflows/ci.yml``) runs those as separate jobs: **Lint Python**, **Test Python**, and **HUD** on Linux (full ``make hud-check``), macOS, and Windows (fmt/clippy/test/release build).
| ``make brand`` | Rebuild ``brand/`` (``uv`` ``brand`` group) |
| ``make clean`` | Python caches plus ``cargo clean`` on ``groket-hud`` |

HUD Cargo trees: a passing ``make hud-cov`` deletes ``groket-hud/target/llvm-cov-target``.
``groket hud`` (release) deletes ``target/debug`` and coverage leftovers.
``--dev`` / ``--debug`` keep debug objects. ``make clean`` runs ``cargo clean``.

Published schemas (also under ``schemas/``; GitHub Pages via
``.github/workflows/pages.yml``):

- https://indynull.github.io/groket/schemas/tasks.schema.json  
- https://indynull.github.io/groket/schemas/rules.schema.json  

### 4.3 Module purity

Keep type/model modules limited to types and type-adjacent members. Move
helpers to ``utils``, a focused module, or the caller layer.

| Module | Allowed | Forbidden |
|--------|---------|-----------|
| ``models.py``, ``*/models.py``, ``analysis/base.py`` | Types, enums, aliases, trivial properties | Standalone strip/regex/I/O helpers |
| ``parser.py`` | Parse/load + private parse helpers for this API | UI, Docker orchestration |
| ``paths.py``, ``constants.py`` | Paths / constants | Business logic, widgets |
| ``utils.py`` | Pure cross-cutting helpers | Domain models, ``ui`` imports |
| ``runs/*``, ``session/*`` | Domain for that concern | Textual screens |
| ``ui/*`` | Screens, widgets, presentation | Docker launch; prefer domain for parse |

### 4.4 Imports

Module-level imports at top (stdlib → third party → local) after
``from __future__ import annotations``.

Do not use function-level imports to hide cycles — break cycles with leaf
modules and ``TYPE_CHECKING``. Rare exceptions (CLI defers TUI for light
``--help``; dynamic plugin ``importlib``) need one factual comment.

### 4.5 Error handling

- Narrowest exception that is actually handled.
- Never ``except Exception: pass`` on core success paths.
- TUI handlers may catch broadly with ``logger.exception`` / ``warning`` — do
  not fake a successful empty UI.
- Workers that update UI must surface failure to the operator.

### 4.5a Agent quality checklist

Before claiming work done:

1. **Feature delivery** checklist in §2 (docs, parity, schemas/examples) complete
   for the change — not only the code path you touched first.
2. ``make lint`` (or mypy + fluent + typing policy + ruff) green.
3. ``uv run pytest tests/ -q`` green.
4. UI: no new hardcoded user-facing strings; Fluent + ``t`` / ``U`` / ``join_ui``.
5. Prefer delete/merge duplicates over parallel JSON/UI helpers.

### 4.5b UI and Docker test drivers

- Textual: ``App.run_test()`` + Pilot; wait helpers in
  ``tests/ui/pilot_helpers.py`` (condition-based, not fixed sleeps).
- Docker: fake ``python_on_whales`` at the orchestrator boundary. No live
  daemon in default ``make test``.
- Domain uses ``logging``; only ``cli.py`` may print.

### 4.5c Test quality

Same bar as product code. Domain-shaped names and paths (no ``smoke`` /
``extra_cov`` / ``full`` in file names). Fake only Docker / network /
interactive git. Assert outcomes and what the user reads in the UI.

### 4.6 Size limits (ruff)

| Rule | Limit |
|------|-------|
| PLR0913 | 5 args |
| PLR0911 | 5 returns |
| PLR0912 | 12 branches |
| PLR0915 | 50 statements |
| PLR0904 | 20 public methods / class |

**Not part of default ``make lint`` / CI** (historical debt). Report with
``make lint-complexity``. When you **edit** a function or class that already
exceeds a limit, split or simplify that unit in the same change — no blanket
``noqa``. Do not open a mass split of unrelated large modules. Debt notes:
[TODO.md](TODO.md).

### 4.7 Models

- **Pydantic v2** for serialised models (Flag, EvalRun, …).
- **Dataclasses** for hot-path trace types (TraceEvent, ToolCall, …).
- Model modules are types only (§4.3).

### 4.8 Naming

Domain-shaped, coredis-style: verb+object publics; descriptive privates; no
``_helper`` / ``_v2`` piles. Tests: ``test_<behaviour>`` under the matching
domain folder.

---

## 5. Linting and dead code

| What | How |
|------|-----|
| Unused imports / locals | ruff F401 / F841 (default ``make lint``) |
| Size / complexity | §4.6 table via ``make lint-complexity`` (not CI) |
| ``from __future__ import annotations`` | isort required-imports |
| ``print`` outside CLI | T20 (``cli.py`` only) |

**Not dead without checking call paths:** Textual hooks (``compose``,
``action_*``, ``on_*``, ``BINDINGS``, …); ``@detector`` modules loaded from
``~/.groket/detectors`` and user rules YAML; analysis plugins listed in
``config.json`` ``analysis.plugins``; model fields filled from traces.

---

## 6. TUI and keyboard UX

Keyboard-first. Mouse is optional acceleration. Every feature reachable by
keys and/or **Ctrl+P**.

| File | Role |
|------|------|
| [`ui/bindings.py`](groket/ui/bindings.py) | Bindings |
| [`ui/keys.py`](groket/ui/keys.py) | Display chords (``Ctrl+S``, ``Cmd+Shift+G``) |
| [`ui/commands.py`](groket/ui/commands.py) | Ctrl+P palette |
| Fluent / ``ui/text`` / ``help.rich.txt`` | Labels and help |

No ad-hoc key legends in banners (``"save [ctrl+s]"``). Footer, tips, HUD,
CLI, and README use the same words: ``Ctrl+S``, ``Shift+Tab``, ``Esc`` —
never caret (``^s``) or glyphs (``⌘⇧``).

### 6.1 Focus

| Input | Role |
|-------|------|
| Tab / Shift+Tab | Between widgets |
| Arrows, Home/End, PgUp/PgDn | Inside focused widget |
| Enter / Space | Activate |
| Esc | Back / dismiss |
| Mouse | Optional |

After filling a primary list: ``focus_primary_list``. Use ``check_action`` +
``refresh_bindings`` for selection-gated keys (e.g. Flag).

### 6.2 Two layers of tabs

1. **App panes** — ``[`` / ``]`` and digits ``1``–``N`` (titles include the digit).
2. **In-pane filters** — visible ``Select`` + focus key (e.g. timeline ``v``).

| Layer | Example | Keys |
|-------|---------|------|
| Browser panes | Timeline … Report | ``[`` ``]`` ``1``–``5`` |
| Persona / runner panes | Identity … / Recipe … | ``[`` ``]`` + digits |
| Timeline filter | All / Tools / … | ``v`` → Select |
| Multi-select | Sessions, configs, pickers | ``s`` / ``space`` → green ``*`` col 0 |

### 6.3 Multi-select

``LIST_SELECT`` / ``LIST_SELECT_ALL`` / ``CAPABILITY_PICKER``; marker via
``data_table.selection_mark`` / ``set_selection_marker``.

### 6.3a Destructive delete (``x``)

Double-press ``x`` (and Delete where bound) on sessions, run configs, personas.
First press arms; second with the **same** target set commits. Shared helper:
:func:`groket.ui.delete_confirm.second_press_armed`.

### 6.4 DataTable

``style_data_table``, ``preserving_cursor``, ``cursor_row_key``,
``set_selection_marker`` / ``update_row_cell`` — do not reimplement.

### 6.5 Guidance chrome (do not mash into one callout)

| Role | Widget / place | Notes |
|------|----------------|--------|
| Empty pane | :class:`~groket.ui.panel_render.EmptyState` | Dim one-line, no border; only when section empty |
| Keys / how-to | Footer, ``?`` help, Ctrl+P | Not permanent in-pane cyan boxes |
| Rare interrupt | :class:`~groket.ui.panel_render.TipSurface` | Framed callout; ``show_tips`` toggles these only |

Never use ``TipSurface`` for empty states or always-on shortcut lessons.
``show_tips`` in config / Analysis / Ctrl+P hides TipSurface only.

### 6.5a Extractable / copyable body content (mandatory)

Operators must be able to copy text from any **body** surface that is useful
to paste elsewhere (detail, summary, diff, findings header, report sections,
similar future panes). OS drag-to-copy does not work while Textual owns the
mouse — the product path is Textual selection + OSC 52 yank.

| Use | Widget |
|-----|--------|
| Body content a human may extract | :class:`~groket.ui.selectable_static.SelectableStatic` |
| Chrome only (labels, filter bar, empty-state) | plain ``Static`` / :class:`~groket.ui.panel_render.EmptyState` |
| Rare framed callout | :class:`~groket.ui.panel_render.TipSurface` |

**Rules**

1. **New extractable bodies** mount :class:`~groket.ui.selectable_static.SelectableStatic`
   (not plain ``Static``). Display the real Rich renderable via ``update()``;
   keep plain cache for selection/yank — do **not** pre-bake fixed-width
   ``Text`` for display (that truncates / mis-wraps).
2. **``y`` / Ctrl+Shift+C** (browser ``action_copy_detail``) order:
   mouse selection → **Findings tab selected finding** (prefer MF
   **Issue box** text What/Where/Why/Should/Pattern when ``Finding.extras``
   has those fields; else export-style markdown) → focused
   ``SelectableStatic`` → whole active pane (all visible Report sub-panes
   joined). Report mounts **one extractable pane per logical unit**:
   overview, flags, notes, plugin header/findings, each markdown ``##``
   chunk, and MF **Form fields** / **Issue box** fence bodies (paste-ready).
   Tab focuses a pane; ``y`` yanks that pane only. Findings-tab ``y`` is
   still the one-key Issue-box path without opening Report.
3. **Live refresh** must not clear a widget that has an active text
   selection (``_widget_has_text_selection`` / ``set_static_renderable``).
4. **Tests** for new extractable surfaces: plain-text cache + yank path
   (see ``tests/ui/test_selectable_static.py``).
5. **Docs**: operator keys in README + ``help.rich.txt`` when adding a
   major extractable surface; Fluent notify ids
   (``ui-copied-selection`` / ``ui-copied-detail`` / ``ui-copied-report`` /
   ``ui-copied-content``).

Helper: :func:`~groket.ui.selectable_static.is_extractable_static`.

### 6.6 Context-sensitive shortcuts

Stable globals: ``?``, ``F5``/``Ctrl+R``, ``j``, ``Esc``, ``Ctrl+P``, ``q``
(any screen; inputs still receive ``q`` while editing). Screen owns the rest.

### 6.7 Discovery

1. Footer (few primary keys)  
2. ``?`` help  
3. Ctrl+P palette  

Add a key: ``bindings.py`` → ``action_*`` → palette if useful → help if major.

### 6.8 Keyboard checklist

Primary list focus; pane digits; visible filters; ``s``/``space`` multi-select;
preserving cursor; ``TipSurface``; ``check_action``; Tab-reachable buttons;
modals Esc + Ctrl+S save; no mouse-only features; extractable bodies use
``SelectableStatic`` + ``y`` (§6.5a).

### 6.9 Global key reference

| Key | Action |
|-----|--------|
| ``?`` | Help |
| ``F5`` / ``Ctrl+R`` | Refresh |
| ``j`` | Jobs / logs |
| ``Esc`` | Back / dismiss |
| ``q`` | Quit |
| ``Ctrl+P`` | Command palette |
| ``Ctrl+S`` | Save / Done (forms, multi-pickers) |
| ``[`` / ``]`` | Previous / next pane |
| ``1``…``N`` | Jump to pane N |
| ``s`` / ``space`` | Select (multi-select lists) |

Session browser also: ``y`` / ``Ctrl+Shift+C`` copy selection or pane body
(§6.5a); ``n``/``e`` follow-up/Done when awaiting; ``x`` delete (double-press);
``f`` flag; ``N``/``O`` notes; ``E`` export; ``H`` show/hide host sessions
(sessions home).

Sessions home also: ``n``/``e`` follow-up/Done when awaiting; ``x`` delete
(double-press); ``a`` analyze; ``d`` rules; ``r``/``C``/``P`` runner/configs/personas;
``H`` show or hide native ``~/.grok/sessions`` next to Docker work traces.

---

## 7. Styling

Prefer Textual design tokens (``$primary``, ``$surface``, ``$text``, …).

| Layer | File |
|-------|------|
| Layout / focus | ``app.tcss`` |
| Semantic Rich colours | ``ui/styles.py`` (status, severity, timeline) |
| Callouts | ``TipSurface`` / ``.tip-surface`` |

UI chrome via ``panel_render`` / panel-card; Markdown **content** only through
``md_content()`` / ``content_block()``.

---

## 8. Filter bars

Exclusive filters: ``Horizontal`` + ``FILTER_BAR_CLASS`` + bold label +
**``Select``** (+ optional search ``Input``). Constants in
``widgets/controls.py``. No button chips for exclusive mode.

---

## 9. Browser Report tab

One scroll of inline ``panel-card`` sections; **Filter** ``Select`` toggles
``display`` only (not nested source tabs).

---

## 10. Plugins and capabilities

Extend without editing package source: ``~/.groket/`` + ``groket gen …``.

| Path | Purpose |
|------|---------|
| ``~/.groket/detectors/*.py`` | ``@detector`` modules |
| ``~/.groket/rules/*.yaml`` | Rule YAML (same schema as ``assets/config`` stubs / published schema) |
| ``~/.groket/plugins/*.py`` | Analysis ``Analyzer`` classes (+ optional detectors) |
| ``~/.groket/tasks/*.yaml`` | Optional task lists (never auto-loaded) |
| ``~/.groket/config.json`` | Prefs + ``analysis.plugins`` |

```bash
uv run groket gen detector my_check
uv run groket gen rule my-rule --detector my_check
uv run groket gen plugin my_stats --register
uv run groket gen tasks
uv run groket rules validate
```

Package ``assets/config/rules.yaml`` and ``composites.yaml`` are **empty stubs**.
Copy packs from ``examples/detection/`` (``minimal/``, ``starters/``,
``catalog/``) into ``~/.groket`` to enable. Findings type:
:class:`~groket.analysis.base.Finding`.

**``examples/`` is a hard contract** (``make examples-check`` / CI): rule and
task YAML schemas, detector registration vs rule ``detector:`` fields, analysis
plugin import/instantiate, sample configs, personas, pack READMEs. Prefer those
packs as the implementation reference when adding detectors, plugins, or tasks.

### Three “plugin” concepts

| Kind | Config | Notes |
|------|--------|--------|
| Analysis plugins | ``analysis.plugins`` | ``module:Class``; ``~/.groket/plugins/`` |
| Detectors + rules | ``@detector`` + YAML | Engine findings; user detectors/rules |
| Grok Build plugins | persona / run ``plugins`` | Marketplace names → ``plugins-manifest.json`` at launch |

**MCP** and **skills** are separate persona fields. MCP may create a hidden
companion skill (``use-<server>-mcp``, ``x-groket: groket-mcp-companion``).

---

## 11. Testing

Domain-shaped layout, behavioural names, fakes only at **system boundaries**.

### 11.1 Layout

```
tests/
  conftest.py
  test_models.py, test_parser.py, test_paths.py, test_flags.py, test_utils.py
  test_event_types.py, test_fs_watch.py, test_job_pools.py, test_session_inflight.py
  test_assets_loader.py
  analysis/  capabilities/  cli/  diagnostics/  docker/  engine/
  runs/  session/  ui/  fixtures/
```

Isolate ``APP_HOME`` in tests so developer ``~/.groket`` never leaks in.

### 11.2 Mock boundaries only

- Fake Docker / python-on-whales, network, interactive git, wall-clock when needed.
- Do **not** mock internal ``groket`` modules against each other for coverage.
- Default suite: **no** live Docker daemon or network ``git clone``.

### 11.3 Style

Parametrize variants; async TUI with ``run_test()``; assert outcomes and
user-visible text; small focused tests.

### 11.4 Coverage

When measuring (``make test-cov`` / ``--cov=groket``), ``fail_under = 100``
applies. Meet it with real domain tests; delete dead code rather than
pragma/omit. Default CI/``make test`` do not fail on coverage percentage.

### 11.5 New test checklist

1. Domain path and behavioural name?  
2. External I/O faked at the boundary?  
3. One conceptual failure reason?  
4. No Docker daemon / no network?  
5. Asserts real outcomes (not pause-and-pass)?  
