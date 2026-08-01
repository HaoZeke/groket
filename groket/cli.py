"""CLI entry point for groket — Typer (Click) app.

Default command launches the interactive TUI. Optional ``-P`` / ``--path`` (or a
leading path argument) selects the work root, traces directory, or session to
open; the default is ``~/.groket/work``.

``groket gen …`` scaffolds user extensions under ``~/.groket/``.

Shell completion::

    uv run groket --install-completion
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from .ui.i18n import setup_i18n

setup_i18n()

app = typer.Typer(
    name="groket",
    help=(
        "Grok session trace evaluation — interactive TUI by default.\n\n"
        "Open a work root, traces directory, or session with "
        "[cyan]-P PATH[/cyan] or [cyan]groket PATH[/cyan] "
        "(default work root: [cyan]~/.groket/work[/cyan]). "
        "Scaffold extensions with [cyan]groket gen[/cyan]. "
        "Headless Docker catalogs: [cyan]groket batch[/cyan]. "
        "Host checks: [cyan]groket self-test[/cyan]."
    ),
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

gen_app = typer.Typer(
    name="gen",
    help=(
        "Generate user extensions under ~/.groket/ "
        "(detectors, rules, analysis plugins, example tasks). "
        "Also accepts the name [bold]generator[/bold] "
        "(e.g. [cyan]groket generator detector …[/cyan])."
    ),
    no_args_is_help=True,
)
app.add_typer(gen_app, name="gen")

batch_app = typer.Typer(
    name="batch",
    help=(
        "Run task YAML catalogs through Docker (headless). "
        "See [cyan]examples/tasks/[/cyan] and "
        "[cyan]https://indynull.github.io/groket/schemas/tasks.schema.json[/cyan]."
    ),
    no_args_is_help=True,
)
app.add_typer(batch_app, name="batch")

rules_app = typer.Typer(
    name="rules",
    help=(
        "Validate detection rules / composites YAML "
        "([cyan]~/.groket/rules[/cyan], example packs). "
        "Schema: [cyan]https://indynull.github.io/groket/schemas/rules.schema.json[/cyan]."
    ),
    no_args_is_help=True,
)
app.add_typer(rules_app, name="rules")

# Subcommand names — must not be consumed as a TUI path positional.
TOOL_COMMANDS = frozenset({"gen", "generator", "self-test", "batch", "rules", "emacs-path"})
COMMAND_ALIASES = {"generator": "gen"}


def launch_tui(
    path: Path | None,
    config: Path | None,
    *,
    control_socket: Path | bool | None = None,
    prompt_index: int | None = None,
) -> None:
    """Start the TUI for *path* (work root, traces dir, or session) or the default work root."""
    from .integrations.control import default_socket_path
    from .paths import resolve_work_and_traces
    from .ui.app import TraceEvalApp

    cfg = config.expanduser() if config is not None else None
    wd, tr = resolve_work_and_traces(path)
    session: Path | None = None
    if path is not None:
        candidate = Path(path).expanduser()
        markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl", "summary.json")
        if candidate.is_dir() and any((candidate / marker).is_file() for marker in markers):
            session = candidate.resolve()
    socket_path = (
        None
        if control_socket is False
        else Path(control_socket).expanduser()
        if isinstance(control_socket, Path)
        else default_socket_path()
    )
    typer.echo(f"groket: work_dir={wd}", err=True)
    typer.echo(f"  traces: {tr}", err=True)
    typer.echo(f"  runner writes: {wd / 'runs' / 'traces'}", err=True)
    TraceEvalApp(
        traces_path=tr,
        work_dir=wd,
        config_path=cfg,
        control_socket=socket_path,
        initial_session=session,
        initial_prompt_index=prompt_index,
    ).run()


@app.command("emacs-path")
def emacs_path() -> None:
    """Print the installed groket.el path."""
    typer.echo(Path(__file__).parent / "integrations" / "emacs" / "groket.el")


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help=(
                "Work root, runs/traces, or a session directory "
                "(or pass as the first argument). Default: ~/.groket/work."
            ),
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "-c",
            "--config",
            help="Path to config.json (default: ~/.groket/config.json).",
            show_default=False,
        ),
    ] = None,
    control_socket: Annotated[
        Path | None,
        typer.Option(
            "--control-socket",
            help="Unix socket used by local editor integrations.",
            show_default=False,
        ),
    ] = None,
    control: Annotated[
        bool,
        typer.Option(
            "--control/--no-control",
            help="Enable the local editor control socket.",
        ),
    ] = True,
    prompt_index: Annotated[
        int | None,
        typer.Option(
            "--prompt-index",
            help="Prompt index selected when PATH is a session directory.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Start the TUI when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    launch_tui(
        path=path,
        config=config,
        control_socket=control_socket if control else False,
        prompt_index=prompt_index,
    )


@batch_app.command("run")
def cmd_batch_run(
    tasks: Annotated[
        Path,
        typer.Option(
            "-t",
            "--tasks",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Tasks YAML file (schema: schemas/tasks.schema.json).",
        ),
    ],
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root for traces and Docker builds (default ~/.groket/work).",
            show_default=False,
        ),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "-C",
            "--category",
            help="Only tasks with this category field.",
        ),
    ] = None,
    task_id: Annotated[
        list[str] | None,
        typer.Option("-i", "--task-id", help="Only these task ids (repeatable)."),
    ] = None,
    models: Annotated[
        list[str] | None,
        typer.Option(
            "-m",
            "--models",
            help=(
                "Model ids (default: host Grok models catalog). "
                "Tasks that set models: in YAML use that list instead."
            ),
        ),
    ] = None,
    parallelism: Annotated[
        int,
        typer.Option("-p", "--parallelism", min=1, help="Concurrent (task, model) jobs."),
    ] = 1,
) -> None:
    """Validate tasks YAML and run each task × model through Docker."""
    from .paths import resolve_work_and_traces
    from .runs.batch import load_models, load_tasks, run_batch
    from .runs.task_schema import load_task_file

    try:
        load_task_file(tasks)  # fail fast with Pydantic errors before Docker
        loaded = load_tasks(tasks, category)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except (ValueError, Exception) as exc:
        # Pydantic ValidationError subclasses Exception
        typer.echo(f"error: invalid tasks file: {exc}", err=True)
        raise typer.Exit(2) from exc

    if task_id:
        wanted = set(task_id)
        loaded = [t for t in loaded if t.task_id in wanted]
    if not loaded:
        typer.echo("No tasks matched filters.", err=True)
        raise typer.Exit(0)

    wd, _tr = resolve_work_and_traces(path)
    typer.echo(f"batch: work_dir={wd}", err=True)
    batch_models = models or load_models()
    typer.echo(
        f"  tasks={len(loaded)}  batch_models={batch_models} "
        f"(per-task models: in YAML override when set)",
        err=True,
    )
    results = run_batch(
        loaded,
        work_dir=wd,
        models=batch_models,
        parallelism=parallelism,
    )
    failed = sum(1 for r in results if (r.get("status") or "") != "completed")
    raise typer.Exit(1 if failed else 0)


@batch_app.command("validate")
def cmd_batch_validate(
    tasks: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Tasks YAML file to validate.",
        ),
    ],
) -> None:
    """Validate a tasks YAML file against the Pydantic / JSON Schema model."""
    from .runs.task_schema import load_task_file

    try:
        doc = load_task_file(tasks)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        f"OK  {tasks}  ({len(doc.resolved_tasks())} task(s), schema_version={doc.schema_version})"
    )


@batch_app.command("schema")
def cmd_batch_schema(
    out: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="Write JSON Schema to this path (default: stdout).",
        ),
    ] = None,
) -> None:
    """Emit JSON Schema for tasks YAML (same as ``make schema`` / Pages publish)."""
    from .runs.task_schema import emit_tasks_schema

    text = emit_tasks_schema(out)
    if out is None:
        typer.echo(text, nl=False)
    else:
        typer.echo(f"Wrote {out}")


@rules_app.command("validate")
def cmd_rules_validate(
    rules: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Rules / composites YAML file to validate.",
        ),
    ],
) -> None:
    """Validate a rules YAML file against the Pydantic / JSON Schema model."""
    from .engine.rule_schema import load_rules_file

    try:
        doc = load_rules_file(rules)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        f"OK  {rules}  ({len(doc.rules)} rule(s), {len(doc.composites)} composite(s), "
        f"schema_version={doc.schema_version})"
    )


@rules_app.command("schema")
def cmd_rules_schema(
    out: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="Write JSON Schema to this path (default: stdout).",
        ),
    ] = None,
) -> None:
    """Emit JSON Schema for rules YAML (same as ``make schema`` / Pages publish)."""
    from .engine.rule_schema import emit_rules_schema

    text = emit_rules_schema(out)
    if out is None:
        typer.echo(text, nl=False)
    else:
        typer.echo(f"Wrote {out}")


@app.command("self-test")
def cmd_self_test(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root to probe (default ~/.groket/work).",
            show_default=False,
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of text lines."),
    ] = False,
) -> None:
    """Check Docker, Grok auth, work dir, and related host deps (no TUI)."""
    from .diagnostics import run_self_test
    from .paths import resolve_work_and_traces

    wd, _tr = resolve_work_and_traces(path)
    report = run_self_test(work_dir=wd)
    if json_out:
        payload = {
            "ok": report.ok,
            "fail_count": report.fail_count,
            "warn_count": report.warn_count,
            "checks": [
                {
                    "id": c.id,
                    "name": c.name,
                    "ok": c.ok,
                    "required": c.required,
                    "detail": c.detail,
                    "level": c.level,
                }
                for c in report.checks
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        for line in report.lines():
            typer.echo(line)
    raise typer.Exit(0 if report.ok else 1)


@gen_app.command("detector")
def gen_detector(
    name: Annotated[str, typer.Argument(help="Detector name / file stem.")],
    force: Annotated[
        bool,
        typer.Option("-f", "--force", help="Overwrite if exists."),
    ] = False,
) -> None:
    """Create ~/.groket/detectors/<name>.py with @detector stub."""
    from .extensions.scaffold import slug_name, write_detector
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        path = write_detector(name, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote detector module: {path}")
    typer.echo(f"  @detector name: {slug_name(name).replace('-', '_')}")
    typer.echo("  Pair with: uv run groket gen rule <id> --detector <name>")


@gen_app.command("rule")
def gen_rule(
    rule_id: Annotated[str, typer.Argument(help="Rule id (e.g. my-custom-rule).")],
    detector: Annotated[
        str,
        typer.Option(
            "-d",
            "--detector",
            help="Detector name (default: from rule id).",
        ),
    ] = "",
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create ~/.groket/rules/<id>.yaml merged with bundled rules."""
    from .extensions.scaffold import slug_name, write_rule
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    det = detector or slug_name(rule_id).replace("-", "_")
    try:
        path = write_rule(rule_id, detector=det, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote rule YAML: {path}")
    typer.echo(f"  detector: {det}")


@gen_app.command("plugin")
def gen_plugin(
    name: Annotated[str, typer.Argument(help="Module stem (e.g. my_session_stats).")],
    register: Annotated[
        bool,
        typer.Option(
            "-r",
            "--register",
            help="Append module:ClassName to ~/.groket/config.json analysis.plugins.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create ~/.groket/plugins/<name>.py analysis Analyzer class."""
    from .extensions.scaffold import (
        append_analysis_plugin_to_config,
        slug_name,
        snake_to_pascal,
        write_analysis_plugin,
    )
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        path = write_analysis_plugin(name, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    stem = slug_name(name).replace("-", "_")
    cls = snake_to_pascal(stem) + "Analyzer"
    typer.echo(f"Wrote analysis plugin: {path}")
    typer.echo(f"  config entry: {stem}:{cls}")
    if register:
        cfg = append_analysis_plugin_to_config(stem, cls)
        typer.echo(f"  updated {cfg}")
    else:
        typer.echo(f'  enable with analysis.plugins: ["{stem}:{cls}"] or pass --register')


@gen_app.command("tasks")
def gen_tasks(
    path: Annotated[
        Path | None,
        typer.Argument(help="Output path (default: ~/.groket/tasks/example_tasks.yaml)."),
    ] = None,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Write an example tasks YAML under ``~/.groket/tasks/`` (or *path*)."""
    from .extensions.scaffold import write_tasks_file
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        out = write_tasks_file(path, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote tasks file: {out}")


def main(argv: list[str] | None = None) -> None:
    """Console script entry (``groket = groket.cli:main``)."""
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in COMMAND_ALIASES:
        args = [COMMAND_ALIASES[args[0]], *args[1:]]

    if args and not args[0].startswith("-") and args[0] not in TOOL_COMMANDS:
        args = ["-P", args[0], *args[1:]]

    app(args=args, prog_name="groket")


if __name__ == "__main__":
    main()
