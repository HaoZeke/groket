# Detection examples

Engine reference: Python detectors (`@detector`) + YAML rules. Install under
`~/.groket/detectors/` and `~/.groket/rules/`.

| Pack | Use when |
|------|----------|
| [`minimal/`](minimal/) | Onboarding — one detector, one rule |
| [`starters/`](starters/) | Small worked examples (e.g. repeated `cd`) |
| [`catalog/`](catalog/) | Full reference rule set |

Each pack layout:

```text
<pack>/
  detectors/*.py    # @detector("name") — sibling imports OK (e.g. patterns.py)
  rules/*.yaml      # rules: / composites:
```

## Install

```bash
mkdir -p ~/.groket/detectors ~/.groket/rules
cp examples/detection/minimal/detectors/*.py ~/.groket/detectors/
cp examples/detection/minimal/rules/*.yaml ~/.groket/rules/
# same pattern for starters/ or catalog/
```

Copy **all** `detectors/*.py` from a pack together (catalog shares helpers such
as `patterns.py`).

## Concepts

| Term | Meaning |
|------|---------|
| Detector | `(tool_calls, messages, params) -> list[Match]` |
| Rule | YAML binding: detector id, severity, title templates |
| Finding | Runner output shown in Findings / Report |
| Composite | Optional YAML grouping of child findings |

Validate a pack’s YAML:

```bash
uv run groket rules validate examples/detection/minimal/rules/demo_rule.yaml
make examples-check
```

Schema: https://indynull.github.io/groket/schemas/rules.schema.json
