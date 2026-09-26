# Examples

**Supported reference packs** — CI and `make examples-check` refuse to break
them. Copy into `~/.groket/` or pass paths explicitly. Nothing under
`examples/` is auto-loaded by the product.

| Pack | What it teaches | Install / use |
|------|-----------------|---------------|
| [`detection/`](detection/) | Detectors (`@detector`) + rule YAML | `~/.groket/detectors/` + `~/.groket/rules/` |
| [`analysis/`](analysis/) | Analysis `Analyzer` plugins + sample configs | `~/.groket/plugins/` + `config.json` |
| [`tasks/`](tasks/) | Batch task catalogs | `groket batch -t <file>` |
| [`personas/`](personas/) | Persona JSON (e.g. marketplace plugins) | `~/.groket/personas/` |
| [`notes/`](notes/) | Operator notes schema TOML (field list) | `~/.groket/notes_schema.toml` |

## Start here

| Goal | Open / run |
|------|------------|
| Smallest detector + rule | [`detection/minimal/`](detection/minimal/) |
| Full detector catalog | [`detection/catalog/`](detection/catalog/) |
| Minimal analysis plugin | [`analysis/plugins/session_event_count.py`](analysis/plugins/session_event_count.py) |
| LLM review plugin | [`analysis/plugins/llm_instruction_check.py`](analysis/plugins/llm_instruction_check.py) |
| Batch tasks | [`tasks/demo_tasks.yaml`](tasks/demo_tasks.yaml) |

```bash
# Detection (minimal)
mkdir -p ~/.groket/detectors ~/.groket/rules
cp examples/detection/minimal/detectors/*.py ~/.groket/detectors/
cp examples/detection/minimal/rules/*.yaml ~/.groket/rules/

# Analysis (one plugin)
mkdir -p ~/.groket/plugins
cp examples/analysis/plugins/session_event_count.py ~/.groket/plugins/
# enable: analysis.plugins = ["session_event_count:SessionEventCountAnalyzer"]

# Or open TUI with a sample config (plugins dir on path — no copy):
uv run groket --config examples/analysis/configs/all-plugins.json

# Batch
uv run groket batch validate examples/tasks/demo_tasks.yaml
uv run groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
```

Scaffold instead of copying:

```bash
uv run groket gen detector my_check
uv run groket gen rule my-rule --detector my_check
uv run groket gen plugin my_stats --register
uv run groket gen tasks
```

## Contract

```bash
make examples-check   # or: uv run python scripts/check_examples.py
```

Validates: rule/task YAML schemas, detector registration vs rule `detector:`
fields, analysis plugin import/instantiate, sample config plugin entries,
persona JSON, pack READMEs. Part of `make ci`.
