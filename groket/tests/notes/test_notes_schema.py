"""Operator notes schema load/defaults."""

from __future__ import annotations

from pathlib import Path

from groket.notes import (
    PICK_MANY,
    PICK_ONE_OF,
    decode_many_choices,
    default_schema,
    encode_many_choices,
    load_schema,
    normalize_pick,
    parse_choices,
    schema_from_mapping,
)


def test_default_schema_has_generic_fields() -> None:
    s = default_schema()
    assert s.schema_id == "default"
    assert [f.id for f in s.fields] == ["summary", "detail"]
    assert all(f.label == "" for f in s.fields)
    assert all(not f.constrained for f in s.fields)


def test_load_schema_missing_uses_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groket.notes.app_home", lambda: tmp_path)
    s = load_schema()
    assert s.schema_id == "default"
    assert [f.id for f in s.fields] == ["summary", "detail"]


def test_load_schema_custom_and_sanitizes(tmp_path: Path) -> None:
    path = tmp_path / "notes_schema.toml"
    path.write_text(
        """
schema_id = "custom"

[[fields]]
id = "what"
label = "What happened"

[[fields]]
id = "bad.id"
label = "skip me"

[[fields]]
id = "what"
label = "duplicate skipped"

[[fields]]
id = "severity"
label = "Severity"
""",
        encoding="utf-8",
    )
    s = load_schema(path=path)
    assert s.schema_id == "custom"
    assert [f.id for f in s.fields] == ["what", "severity"]
    assert s.fields[0].label == "What happened"


def test_schema_from_mapping_empty_fields_uses_default() -> None:
    s = schema_from_mapping({"schema_id": "x", "fields": []})
    assert [f.id for f in s.fields] == ["summary", "detail"]


def test_schema_label_defaults_to_field_id() -> None:
    s = schema_from_mapping(
        {
            "schema_id": "c",
            "fields": [{"id": "severity"}],
        }
    )
    assert s.fields[0].label == "severity"


def test_schema_choices_one_of_and_many() -> None:
    s = schema_from_mapping(
        {
            "schema_id": "rubric",
            "fields": [
                {"id": "summary", "label": "Summary"},
                {
                    "id": "severity",
                    "label": "Severity",
                    "choices": ["low", "medium", "high", "medium"],
                    "pick": "one-of",
                },
                {
                    "id": "tags",
                    "label": "Tags",
                    "choices": ["ux", "security"],
                    "pick": "many",
                },
                {
                    "id": "status",
                    "label": "Status",
                    "choices": ["open", "closed"],
                    # pick omitted → one-of
                },
                {
                    "id": "junk",
                    "label": "Junk",
                    "choices": ["a"],
                    "pick": "not-a-real-pick",
                },
            ],
        }
    )
    by_id = {f.id: f for f in s.fields}
    assert not by_id["summary"].constrained
    sev = by_id["severity"]
    assert sev.choices == ("low", "medium", "high")
    assert sev.pick == PICK_ONE_OF
    assert not sev.pick_many
    tags = by_id["tags"]
    assert tags.pick == PICK_MANY
    assert tags.pick_many
    assert by_id["status"].pick == PICK_ONE_OF
    assert by_id["junk"].pick == PICK_ONE_OF


def test_schema_empty_choices_is_free_text() -> None:
    s = schema_from_mapping(
        {
            "schema_id": "x",
            "fields": [
                {"id": "notes", "label": "Notes", "choices": [], "pick": "many"},
            ],
        }
    )
    f = s.fields[0]
    assert f.choices == ()
    assert not f.constrained
    assert not f.pick_many


def test_parse_choices_and_pick_helpers() -> None:
    assert parse_choices(None) == ()
    assert parse_choices([" a ", "b", "a", ""]) == ("a", "b")
    assert normalize_pick("MANY") == PICK_MANY
    assert normalize_pick("") == PICK_ONE_OF


def test_encode_decode_many_choices() -> None:
    choices = ("regression", "ux", "tooling")
    assert encode_many_choices(["tooling", "regression", "tooling"], choices) == (
        "regression\ntooling"
    )
    assert encode_many_choices([], choices) == ""
    assert decode_many_choices("ux\nsecurity\nux") == ["ux", "security"]
    assert decode_many_choices("") == []
    # Extras not in schema keep order after schema tokens.
    assert encode_many_choices(["custom", "ux"], choices) == "ux\ncustom"


def test_load_schema_example_pack() -> None:
    from pathlib import Path as P

    root = P(__file__).resolve().parents[2]
    path = root / "examples" / "notes" / "notes_schema.example.toml"
    s = load_schema(path=path)
    assert s.schema_id == "default"
    by_id = {f.id: f for f in s.fields}
    assert "summary" in by_id and not by_id["summary"].constrained
    assert by_id["severity"].choices == ("low", "medium", "high")
    assert by_id["severity"].pick == PICK_ONE_OF
    assert by_id["tags"].pick_many
