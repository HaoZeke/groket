# Task catalogs (batch)

YAML catalogs for headless Docker evals. Optional power path — the TUI runner
covers interactive launches.

## Schema

- Published: https://indynull.github.io/groket/schemas/tasks.schema.json  
- In-repo: `schemas/tasks.schema.json` (`make schema` / `groket batch schema`)

## Files

| File | Purpose |
|------|---------|
| [`demo_tasks.yaml`](demo_tasks.yaml) | Small smoke tasks (empty workspace; optional `repo_url` / `repo_path`) |
| [`superpowers_tasks.yaml`](superpowers_tasks.yaml) | Marketplace **superpowers** plugin exercises |

`repo_path` bind-mounts a host directory as `/workspace` (live edits; single
model). Prefer `repo_url` when you want an isolated clone under `runs/checkouts/`.
Tasks that set `models:` use that list even when batch is run without `-m`.

### Superpowers-only persona

```bash
mkdir -p ~/.groket/personas
cp examples/personas/superpowers.json ~/.groket/personas/
```

## Commands

```bash
uv run groket batch validate examples/tasks/demo_tasks.yaml
uv run groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
uv run groket batch run -t examples/tasks/demo_tasks.yaml -i demo-list-workspace -p 2

uv run groket batch validate examples/tasks/superpowers_tasks.yaml
uv run groket batch run -t examples/tasks/superpowers_tasks.yaml -m <model-id>
```

Scaffold:

```bash
uv run groket gen tasks   # → ~/.groket/tasks/example_tasks.yaml
```

Work root defaults to `~/.groket/work` (`-P` to override). Contract:
`make examples-check`.
