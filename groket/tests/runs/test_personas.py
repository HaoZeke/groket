"""PersonaStore unit tests."""

from __future__ import annotations

from pathlib import Path

from groket.runs.personas import Persona, PersonaStore, personas_dir


def test_personas_dir(tmp_path: Path):
    d = personas_dir(tmp_path)
    assert tmp_path in d.parents or d.parent == tmp_path or d.is_absolute()


def test_store_save_list_get_delete(tmp_path: Path):
    store = PersonaStore(tmp_path)
    store.ensure_defaults()
    # API may be list() or iterate via dir — try common names
    rows = store.list()
    assert isinstance(rows, list)
    p = Persona(persona_id="t-unit", name="Unit", description="d")
    store.save(p)
    got = store.get("t-unit")
    assert got is not None
    assert got.name == "Unit"
    assert store.delete("t-unit") is True
    assert store.get("t-unit") is None


def test_store_delete_missing(tmp_path: Path):
    store = PersonaStore(tmp_path)
    assert store.delete("nope") is False


import json

import pytest
from groket.runs.personas import _slug


def test_slug_and_persona_defaults():
    assert _slug("Hello World!!") == "hello-world"
    assert _slug("") == "persona"
    p = Persona(persona_id="", name="")
    assert p.persona_id
    assert p.name == p.persona_id
    assert p.created_at
    d = p.to_dict()
    assert "persona_id" in d


def test_from_dict_and_apply_env(monkeypatch: pytest.MonkeyPatch):
    data = {
        "persona_id": "p1",
        "name": "P",
        "env_vars": "bad",
        "mcp_servers": ["a", "a", ""],
        "mcp_definitions": [{"id": "srv"}, "skip", {"id": ""}],
        "skills": "not-list",
        "github_token": "",
        "github_token_env": "MY_TOKEN",
        "git_user_name": "U",
        "git_user_email": "u@e.com",
        "github_write": True,
    }
    p = Persona.from_dict(data)
    assert p.mcp_servers == ["a"]
    assert len(p.mcp_definitions) == 1
    assert p.skills == []
    env = p.apply_to_env({"X": "1"})
    assert env["X"] == "1"
    assert env["GIT_AUTHOR_NAME"] == "U"
    assert env["GIT_AUTHOR_EMAIL"] == "u@e.com"
    assert p.merge_github_write(True) is True
    monkeypatch.setenv("MY_TOKEN", "secret")
    assert p.resolve_github_token() == "secret"
    p.github_token = "direct"
    assert p.resolve_github_token() == "direct"
    p2 = Persona(persona_id="x", github_token="", github_token_env="")
    assert p2.resolve_github_token() == ""


def test_store_defaults_and_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from groket import paths

    home = tmp_path / "home"
    home.mkdir()
    personas = home / "personas"
    personas.mkdir()
    monkeypatch.setattr(paths, "APP_HOME", home)
    monkeypatch.setattr(paths, "personas_home", lambda: personas)

    store = PersonaStore(tmp_path / "work")
    store.ensure_defaults()
    assert len(store.list()) >= 2
    before = len(store.list())
    store.ensure_defaults()
    assert len(store.list()) == before

    store._index_path.write_text("not-json", encoding="utf-8")
    assert store._load_index()["personas"] == []
    store._index_path.write_text("[]", encoding="utf-8")
    assert store._load_index()["personas"] == []

    bad = personas / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    assert store.get("bad") is None

    orphan = personas / "orphan.json"
    orphan.write_text(json.dumps({"persona_id": "orphan", "name": "O"}), encoding="utf-8")
    ids = [p.persona_id for p in store.list()]
    assert "orphan" in ids

    assert personas_dir(tmp_path) == personas


class TestPersonaSaveCreatedAt:
    """Persona save populates created_at on first write."""

    def test_save_sets_created_at_on_new(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from groket import paths

        home = tmp_path / "home3"
        home.mkdir()
        personas = home / "personas"
        personas.mkdir()
        monkeypatch.setattr(paths, "APP_HOME", home)
        monkeypatch.setattr(paths, "personas_home", lambda: personas)

        store = PersonaStore(tmp_path / "work3")
        p = Persona(persona_id="new-p", name="New")
        p.created_at = ""  # Force empty
        saved = store.save(p)
        assert saved.created_at
        assert saved.updated_at == saved.created_at
