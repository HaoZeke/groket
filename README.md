# groket

**groket** (grok evaluation tool) is a [Textual](https://github.com/Textualize/textual)
TUI for evaluating [Grok Build](https://docs.x.ai/build/overview) agent sessions:
timeline, findings, workspace diffs, Docker launches, personas, and pluggable
detectors / analysis plugins.

It is **not** the Grok coding agent. That CLI is `grok`
([install](https://docs.x.ai/build/overview),
[headless](https://docs.x.ai/build/cli/headless-scripting#cli)).
Groket runs alongside it: open traces, score runs, and drive Docker-based evals.

## Install

### Development (clone)

```bash
make install    # .venv + `groket` on PATH via uv tool (editable)
groket          # interactive TUI
```

### Users (uv tool)

```bash
uv tool install git+https://github.com/indynull/groket
groket
uv tool upgrade groket   # later
```

### Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.groket` | `config.json`, personas, detectors, rules, analysis plugins, prefs |
| Work root | `~/.groket/work` | `runs/traces/`, recipes, Docker build contexts, batch results |

```bash
groket                      # default work root
groket /path/to/work        # work root, traces tree, or a single session dir
```

## Using the TUI

### Sessions home

Browse past and **live** runs (filesystem watch + periodic read-only refresh).

| Key | Action |
|-----|--------|
| `Enter` | Open session browser |
| `/` | Search sessions |
| `s` / `Space` | Select row (multi-select); `S` select all / clear |
| `r` | New run (runner) |
| `R` | Re-run recipe from the highlighted session (fresh session, same launch fields) |
| `f` | **Fork** an *ended* session into a new interactive multi-turn (see below) |
| `C` | Run configs (recipes) |
| `P` | Personas |
| `d` | Rules (enable/disable detectors) |
| `a` | Analyze selection |
| `H` | **Show host** / **Hide host** sessions (`~/.grok/sessions`; footer label flips) |
| `E` | Export session bundle (tarball) |
| `n` | Follow-up prompt (awaiting sessions; optional **last turn** in the modal) |
| `e` | End session (Done) while awaiting — list shows **ending** until shutdown finishes |
| `x` / `Delete` | Delete session(s) — press twice to confirm |
| `j` | Jobs / logs |
| `F5` / `Ctrl+R` | Refresh |
| `?` | Help |
| `Ctrl+P` | Command palette (theme, tips, analysis, …) |
| `q` | Quit |

Columns include **Src** (`Eval` = sessions groket launched under
`work/runs/traces`, `Host` = native Grok under `~/.grok/sessions`), turn status
(`running` / `awaiting` / `ending` / `complete`), and **context** fill from
`signals.json` when present. Subagent session dirs are hidden so each eval is
one row. Prefs live in `~/.groket/config.json` (`show_host_sessions` for the
host catalog).

### Session browser

Panes (`[` / `]` or digits **1–5**):

1. **Timeline** — events + detail; `v` filter, `f` flag, `N` new operator note, `O` / palette edit or delete note, `/` search  
2. **Summary** — overview, usage, turns (context samples when live)  
3. **Diff** — workspace changes  
4. **Findings** — detector / analyzer hits (`i` jumps here)  
5. **Report** — analysis panels, flags, and operator notes  

**Copy from extractable panes:** the TUI owns the mouse, so OS highlight-to-copy
does not work. Body text uses ``SelectableStatic`` (detail, summary, diff,
findings header, Report sub-panes). Drag to highlight, then **`y`** /
**Ctrl+Shift+C** / **Ctrl+C**. Tab focuses a body pane for full-pane yank.

- **One Model Feedback issue:** Findings tab (`4` / `i`) → highlight the row
  → **`y`** copies the **Issue (copy into the Issue box)** text
  (`What` / `Where` / `Why` / `Should have` / `Pattern`) when the analyzer
  filled those fields (e.g. `mf_form_feedback`). On **Report**, the same
  Issue fence (and Form fields) are separate Tab-focusable panes under the
  plugin card — focus the pane, then **`y`**. Palette “export finding”
  still writes a full markdown file under `~/.groket/reports`.
- **No selection:** focused body, else the whole active pane (all visible
  Report sub-panes, Summary, Diff, or Timeline detail).

OSC 52 clipboard. tmux over SSH: `set -g set-clipboard on` so OSC 52 reaches
the local pasteboard.

Multi-turn live bar: follow-up input, optional **Last turn**, `n` focus, `e` Done.
`x` deletes (double-press). `Esc` back to the list.

**Eval vs host sessions:** Docker launches always land under
``work/runs/traces`` (**Eval** in the list). Press **`H`** (footer **Show host**
/ **Hide host**) to also list native sessions from ``~/.grok/sessions`` at
their real paths — no copy into the work tree. ``groket -P ~/.grok/sessions``
browses that tree while keeping the default work root for new runs. Operator
notes on host sessions write under ``~/.groket/notes/<session_id>/`` so the
live Grok tree stays clean.

### Emacs Org buffers

The installed distribution includes an Emacs package for viewing the active
session and editing operator notes in an Org buffer. Load the packaged file:

```elisp
(load (car (process-lines "groket" "emacs-path")))
```

Use `M-x groket-open-session` with a session directory or an id from the
running catalog. When Groket is not running, a session directory starts the
TUI in a terminal buffer. The TUI owns a private per-user Unix socket at
`$XDG_RUNTIME_DIR/groket/control.sock`; set `groket-control-socket` when a
different location is required.

The Org projection keeps generated trace content read-only and leaves only
operator-note field bodies editable. `operator_notes.toml` remains canonical;
every mutation includes the revision used to render the buffer, so concurrent
edits fail instead of overwriting a newer note.

| Key | Action |
|-----|--------|
| `g` | Refresh the session projection |
| `C-c C-o` | Select the prompt at point in the TUI |
| `C-c C-c` | Save the note at point |
| `C-x C-s` | Save all notes in the buffer |
| `C-c C-n` | Create a note under the prompt at point |
| `C-c C-k` | Delete the note at point |

The TUI and Org buffer exchange prompt selection, trace-change, and note-change
notifications over the same socket. `groket --no-control` disables the socket;
`groket --control-socket PATH` selects another path.

**Export as task** (`T` on Runner or Recipes): write a batch tasks YAML
(prompt, repo/local path, persona, models, max_turns, yolo, …). A modal asks
for the file path (default `~/.groket/tasks/<task_id>.yaml`). Run extras
(plugins/skills/MCP) are noted in comments — put those on the persona for
batch, or re-apply as runner extras.

**Export** (`E` in the browser or on the sessions list): builds a session
bundle under `~/.groket/reports/` using an **export profile** (default
``archive-full``). Profiles control packaging, which content units to
include, and (later) the human renderer id.

**Config**

- Default profile: ``export.default_profile`` in ``~/.groket/config.json``
  (built-ins: ``archive-full``, ``archive-org``, ``trace-only``).
- User profiles: ``~/.groket/export_profiles/*.yaml`` (same id overrides a
  built-in). Fields: ``id``, ``name``, ``packaging`` (`tar.gz` | `dir`),
  ``include`` (unit list), ``renderer``, ``renderer_options``.

**Built-in units** (`include`): ``grok_trace``, ``run``, ``summary``,
``analysis``, ``analysis_reports``, ``flags``, ``notes``, ``readme``,
``manifest``.

**Built-in renderers** (`renderer`): ``markdown`` (default for
``archive-full``), ``org`` (Org mode human files; use profile
``archive-org``), ``plain`` (``.txt``). Human files: ``human/summary.*``
and analysis reports next to cache JSON as ``*.md`` / ``*.org`` / ``*.txt``.

**Always written for `archive-full` when selected and data exists**

- **`grok-trace.tar.gz`** — nested archive that is **only** the output of
  ``grok trace --local $session_id`` (exact CLI bytes; no groket repack).
  Requires ``grok`` on ``PATH``. Grok session files only
  (``export_metadata.json``, `events.jsonl`, `chat_history.jsonl`, …). Does
  **not** contain groket flags, notes, analysis cache, or eval `run/` extras.
- **`manifest.json`** — inventory (profile, packaging, include, members)
- **`README.txt`** — short layout notes
- **`human/summary.*`** — session overview (title, model, outcome, counts,
  Grok session summary text, usage) in the profile renderer dialect
- **`run/`** — launch recipe / prompt / config / turn gate under a work volume
- **`analysis/`** — analysis cache (``*.json``); ``analysis_reports`` adds
  a human report per analyzer (plugin ``artifacts["report"]`` or findings)
- **`flags.json`** — operator flags (session or `~/.groket/flags/…`; outer only)
- **`notes/`** — `operator_notes.toml` from the notes store. Schema is
  `~/.groket/notes_schema.toml` (not bundled). Author with the TUI (`N` / `O`)
  or the Emacs Org buffer. Host notes live under `~/.groket/notes/`.

``trace-only`` is nest + readme + manifest. Host sessions often have no
``run/``. Packaging ``dir`` writes a folder instead of a tarball.

**TUI:** **`E`** exports with ``export.default_profile`` when that is set in
``~/.groket/config.json``; if unset, **`E` asks once** (profile picker) and
saves the choice as the default. Command palette **Export with profile…**
picks a profile for that export only (does not change the default).

### Multi-turn and forking

| Path | When | What happens |
|------|------|----------------|
| **Live follow-up** (`n` / browser bar) | Session container still running and awaiting | Same Grok session; host writes the next prompt on the turn gate |
| **End** (`e`) | Awaiting | Mark done; status may show **ending** until the container finishes |
| **Fork** (`f` / palette **Fork session**) | Session has *ended* (chat/events present) | New Docker launch: parent history seeded under `.groket-resume-seed/`; first turn is `grok --resume --fork-session` (new session id). Type the continuation prompt; multi-turn on. The browser **timeline inherits parent turns** (Grok often writes only the new turn into the child dir). **Workspace** usually lives under `runs/checkouts/<container>/` (git clone or CoW from parent) and is bind-mounted as `/workspace`. If the recipe has **`repo_path`**, fork remounts that same host directory (live tree; no CoW). On **reflink** filesystems (btrfs, xfs) managed-checkout forks use CoW (`cp --reflink=always`). On **ext4** (no reflink) groket does **not** silently full-copy multi‑GB trees: it falls back to a fresh git clone / empty workspace (parent dirt not preserved). Set `GROKET_ALLOW_FULL_WORKSPACE_COPY=1` to force a full recursive copy when you need dirt on non-reflink volumes. |
| **Re-run** (`R`) | Any listed session | New launch from the same recipe fields — **not** a conversation continue |

While a session is still live, use follow-up (`n`), not fork. Each fork is an
independent branch of the parent snapshot (parent files are not overwritten).

**Fork is a new container**, not the same disk layout as the parent eval:

- **Persona MCP / plugins / skills** are re-applied from the runner prefill.
  The launch recipe (`run.json`) is written on the **traces volume at container
  start** (not only when the container exits), so interactive / still-running
  sessions and re-forks keep `persona_id`, plugins, skills, and MCP. Prefill
  also falls back to the **fork parent seed** recipe when the child dir has
  none. Plugins are **staged on the host** into `*.stage/plugins/checkouts/`
  and installed in-container with `grok plugin install --trust` only (no git
  inside the image). Plugin skill packs are also staged under
  `~/.grok/skills/<id>/` so skill reads stay stable when Grok’s install dir is
  a new `src-<hash>/`.
- Parent chat that hardcodes `/root/.grok/installed-plugins/<old-id>/…` is
  bridged by recreating those directory names as symlinks after install
  (`RESUME_PLUGIN_DIR_ALIASES`).
- **Share** is a new `grok share <child-id>` for the forked session (parent
  share JSON is not copied into the seed). The TUI shows a share as ready only
  when the last write has a URL and an empty error field.

**Batch / task YAML:**

| Field | Role |
|-------|------|
| `repo_url` / `repo_branch` | Git clone into a managed checkout under `runs/checkouts/` |
| `repo_path:` | Host directory bind-mounted as `/workspace` (live tree; **no** clone/CoW). Single model only. Agent edits that directory; root-owned new files are reclaimed for the host user on exit |
| `yolo:` | When true, launch with `grok --yolo` (default false → `--always-approve`) |
| `turns:` | Scripted follow-ups after the primary `prompt` on a **new** session |
| `resume_session_dir:` | Host path to an *ended* session dir — **fork** (same as TUI `f`): seed history, first turn `grok --resume --fork-session`; `prompt` is the continuation; optional `turns` after that |
| `resume_session_id:` | Optional parent id (defaults to the directory basename) |
| `max_turns:` | Grok `--max-turns` per prompt (default **50**); also allowed under `defaults:` |

Example: see commented `demo-fork-resume` / `repo_path` notes in [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml).

### Runner

Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** / **Ctrl+J** launch, **Ctrl+S** save, `[` / `]` panes.
`Esc` back (discard prompt if dirty).

**Workspace:** optional git **Repository** URL (cloned into `runs/checkouts/`),
or **Local path** (bind-mount that host directory as `/workspace` — edits are
permanent; one model only). Managed checkouts are chowned to the host user on
exit; **local path** only reclaims **root-owned** paths the agent created (so
your existing tree is not fully re-chowned).

**Permissions:** by default the agent runs with ``--always-approve``. Check
**YOLO mode** on the runner (or set task YAML ``yolo: true``) to use
``grok --yolo`` instead (same auto-approve family; sets `yolo` in container
config / session telemetry).

Pick reasoning effort with the model token (`model:effort`, e.g. `…:xhigh` or
`…:max`) when selecting models for a run.

**Max turns** (Runtime pane, default **50**) sets Grok `--max-turns`: how many
agent tool/plan steps are allowed **per prompt** (not host multi-turn count).
Batch task YAML may set `max_turns:` (or document-level `defaults.max_turns`).

Runtime shows **persona + effective** plugins / skills / MCP / inline skills /
env (persona base merged with this-run extras). Extras pane still has the full
breakdown.

### Personas & configs

**P** / **C** — tabbed editors (`[` / `]` + digits), **Ctrl+S** save, **Esc** cancel.
Env key/value editing and Grok marketplace plugins/skills/MCP are supported.

## CLI (non-TUI)

| Command | Purpose |
|---------|---------|
| `groket self-test` | Host checks (Docker, Grok auth, paths) — no TUI |
| `groket gen …` | Scaffold detectors, rules, analysis plugins, tasks under `~/.groket/` |
| `groket batch …` | Headless Docker runs from a task YAML catalog |
| `groket rules …` | Validate rules / composites YAML |
```bash
groket self-test
groket gen detector my_check
groket rules validate examples/detection/minimal/rules/demo_rule.yaml
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
# Browse host Grok sessions (default work root still used for Docker launches):
groket -P ~/.grok/sessions
```

Schemas (editors / Pages):

- Tasks: https://indynull.github.io/groket/schemas/tasks.schema.json  
- Rules: https://indynull.github.io/groket/schemas/rules.schema.json  

Prefer the TUI runner for interactive work.

## Examples (reference packs)

Supported, CI-gated packs under [`examples/`](examples/README.md) — copy into
`~/.groket/` or pass paths. **Not** auto-loaded.

| Goal | Start here |
|------|------------|
| Smallest detector + rule | [`examples/detection/minimal/`](examples/detection/minimal/) |
| Full detector catalog | [`examples/detection/catalog/`](examples/detection/catalog/) |
| Analysis plugin | [`examples/analysis/plugins/session_event_count.py`](examples/analysis/plugins/session_event_count.py) |
| Batch tasks | [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml) |

```bash
make examples-check   # hard gate: schemas, imports, rule↔detector links
```

## Development

```bash
make install
make lint            # ruff, mypy, fluent + typing policy scripts
make test            # pytest (daemon-free)
make examples-check  # examples/ contract
make test-cov        # pytest + coverage report
make ci              # lint + schema-check + examples-check + test
```
