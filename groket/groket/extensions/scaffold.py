"""Scaffold user extensions under ``~/.groket/`` (detectors, rules, analysis plugins, tasks)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..paths import (
    app_config_path,
    ensure_user_extension_dirs,
    user_analysis_plugins_dir,
    user_detectors_dir,
    user_rules_dir,
    user_tasks_dir,
)


def slug_name(name: str) -> str:
    """Filesystem- and detector-safe slug (lowercase, limited punctuation)."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (name or "").strip().lower())
    s = s.strip("-.") or "unnamed"
    return s[:48]


def snake_to_pascal(slug: str) -> str:
    """``my_plugin`` → ``MyPlugin`` (for generated class names)."""
    parts = re.split(r"[^a-zA-Z0-9]+", slug)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "MyPlugin"


def write_detector(name: str, *, force: bool = False) -> Path:
    """Create ``~/.groket/detectors/<slug>.py`` with a ``@detector`` stub."""
    ensure_user_extension_dirs()
    slug = slug_name(name)
    path = user_detectors_dir() / f"{slug}.py"
    if path.exists() and not force:
        raise FileExistsError(f"already exists: {path} (use --force to overwrite)")
    detector_name = slug.replace("-", "_")
    path.write_text(
        f'''"""User detector: {detector_name}

Loaded automatically from ~/.groket/detectors/ (no source edits).
Reference this detector name from a rule YAML under ~/.groket/rules/.

Generate a paired rule:
  uv run groket gen rule my-rule --detector {detector_name}
"""

from __future__ import annotations

from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, ToolCall


@detector("{detector_name}")
def detect_{detector_name}(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    """Return zero or more :class:`Match` instances."""
    _ = messages
    matches: list[Match] = []
    # Example: flag every failed tool call (tune via params in your rule YAML).
    only_errors = params.as_bool("only_errors", True)
    for tc in tool_calls:
        if only_errors and not tc.is_error:
            continue
        matches.append(
            Match(
                tool_calls=[tc],
                variables={{
                    "tool": tc.tool_name or "",
                    "snippet": (tc.output or "")[:120],
                }},
            )
        )
        # Demo: at most one hit unless you remove this break.
        break
    return matches
''',
        encoding="utf-8",
    )
    return path


def write_rule(
    rule_id: str,
    *,
    detector: str,
    force: bool = False,
) -> Path:
    """Create ``~/.groket/rules/<slug>.yaml`` referencing *detector*."""
    ensure_user_extension_dirs()
    slug = slug_name(rule_id)
    path = user_rules_dir() / f"{slug}.yaml"
    if path.exists() and not force:
        raise FileExistsError(f"already exists: {path} (use --force to overwrite)")
    det = (detector or slug).replace("-", "_")
    rid = slug.replace("_", "-")
    path.write_text(
        f"""# yaml-language-server: $schema=https://indynull.github.io/groket/schemas/rules.schema.json
# User rule — merged with package / other user rules (same id replaces).
# Detector must be registered (~/.groket/detectors/*.py or plugins).
# Validate: uv run groket rules validate {path.name}

schema_version: 1
rules:
  - id: {rid}
    description: "User rule for {rid}"
    category: Custom
    severity: medium
    enabled: true
    detector: {det}
    params:
      only_errors: true
    summary: "Custom finding on {{tool}}"
    detail: |
      Detector `{det}` reported a match.
      Snippet: {{snippet}}
    recommendation: "Review the flagged tool calls and adjust the agent behaviour."
""",
        encoding="utf-8",
    )
    return path


def write_analysis_plugin(name: str, *, force: bool = False) -> Path:
    """Create ``~/.groket/plugins/<slug>.py`` with an Analyzer class."""
    ensure_user_extension_dirs()
    slug = slug_name(name).replace("-", "_")
    class_name = snake_to_pascal(slug) + "Analyzer"
    path = user_analysis_plugins_dir() / f"{slug}.py"
    if path.exists() and not force:
        raise FileExistsError(f"already exists: {path} (use --force to overwrite)")
    path.write_text(
        f'''"""User analysis plugin: {class_name}

Place this file in ~/.groket/plugins/ (on sys.path for analysis loads).
Enable in ~/.groket/config.json:

  "analysis": {{
    "plugins": ["{slug}:{class_name}"]
  }}

Or: uv run groket gen plugin {slug}  # already wrote the file
"""

from __future__ import annotations

from pathlib import Path
from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import Severity
from groket.parser import load_session_meta, parse_timeline


class {class_name}:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="{slug.replace("_", "-")}",
            name="{class_name}",
            description="User analysis plugin ({slug})",
            optional=True,
        )

    def analyze(self, session_dir: Path, context: AnalyzeContext | None = None) -> AnalysisResult:
        _ = context
        meta = load_session_meta(session_dir)
        timeline = parse_timeline(session_dir)
        findings: list[Finding] = []
        if not timeline:
            findings.append(
                Finding(
                    id="{slug}-empty",
                    plugin_id=self.info.id,
                    title="No timeline events",
                    detail=f"Session {{meta.session_id}} has an empty timeline.",
                    severity=Severity.LOW,
                )
            )
        return AnalysisResult(
            analyzer_id=self.info.id,
            findings=findings,
            summary=f"{{len(findings)}} finding(s)",
            ok=True,
        )
''',
        encoding="utf-8",
    )
    return path


def write_tasks_file(path: Path | None = None, *, force: bool = False) -> Path:
    """Create an empty tasks YAML (for ``groket batch --tasks``)."""
    ensure_user_extension_dirs()
    out = Path(path).expanduser() if path else user_tasks_dir() / "example_tasks.yaml"
    if out.exists() and not force:
        raise FileExistsError(f"already exists: {out} (use --force to overwrite)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        """# yaml-language-server: $schema=https://indynull.github.io/groket/schemas/tasks.schema.json
# Run: groket batch validate <this-file>
#      groket batch run -t <this-file> -m <model-id>

schema_version: 1

tasks:
  - task_id: example-hello
    category: regular
    # repo_url: https://github.com/org/repo.git
    # repo_branch: main
    # Or live host tree (bind-mounted as /workspace; single model only):
    # repo_path: ~/src/my-project
    prompt: >
      Say hello from the workspace and list the top-level files.
    # initial_commands: |
    #   echo ready
""",
        encoding="utf-8",
    )
    return out


def append_analysis_plugin_to_config(module_stem: str, class_name: str) -> Path:
    """Append ``module:Class`` to ``~/.groket/config.json`` analysis.plugins if missing."""
    cfg_path = app_config_path()
    data: dict = {}
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    analysis = data.setdefault("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
        data["analysis"] = analysis
    plugins = analysis.setdefault("plugins", [])
    if not isinstance(plugins, list):
        plugins = []
        analysis["plugins"] = plugins
    entry = f"{module_stem}:{class_name}"
    if entry not in plugins:
        plugins.append(entry)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return cfg_path
