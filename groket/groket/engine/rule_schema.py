"""Pydantic models and JSON Schema for detection rules / composites YAML.

Authoring format (``~/.groket/rules/*.yaml``, example packs under
``examples/detection/``)::

    schema_version: 1
    rules:
      - id: my-rule
        detector: my_detector
        ...
    composites:
      - id: my-composite
        child_rules: [my-rule]
        ...

A bare YAML list of rule mappings is also accepted (treated as ``rules:`` only).

Published schema: ``https://indynull.github.io/groket/schemas/rules.schema.json``
(also ``schemas/rules.schema.json`` in the repo).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from groket.models import JsonObject, JsonValue, Severity

SCHEMA_VERSION = 1
SCHEMA_TITLE = "groket-rules"
SCHEMA_ID = "https://indynull.github.io/groket/schemas/rules.schema.json"

SeverityName = Literal["high", "medium", "low"]


class RuleDefinition(BaseModel):
    """One detection rule under ``rules:`` (or a bare list root)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    description: str = ""
    category: str = "Uncategorized"
    severity: SeverityName = "medium"
    enabled: bool = True
    detector: str = ""
    params: dict[str, JsonValue] = Field(default_factory=dict)
    summary: str = ""
    detail: str = ""
    recommendation: str = ""

    @field_validator("params", mode="before")
    @classmethod
    def _params_mapping(cls, v: object) -> dict[str, JsonValue]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise TypeError("params must be a mapping")
        return {str(k): val for k, val in v.items()}

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        return bool(v)

    def to_rule_config(self):  # -> RuleConfig (loader)
        """Convert to the engine runtime :class:`~groket.engine.loader.RuleConfig`."""
        from .loader import RuleConfig

        return RuleConfig(
            rule_id=self.id,
            description=self.description,
            category=self.category or "Uncategorized",
            severity=Severity(self.severity),
            enabled=self.enabled,
            detector_name=self.detector,
            params=dict(self.params),
            summary_template=self.summary,
            detail_template=self.detail,
            recommendation=self.recommendation,
        )


class CompositeDefinition(BaseModel):
    """One composite under ``composites:``."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    name: str = ""
    severity: SeverityName = "medium"
    child_rules: list[str] = Field(default_factory=list)
    relationship: str = "co_occurrence"
    min_repeat: int = 3
    max_gap: int = 5
    root_cause: str = ""
    should_have: str = ""

    @field_validator("child_rules", mode="before")
    @classmethod
    def _child_rules_list(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise TypeError("child_rules must be a list of rule ids")
        return [str(x) for x in v]

    @field_validator("min_repeat", "max_gap", mode="before")
    @classmethod
    def _int_fields(cls, v: object) -> int:
        if isinstance(v, bool):
            raise TypeError("expected int")
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            return int(v)
        raise TypeError("expected int")

    def to_composite_config(self):  # -> CompositeConfig (loader)
        """Convert to the engine runtime :class:`~groket.engine.loader.CompositeConfig`."""
        from .loader import CompositeConfig

        return CompositeConfig(
            composite_id=self.id,
            name=self.name,
            severity=Severity(self.severity),
            child_rules=list(self.child_rules),
            relationship=self.relationship or "co_occurrence",
            min_repeat=self.min_repeat,
            max_gap=self.max_gap,
            root_cause=self.root_cause,
            should_have=self.should_have,
        )


class RulesFile(BaseModel):
    """Root document for a rules YAML file (rules and/or composites)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    rules: list[RuleDefinition] = Field(default_factory=list)
    composites: list[CompositeDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _need_entries(self) -> Self:
        if not self.rules and not self.composites:
            raise ValueError("rules file must contain a non-empty 'rules' and/or 'composites' list")
        return self


def normalize_rules_raw(raw: object) -> dict[str, JsonValue]:
    """Normalize YAML root (mapping or bare rule list) to a RulesFile dict."""
    if raw is None:
        return {"schema_version": SCHEMA_VERSION, "rules": [], "composites": []}
    if isinstance(raw, list):
        return {"schema_version": SCHEMA_VERSION, "rules": raw, "composites": []}
    if isinstance(raw, dict):
        return raw
    raise ValueError("rules file root must be a mapping or a list of rules")


def load_rules_file(path: Path) -> RulesFile:
    """Parse and validate a rules/composites YAML file."""
    rules_path = Path(path).expanduser()
    if not rules_path.is_file():
        raise FileNotFoundError(f"rules file not found: {rules_path}")
    with rules_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return RulesFile.model_validate(normalize_rules_raw(raw))


def validate_rules_path(path: Path) -> RulesFile:
    """Validate *path*; raise ``ValueError`` / ``FileNotFoundError`` on failure."""
    return load_rules_file(path)


def rules_json_schema() -> JsonObject:
    """JSON Schema for RulesFile (draft 2020-12 via Pydantic)."""
    schema = RulesFile.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = SCHEMA_TITLE
    return schema


def emit_rules_schema(out: Path | None = None) -> str:
    """Serialize rules JSON Schema; optionally write *out*. Returns the JSON text."""
    text = json.dumps(rules_json_schema(), indent=2) + "\n"
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return text


def parse_rule_entry(entry: object):
    """Validate one rule mapping and return a runtime :class:`~groket.engine.loader.RuleConfig`."""
    if not isinstance(entry, dict):
        raise TypeError("rule entry must be a mapping")
    return RuleDefinition.model_validate(entry).to_rule_config()


def parse_composite_entry(entry: object):
    """Validate one composite mapping and return a runtime :class:`~groket.engine.loader.CompositeConfig`."""
    if not isinstance(entry, dict):
        raise TypeError("composite entry must be a mapping")
    return CompositeDefinition.model_validate(entry).to_composite_config()
