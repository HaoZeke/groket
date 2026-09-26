"""Rules / composites YAML schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from groket.engine.rule_schema import (
    RulesFile,
    emit_rules_schema,
    load_rules_file,
    normalize_rules_raw,
    parse_composite_entry,
    parse_rule_entry,
    rules_json_schema,
    validate_rules_path,
)


def test_load_mapping_document(tmp_path: Path) -> None:
    p = tmp_path / "r.yaml"
    p.write_text(
        yaml.dump(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "r1",
                        "detector": "count_matching_calls",
                        "severity": "high",
                        "params": {"min_count": 2},
                        "summary": "hit {n}",
                    }
                ],
                "composites": [
                    {
                        "id": "c1",
                        "child_rules": ["r1"],
                        "relationship": "co_occurrence",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    doc = load_rules_file(p)
    assert doc.schema_version == 1
    assert len(doc.rules) == 1
    assert doc.rules[0].id == "r1"
    assert doc.rules[0].params["min_count"] == 2
    assert len(doc.composites) == 1
    cfg = doc.rules[0].to_rule_config()
    assert cfg.rule_id == "r1"
    assert cfg.detector_name == "count_matching_calls"
    assert cfg.summary_template == "hit {n}"


def test_load_bare_list(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text(
        yaml.dump([{"id": "only", "detector": "x", "enabled": True}]),
        encoding="utf-8",
    )
    doc = validate_rules_path(p)
    assert len(doc.rules) == 1
    assert not doc.composites


def test_empty_document_rejected(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("schema_version: 1\nrules: []\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_rules_file(p)


def test_invalid_severity(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        yaml.dump({"rules": [{"id": "x", "severity": "critical"}]}),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_rules_file(p)


def test_normalize_rejects_scalar() -> None:
    with pytest.raises(ValueError, match="mapping or a list"):
        normalize_rules_raw("nope")


def test_parse_rule_and_composite_entries() -> None:
    rc = parse_rule_entry({"id": "a", "detector": "d", "enabled": 1})
    assert rc.enabled is True
    cc = parse_composite_entry({"id": "c", "child_rules": ["a"], "min_repeat": 4})
    assert cc.min_repeat == 4
    with pytest.raises(TypeError):
        parse_rule_entry("x")
    with pytest.raises(TypeError):
        parse_composite_entry([])


def test_emit_schema(tmp_path: Path) -> None:
    out = tmp_path / "rules.schema.json"
    text = emit_rules_schema(out)
    assert out.is_file()
    assert '"$id"' in text
    schema = rules_json_schema()
    assert schema.get("title") == "groket-rules"
    assert schema.get("$id", "").endswith("rules.schema.json")


def test_catalog_examples_validate() -> None:
    root = Path(__file__).resolve().parents[2]
    rules = root / "examples" / "detection" / "catalog" / "rules" / "rules.yaml"
    comps = root / "examples" / "detection" / "catalog" / "rules" / "composites.yaml"
    if rules.is_file():
        doc = load_rules_file(rules)
        assert doc.rules
    if comps.is_file():
        doc = load_rules_file(comps)
        assert doc.composites


def test_rules_file_model_direct() -> None:
    doc = RulesFile.model_validate({"rules": [{"id": "z", "detector": "d"}], "composites": []})
    assert doc.rules[0].id == "z"
