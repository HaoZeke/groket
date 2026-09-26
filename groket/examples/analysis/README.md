# Analysis plugin examples

Each `plugins/*.py` defines an analysis class (`module_stem:ClassName` in
config). Directory on `sys.path` → module stem = filename without `.py`.

| File | Config entry | Notes |
|------|----------------|-------|
| `session_event_count.py` | `session_event_count:SessionEventCountAnalyzer` | Smallest pure example |
| `token_tracker.py` | `token_tracker:TokenTrackerAnalyzer` | |
| `security_scanner.py` | `security_scanner:SecurityScannerAnalyzer` | |
| `latency_profiler.py` | `latency_profiler:LatencyProfilerAnalyzer` | |
| `diff_reviewer.py` | `diff_reviewer:DiffReviewerAnalyzer` | |
| `conversation_flow.py` | `conversation_flow:ConversationFlowAnalyzer` | |
| `session_health.py` | `session_health:SessionHealthAnalyzer` | |
| `llm_instruction_check.py` | `llm_instruction_check:InstructionCheckAnalyzer` | Minimal LLM review |
| `gte_feedback_grok.py` | `gte_feedback_grok:FeedbackReportAnalyzer` | Full multi-turn LLM review |
| `teachx_v2_mf.py` | `teachx_v2_mf:TeachxV2MfAnalyzer` | TeachX V2 MF-shaped LLM review (What/Where/Why/Should/Pattern rubric; no Slack submit) |

## Implement a plugin

1. Subclass / implement `Analyzer` (`analyze` + `info`).
2. For structured LLM reviews, subclass `LlmReviewAnalyzer` and implement
   `build_instructions(pack)` only (see `llm_instruction_check.py`). The host
   attaches operator turns, timeline digest, and runtime fairness; your method
   returns the rubric string (use `pack` fields such as `pack.turn_count` for
   dynamic text).
3. Register via `analysis.plugins` in `~/.groket/config.json`.

## Install

```bash
mkdir -p ~/.groket/plugins
cp examples/analysis/plugins/session_event_count.py ~/.groket/plugins/
```

```json
{
  "analysis": {
    "plugins": ["session_event_count:SessionEventCountAnalyzer"]
  }
}
```

Or:

```bash
uv run groket gen plugin session_event_count --register
```

## Sample configs (no copy)

`configs/` point at `plugins/` via the config search path:

```bash
uv run groket --config examples/analysis/configs/all-plugins.json
uv run groket --config examples/analysis/configs/security-only.json
```

Then re-analyze (`a` on the sessions list). Contract: `make examples-check`.

## TeachX V2 Model Feedback handoff

The `teachx_v2_mf` plugin drafts MF-oriented findings (What / Where / Why / Should have / Pattern).
It does **not** post to Slack. Operators paste into the Model Feedback form, or use a separate Grok `/mf` skill for form layout.

Program policy (categories, model priority, etc.) lives in the TeachX instructions canvas / local kit mirror — not in this repo.
