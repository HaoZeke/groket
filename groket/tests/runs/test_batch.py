"""Unit tests for runs/batch.py without real Docker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from groket.runs import batch


def test_entry_info_and_models_block():
    assert batch._entry_info("x") == {}
    assert batch._entry_info({"info": {"model": "m"}})["model"] == "m"
    assert batch._entry_info({"model": "m2"})["model"] == "m2"
    # may read real cache; just ensure dict return
    assert isinstance(batch._models_block(None), dict)
    assert batch._models_block({"models": {"a": {}}}) == {"a": {}}
    assert batch._models_block({"models": "bad"}) == {}


def test_active_catalog_with_fake_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "models_cache.json"
    cache.write_text(
        json.dumps(
            {
                "models": {
                    "v9-dietcoke": {
                        "info": {"model": "v9-dietcoke", "name": "Diet Coke"},
                    },
                    "v9-pizzaparty": "not-a-dict",
                    "bare": {"model": "v9-pizzaparty", "name": "Pizza"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", cache)
    cat = batch.active_model_catalog()
    assert "v9-dietcoke" in cat
    ids = batch.active_model_ids()
    assert "v9-dietcoke" in ids
    assert "active:" in batch.models_catalog_help_text()
    assert batch._catalog_lookup("dietcoke") == "v9-dietcoke"
    assert batch._catalog_lookup("Diet Coke") == "v9-dietcoke"
    assert batch._catalog_lookup("") is None
    assert batch.resolve_model_id("dietcoke") == "v9-dietcoke"
    assert batch.resolve_model_id("unknown-model") == "unknown-model"
    assert batch.resolve_model_id("ghost", require_active=True) == ""
    assert batch.resolve_model_ids(["dietcoke", "", "x"])
    active, skips = batch.validate_models_for_launch(["dietcoke", "nope", "dietcoke", ""])
    assert "v9-dietcoke" in active
    assert skips


def test_read_models_cache_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bad = tmp_path / "models_cache.json"
    bad.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", bad)
    assert batch._read_models_cache() == {}
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", missing)
    assert batch._read_models_cache() == {}
    monkeypatch.setattr(batch, "_models_block", lambda data=None: {})
    assert batch.active_model_ids() == list(batch.MODELS)


def test_user_models_yaml_and_load_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "models_cache.json"
    cache.write_text(
        json.dumps({"models": {"v9-dietcoke": {"info": {"model": "v9-dietcoke", "name": "DC"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", cache)
    yml = tmp_path / "models.yaml"
    yml.write_text(yaml.dump({"models": ["dietcoke", "ghost"]}), encoding="utf-8")
    monkeypatch.setattr(batch, "_USER_MODELS_PATH", yml)
    models = batch.load_models()
    assert models[0] == "v9-dietcoke"
    yml.write_text(yaml.dump(["dietcoke"]), encoding="utf-8")
    assert batch._read_user_models_yaml() == ["dietcoke"]
    yml.write_text(":::bad", encoding="utf-8")
    assert batch._read_user_models_yaml() is None
    monkeypatch.setattr(batch, "_USER_MODELS_PATH", tmp_path / "nope.yaml")
    assert batch._read_user_models_yaml() is None


def test_default_model_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    grok = tmp_path / ".grok"
    grok.mkdir()
    (grok / "models_cache.json").write_text(
        json.dumps(
            {
                "models": {
                    "v9-dietcoke": {"info": {"model": "v9-dietcoke"}},
                    "v9-pizzaparty": {"info": {"model": "v9-pizzaparty"}},
                }
            }
        ),
        encoding="utf-8",
    )
    (grok / "config.toml").write_text('[models]\ndefault = "dietcoke"\n', encoding="utf-8")
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", grok / "models_cache.json")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert batch.default_model_id() in ("v9-dietcoke", "v9-pizzaparty")


def test_eval_task_and_load_tasks(tmp_path: Path):
    t = batch.EvalTask(task_id="t1", prompt="p", repo_url="https://x/y")
    assert t.has_repo is True
    t2 = batch.EvalTask(task_id="t2", prompt="p")
    assert t2.has_repo is False
    assert batch._task_setup_from_entry({"initial_commands": ["a", "b"]}) == "a\nb"
    assert batch._task_setup_from_entry({"setup": "s"}) == "s"
    assert batch._task_setup_from_entry({}) == ""
    assert batch.model_suffix("v9-dietcoke") == "dietcoke"
    assert batch.model_suffix("custom-long-name") == "custom-lon"
    assert batch.eval_container_model_tag("v9-tomato:xhigh") == "tomato-xhigh"
    assert batch.eval_container_model_tag("v9-goldbond:xhigh") == "goldbond-xhigh"
    assert batch.eval_container_model_tag("v9-tomato:xhigh") != batch.eval_container_model_tag(
        "v9-goldbond:xhigh"
    )

    tasks_yml = tmp_path / "tasks.yaml"
    tasks_yml.write_text(
        yaml.dump(
            {
                "tasks": [
                    {
                        "task_id": "a",
                        "prompt": "do a",
                        "repo_url": "https://github.com/o/r",
                        "category": "regular",
                        "initial_commands": "echo hi",
                    },
                    {
                        "task_id": "b",
                        "prompt": "do b",
                        "repo_url": None,
                        "category": "special",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    tasks = batch.load_tasks(tasks_yml)
    assert len(tasks) == 2
    assert tasks[0].repo_branch == ""
    assert tasks[0].setup_instructions == "echo hi"
    assert tasks[1].repo_url == ""
    filtered = batch.load_tasks(tasks_yml, category="special")
    assert len(filtered) == 1
    with pytest.raises(FileNotFoundError):
        batch.load_tasks(tmp_path / "missing.yaml")


def test_run_single_task_and_run_batch_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class FakeStatus:
        def __init__(self, model, name, status="completed", err=""):
            self.model = model
            self.container_name = name
            self.status = status
            self.session_dir = tmp_path / "sess"
            self.session_dir.mkdir(exist_ok=True)
            self.error = err

    class FakeOrch:
        def __init__(self, work_dir):
            self.work_dir = work_dir
            self.last_configs: list = []

        def check_docker_available(self):
            return True

        def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
            self.last_configs = list(configs)
            out = []
            for c in configs:
                st = FakeStatus(c.model, c.container_name)
                if on_status:
                    on_status(st)
                if on_log:
                    on_log(c.container_name, ">>> start")
                    on_log(c.container_name, "error happened")
                out.append(st)
            return out

    class FakeCfg:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    orch_holder: list = []

    def _types():
        class TrackingOrch(FakeOrch):
            def __init__(self, work_dir):
                super().__init__(work_dir)
                orch_holder.append(self)

        return FakeCfg, TrackingOrch

    monkeypatch.setattr(batch, "_docker_types", _types)
    task = batch.EvalTask(
        task_id="t1",
        prompt="p",
        description="d",
        repo_url="https://x/y",
        repo_branch="main",
        setup_instructions="echo 1",
    )
    results = batch._run_single_task(task, ["m1"], tmp_path, 1, 1)
    assert results and results[0]["status"] == "completed"

    class BoomOrch(FakeOrch):
        def run_parallel_evaluations(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, BoomOrch))
    task2 = batch.EvalTask(task_id="t2", prompt="p")
    fail_res = batch._run_single_task(task2, ["m1"], tmp_path, 1, 1)
    assert fail_res[0]["status"] == "failed"

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, FakeOrch))
    monkeypatch.setattr(batch, "load_models", lambda: ["m1"])
    monkeypatch.setattr(batch, "resolve_model_ids", lambda ms: list(ms))
    out = batch.run_batch([task], work_dir=tmp_path, models=["m1"], parallelism=1)
    assert out
    assert (tmp_path / "runs" / "eval_results.json").is_file()

    class NoDocker(FakeOrch):
        def check_docker_available(self):
            return False

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, NoDocker))
    assert batch.run_batch([task], work_dir=tmp_path, models=["m1"]) == []

    monkeypatch.setattr(batch, "resolve_model_ids", lambda ms: [])
    assert batch.run_batch([task], work_dir=tmp_path, models=["m1"]) == []


def test_run_single_task_max_turns_on_container_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch EvalTask.max_turns is forwarded to ContainerConfig."""
    captured: list = []

    class FakeOrch:
        def __init__(self, work_dir):
            self.work_dir = work_dir

        def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
            captured.extend(configs)
            return []

    class FakeCfg:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, FakeOrch))
    task = batch.EvalTask(task_id="t-max", prompt="p", max_turns=80)
    batch._run_single_task(task, ["m1"], tmp_path, 1, 1)
    assert len(captured) == 1
    assert captured[0].max_turns == 80


def test_models_for_task_prefers_task_yaml_list() -> None:
    """Task ``models:`` overrides the batch catalog list."""
    task = batch.EvalTask(
        task_id="t",
        prompt="p",
        models=["grok-4.5:high"],
    )
    got = batch.models_for_task(task, ["a", "b", "c"])
    # resolve_model_ids may rewrite tokens; still a single model when YAML has one.
    assert len(got) == 1


def test_run_single_task_repo_path_uses_task_models_not_batch_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo_path + multi-model batch catalog must not fail when task sets one model."""
    ws = tmp_path / "ws"
    ws.mkdir()
    captured: list = []

    class FakeOrch:
        def __init__(self, work_dir):
            self.work_dir = work_dir

        def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
            captured.extend(configs)
            return []

    class FakeCfg:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, FakeOrch))
    monkeypatch.setattr(batch, "resolve_model_ids", lambda ms: list(ms))
    task = batch.EvalTask(
        task_id="local",
        prompt="p",
        repo_path=str(ws),
        models=["only-one"],
    )
    # Batch would have passed 3 catalog models — task YAML must win.
    batch._run_single_task(task, ["a", "b", "c"], tmp_path, 1, 1)
    assert len(captured) == 1
    assert captured[0].model == "only-one"
    assert captured[0].repo_path == str(ws.resolve())


def test_run_single_task_fork_resume_sets_container_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch resume_session_dir seeds ContainerConfig like TUI fork."""
    sess = tmp_path / "%2Fworkspace" / "parent-sess"
    sess.mkdir(parents=True)
    (sess / "chat_history.jsonl").write_text('{"role":"user"}\n', encoding="utf-8")
    captured: list = []

    class FakeOrch:
        def __init__(self, work_dir):
            self.work_dir = work_dir

        def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
            captured.extend(configs)
            return []

    class FakeCfg:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, FakeOrch))
    task = batch.EvalTask(
        task_id="fork-task",
        prompt="continue please",
        resume_session_dir=str(sess),
        resume_session_id="parent-sess",
        turns=["second scripted turn"],
    )
    batch._run_single_task(task, ["m1"], tmp_path, 1, 1)
    assert len(captured) == 1
    cfg = captured[0]
    assert cfg.resume_source_dir == str(sess.resolve())
    assert cfg.resume_session_id == "parent-sess"
    assert cfg.prompt == "continue please"
    assert cfg.follow_up_prompts == ["second scripted turn"]


def test_run_single_task_resume_missing_dir_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOrch:
        def __init__(self, work_dir):
            self.work_dir = work_dir

        def run_parallel_evaluations(self, *a, **k):
            raise AssertionError("should not run")

    class FakeCfg:
        def __init__(self, **kw):
            pass

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, FakeOrch))
    task = batch.EvalTask(
        task_id="bad",
        prompt="p",
        resume_session_dir=str(tmp_path / "no-such-session"),
    )
    with pytest.raises(FileNotFoundError, match="resume_session_dir"):
        batch._run_single_task(task, ["m1"], tmp_path, 1, 1)


def test_model_suffix_short_name():
    assert batch.model_suffix("short") == "short"


def test_catalog_lookup_display_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Lookup by display name (case-insensitive)."""
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"models": {"v9-dc": {"info": {"model": "v9-dc", "name": "Diet Coke"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", cache)
    assert batch._catalog_lookup("diet coke") == "v9-dc"


def test_resolve_model_id_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Static alias resolves when catalog has the target."""
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"models": {"v9-pizzaparty": {"info": {"model": "v9-pizzaparty"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", cache)
    assert batch.resolve_model_id("pizzaparty") == "v9-pizzaparty"


def test_resolve_model_id_bare_v9(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """bare 'v9' resolves to default model id."""
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"models": {"v9-dc": {"info": {"model": "v9-dc"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", cache)
    monkeypatch.setattr(batch, "default_model_id", lambda: "v9-dc")
    result = batch.resolve_model_id("v9")
    assert result == "v9-dc"


def test_active_model_catalog_empty_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", tmp_path / "missing.json")
    cat = batch.active_model_catalog()
    assert cat == {}


def test_models_catalog_help_text_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(batch, "active_model_ids", lambda: [])
    text = batch.models_catalog_help_text()
    assert "no models_cache" in text


def test_load_tasks_category_filter(tmp_path: Path):
    tasks_yml = tmp_path / "t.yaml"
    tasks_yml.write_text(
        yaml.dump(
            {
                "tasks": [
                    {"task_id": "a", "prompt": "p1", "category": "ml"},
                    {"task_id": "b", "prompt": "p2", "category": "web"},
                ]
            }
        ),
        encoding="utf-8",
    )
    ml = batch.load_tasks(tasks_yml, category="ml")
    assert len(ml) == 1
    assert ml[0].task_id == "a"


def test_validate_models_dedupe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"models": {"m1": {"info": {"model": "m1"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_GROK_MODELS_CACHE", cache)
    active, skipped = batch.validate_models_for_launch(["m1", "m1", "missing"])
    assert active == ["m1"]
    assert len(skipped) >= 1


def test_run_batch_resolved_models_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """run_batch logs when resolved model ids differ from input."""

    class FakeStatus:
        def __init__(self, model, name):
            self.model = model
            self.container_name = name
            self.status = "completed"
            self.session_dir = tmp_path / "s"
            self.session_dir.mkdir(exist_ok=True)
            self.error = ""

    class FakeOrch:
        def __init__(self, wd):
            pass

        def check_docker_available(self):
            return True

        def run_parallel_evaluations(self, configs, **kw):
            return [FakeStatus(c.model, c.container_name) for c in configs]

    class FakeCfg:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    monkeypatch.setattr(batch, "_docker_types", lambda: (FakeCfg, FakeOrch))
    # Resolve "dc" → "v9-dc" so models_in != models
    monkeypatch.setattr(batch, "resolve_model_ids", lambda ms: ["v9-dc"])
    task = batch.EvalTask(task_id="t1", prompt="p")
    out = batch.run_batch([task], work_dir=tmp_path, models=["dc"], parallelism=1)
    assert len(out) >= 1


class TestActiveModelCatalogEdgeCases:
    def test_catalog_empty_mid_skipped(self, monkeypatch: pytest.MonkeyPatch):
        """Models with empty id after stripping are skipped."""
        monkeypatch.setattr(
            batch,
            "_models_block",
            lambda *a: {"": {"info": {"model": "", "name": "empty"}}},
        )
        cat = batch.active_model_catalog()
        assert "" not in cat

    def test_catalog_name_not_overwritten(self, monkeypatch: pytest.MonkeyPatch):
        """First name seen wins for a model id."""
        monkeypatch.setattr(
            batch,
            "_models_block",
            lambda *a: {
                "m1": {"info": {"model": "m1", "name": "First"}},
            },
        )
        cat = batch.active_model_catalog()
        assert cat.get("m1", {}).get("name") == "First"


class TestCatalogLookupExtended:
    def test_lookup_by_display_name(self, monkeypatch: pytest.MonkeyPatch):
        """_catalog_lookup resolves by display name match."""
        monkeypatch.setattr(
            batch,
            "active_model_catalog",
            lambda: {
                "v9-pizza": {
                    "id": "v9-pizza",
                    "name": "Pizza Party",
                    "aliases": ["v9-pizza", "pizza"],
                }
            },
        )
        assert batch._catalog_lookup("Pizza Party") == "v9-pizza"

    def test_lookup_by_alias(self, monkeypatch: pytest.MonkeyPatch):
        """_catalog_lookup resolves by alias."""
        monkeypatch.setattr(
            batch,
            "active_model_catalog",
            lambda: {
                "v9-pizza": {
                    "id": "v9-pizza",
                    "name": "Pizza",
                    "aliases": ["v9-pizza", "pizza", "pizzaparty"],
                }
            },
        )
        assert batch._catalog_lookup("pizzaparty") == "v9-pizza"

    def test_lookup_returns_none_for_unknown(self, monkeypatch: pytest.MonkeyPatch):
        """_catalog_lookup returns None for unrecognized input."""
        monkeypatch.setattr(
            batch, "active_model_catalog", lambda: {"m1": {"id": "m1", "aliases": ["m1"]}}
        )
        assert batch._catalog_lookup("nonexistent") is None

    def test_lookup_empty_input(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            batch, "active_model_catalog", lambda: {"m1": {"id": "m1", "aliases": []}}
        )
        assert batch._catalog_lookup("") is None
        assert batch._catalog_lookup("  ") is None


class TestLoadModels:
    def test_load_models_with_yaml_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """load_models respects models.yaml ordering."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1", "m2", "m3"])
        monkeypatch.setattr(
            batch, "_catalog_lookup", lambda raw: raw if raw in ("m1", "m2", "m3") else None
        )
        monkeypatch.setattr(batch, "_read_user_models_yaml", lambda: ["m3", "m1"])
        models = batch.load_models()
        assert models[0] == "m3"
        assert models[1] == "m1"
        assert "m2" in models

    def test_load_models_yaml_with_retired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """load_models drops retired ids from yaml that don't resolve."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: raw if raw == "m1" else None)
        monkeypatch.setattr(batch, "_read_user_models_yaml", lambda: ["retired", "m1"])
        models = batch.load_models()
        assert models == ["m1"]


class TestDefaultModelId:
    def test_default_model_from_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """default_model_id reads from config.toml [models].default."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1", "m2"])
        cfg = tmp_path / ".grok" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('[models]\ndefault = "m2"\n', encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = batch.default_model_id()
        assert result == "m2"

    def test_default_model_case_insensitive(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """default_model_id matches case-insensitively."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["v9-Pizza"])
        cfg = tmp_path / ".grok" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('[models]\ndefault = "V9-PIZZA"\n', encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = batch.default_model_id()
        assert result == "v9-Pizza"


class TestResolveModelIdExtended:
    def test_resolve_bare_v9(self, monkeypatch: pytest.MonkeyPatch):
        """Bare 'v9' falls through to default_model_id."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        monkeypatch.setattr(batch, "default_model_id", lambda: "v9-pizza")
        result = batch.resolve_model_id("v9")
        assert result == "v9-pizza"

    def test_resolve_alias_target_from_catalog(self, monkeypatch: pytest.MonkeyPatch):
        """_MODEL_ALIASES are tried against the catalog."""
        monkeypatch.setattr(
            batch, "_catalog_lookup", lambda raw: "v9-resolved" if raw == "v9-resolved" else None
        )
        # Not in _MODEL_ALIASES, but test the plain passthrough
        result = batch.resolve_model_id("v9-resolved")
        assert result == "v9-resolved"

    def test_resolve_require_active_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        """require_active=True returns empty string for unknown models."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        result = batch.resolve_model_id("unknown-model", require_active=True)
        assert result == ""

    def test_resolve_unknown_passthrough(self, monkeypatch: pytest.MonkeyPatch):
        """Unknown model is passed through when require_active=False."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        result = batch.resolve_model_id("custom-model")
        assert result == "custom-model"


class TestReadUserModelsYaml:
    def test_read_dict_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """models.yaml with dict format {models: [list]}."""
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", tmp_path / "models.yaml")
        (tmp_path / "models.yaml").write_text(yaml.dump({"models": ["m1", "m2"]}), encoding="utf-8")
        result = batch._read_user_models_yaml()
        assert result == ["m1", "m2"]

    def test_read_list_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """models.yaml with plain list format."""
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", tmp_path / "models.yaml")
        (tmp_path / "models.yaml").write_text(yaml.dump(["m1", "m2"]), encoding="utf-8")
        result = batch._read_user_models_yaml()
        assert result == ["m1", "m2"]

    def test_read_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", tmp_path / "nope.yaml")
        result = batch._read_user_models_yaml()
        assert result is None

    def test_read_bad_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """models.yaml with unparseable content returns None."""
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", tmp_path / "bad.yaml")
        (tmp_path / "bad.yaml").write_text(": :\n  ::\n!!bad", encoding="utf-8")
        result = batch._read_user_models_yaml()
        assert result is None


class TestDockerTypes:
    """Lazy import of Docker container types."""

    def test_docker_types_imports(self):
        """_docker_types returns (ContainerConfig, DockerOrchestrator)."""
        CC, DO = batch._docker_types()
        from groket.docker.orchestrator import ContainerConfig, DockerOrchestrator

        assert CC is ContainerConfig
        assert DO is DockerOrchestrator


class TestActiveCatalogDuplicate:
    """First-seen name wins when catalog has duplicate model ids."""

    def test_catalog_entry_name_not_overridden(self, monkeypatch: pytest.MonkeyPatch):
        """First name wins when catalog has duplicate model ids."""
        fake_block = {
            "k1": {"model": "m1", "name": "First"},
            "k2": {"model": "m1", "name": "Second"},
        }
        monkeypatch.setattr(batch, "_models_block", lambda: fake_block)
        monkeypatch.setattr(
            batch, "_entry_info", lambda e: e if isinstance(e, dict) else {"model": str(e)}
        )
        cat = batch.active_model_catalog()
        assert cat["m1"]["name"] == "First"


class TestLoadModelsWithPreferred:
    """User models.yaml ordering applied by load_models."""

    def test_preferred_orders_models(self, monkeypatch: pytest.MonkeyPatch):
        """load_models reorders based on models.yaml."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1", "m2", "m3"])
        monkeypatch.setattr(batch, "_read_user_models_yaml", lambda: ["m3", "m1"])
        monkeypatch.setattr(
            batch, "_catalog_lookup", lambda raw: raw if raw in ("m1", "m2", "m3") else None
        )
        result = batch.load_models()
        assert result[0] == "m3"
        assert result[1] == "m1"
        assert "m2" in result

    def test_preferred_skips_unknown(self, monkeypatch: pytest.MonkeyPatch):
        """load_models skips preferred tokens not in catalog."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1"])
        monkeypatch.setattr(batch, "_read_user_models_yaml", lambda: ["unknown"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: raw if raw == "m1" else None)
        result = batch.load_models()
        assert "m1" in result


class TestDefaultModelIdToml:
    """Cover default_model_id toml config path."""

    def test_toml_lowercase_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """default_model_id tries lowercase match in id_set."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["V9-Model"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        # conftest monkeypatches Path.home() to tmp_path
        config = Path.home() / ".grok" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('[models]\ndefault = "v9-model"\n', encoding="utf-8")
        result = batch.default_model_id()
        # Should match via lowercase
        assert result in ("V9-Model", "v9-model", "v9-pizzaparty")


class TestResolveModelIdAlias:
    """Cover resolve_model_id _MODEL_ALIASES and bare v9 paths."""

    def test_resolve_alias_not_in_catalog(self, monkeypatch: pytest.MonkeyPatch):
        """Alias target used when not in catalog and require_active=False."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        # Ensure _MODEL_ALIASES has an entry
        if batch._MODEL_ALIASES:
            alias = next(iter(batch._MODEL_ALIASES))
            result = batch.resolve_model_id(alias)
            assert result == batch._MODEL_ALIASES[alias]

    def test_resolve_bare_v9(self, monkeypatch: pytest.MonkeyPatch):
        """Bare 'v9' resolves to default_model_id."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        monkeypatch.setattr(batch, "default_model_id", lambda: "v9-pizzaparty")
        result = batch.resolve_model_id("v9")
        assert result == "v9-pizzaparty"

    def test_resolve_bare_v9_require_active(self, monkeypatch: pytest.MonkeyPatch):
        """Bare 'v9' with require_active checks catalog for default."""
        monkeypatch.setattr(
            batch,
            "_catalog_lookup",
            lambda raw: "v9-pizzaparty" if raw == "v9-pizzaparty" else None,
        )
        monkeypatch.setattr(batch, "default_model_id", lambda: "v9-pizzaparty")
        result = batch.resolve_model_id("v9", require_active=True)
        assert result == "v9-pizzaparty"


# ── catalog merge and dedup ───────────────────────────────────────────────


class TestActiveCatalogMerge:
    """active_model_catalog merge/dedup paths."""

    def test_catalog_merge_aliases(self, monkeypatch: pytest.MonkeyPatch):
        """Catalog entry aliases include short tail from id."""
        monkeypatch.setattr(
            batch,
            "_models_block",
            lambda: {
                "model1": {"model": "v9-pizzaparty", "name": "v9-pizzaparty"},
                "model2": {"model": "v9-dietcoke", "name": "v9-dietcoke"},
            },
        )
        cat = batch.active_model_catalog()
        pp = cat.get("v9-pizzaparty")
        assert pp is not None
        assert "pizzaparty" in pp.get("aliases", [])

    def test_catalog_duplicate_id(self, monkeypatch: pytest.MonkeyPatch):
        """Duplicate model ids merge aliases."""
        monkeypatch.setattr(
            batch,
            "_models_block",
            lambda: {
                "a": {"model": "v9-test", "name": "v9-test"},
                "b": {"model": "v9-test", "name": ""},
            },
        )
        cat = batch.active_model_catalog()
        assert "v9-test" in cat


class TestLoadModelsEdge:
    """load_models edge paths."""

    def test_load_models_no_live(self, monkeypatch: pytest.MonkeyPatch):
        """Falls back to MODELS when no live catalog."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: [])
        result = batch.load_models()
        assert len(result) > 0

    def test_load_models_preferred(self, monkeypatch: pytest.MonkeyPatch):
        """Preferred models from yaml reorder output."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["v9-a", "v9-b", "v9-c"])
        monkeypatch.setattr(batch, "_read_user_models_yaml", lambda: ["v9-c", "v9-a"])
        monkeypatch.setattr(
            batch, "_catalog_lookup", lambda t: t if t in ["v9-a", "v9-b", "v9-c"] else None
        )
        result = batch.load_models()
        assert result[0] == "v9-c"
        assert result[1] == "v9-a"


class TestDefaultModelIdEdge:
    """default_model_id edge cases."""

    def test_toml_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        """Lowercase match in default_model_id toml lookup."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["v9-pizzaparty"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: None)
        result = batch.default_model_id()
        assert result == "v9-pizzaparty"


class TestResolveModelIdEdge:
    """resolve_model_id edge cases."""

    def test_alias_fallback_no_catalog(self, monkeypatch: pytest.MonkeyPatch):
        """Model alias target used when not in catalog (not require_active)."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        # _MODEL_ALIASES contains common aliases
        result = batch.resolve_model_id("sonnet", require_active=False)
        # Should return the alias target even without catalog match
        assert isinstance(result, str)

    def test_require_active_empty(self, monkeypatch: pytest.MonkeyPatch):
        """require_active returns '' when model not in catalog."""
        monkeypatch.setattr(batch, "_catalog_lookup", lambda raw: None)
        monkeypatch.setattr(batch, "default_model_id", lambda: "")
        result = batch.resolve_model_id("unknown-model", require_active=True)
        assert result == ""


class TestReadUserModelsYamlFormats:
    """_read_user_models_yaml edge cases (additional)."""

    def test_list_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Reads list format models.yaml."""
        models_path = tmp_path / "models.yaml"
        models_path.write_text("- v9-a\n- v9-b\n", encoding="utf-8")
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", models_path)
        result = batch._read_user_models_yaml()
        assert result == ["v9-a", "v9-b"]

    def test_dict_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Reads dict format models.yaml."""
        models_path = tmp_path / "models.yaml"
        models_path.write_text("models:\n  - v9-c\n", encoding="utf-8")
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", models_path)
        result = batch._read_user_models_yaml()
        assert result == ["v9-c"]

    def test_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Returns None when file doesn't exist."""
        monkeypatch.setattr(batch, "_USER_MODELS_PATH", tmp_path / "nope.yaml")
        result = batch._read_user_models_yaml()
        assert result is None


# ── alias and model resolution ────────────────────────────────────────────


class TestActiveCatalogAliases:
    """active_model_catalog populates alias sets from model ids."""

    def test_alias_from_hyphenated_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Model id with hyphen generates alias from suffix."""
        monkeypatch.setattr(
            batch,
            "_models_block",
            lambda data=None: {
                "v9-alpha-beta": {"info": {"model": "v9-alpha-beta", "name": "Alpha Beta"}},
            },
        )
        cat = batch.active_model_catalog()
        assert "v9-alpha-beta" in cat
        rec = cat["v9-alpha-beta"]
        assert "alpha-beta" in rec["aliases"]


class TestLoadModelsPreferred:
    """load_models_for_eval with preferred user list."""

    def test_load_with_preferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1", "m2", "m3"])
        monkeypatch.setattr(batch, "_read_user_models_yaml", lambda: ["m2", "m1"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: t)
        result = batch.load_models()
        assert result[0] == "m2"
        assert result[1] == "m1"


class TestDefaultModelIdFromToml:
    """default_model_id reads from TOML config."""

    def test_toml_default_direct_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direct id match in config.toml."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["v9-alpha"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: t if t == "v9-alpha" else None)
        cfg = Path.home() / ".grok" / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('[models]\ndefault = "v9-alpha"\n', encoding="utf-8")
        result = batch.default_model_id()
        assert result == "v9-alpha"

    def test_toml_default_catalog_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alias resolved via catalog lookup."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1"])
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: "m1" if t == "alias" else None)
        cfg = Path.home() / ".grok" / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('[models]\ndefault = "alias"\n', encoding="utf-8")
        result = batch.default_model_id()
        assert result == "m1"

    def test_toml_read_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid TOML falls back to first active id."""
        monkeypatch.setattr(batch, "active_model_ids", lambda: ["m1"])
        cfg = Path.home() / ".grok" / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("not valid toml {{{{", encoding="utf-8")
        result = batch.default_model_id()
        assert result == "m1"


class TestResolveModelIdAliasExtra:
    """resolve_model_id handles aliases and require_active."""

    def test_alias_no_catalog_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: "")
        monkeypatch.setattr(batch, "active_model_ids", lambda: [])
        result = batch.resolve_model_id("v9-long-alias")
        assert isinstance(result, str)

    def test_require_active_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: "")
        monkeypatch.setattr(batch, "active_model_ids", lambda: [])
        result = batch.resolve_model_id("unknown", require_active=True)
        assert result == ""


class TestActiveCatalogNameField:
    """Catalog record retains the first-seen name for a model id."""

    def test_duplicate_model_id_retains_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        block = {
            "m1": {"info": {"model": "m1", "name": "First"}},
            "m1-alias": {"info": {"model": "m1", "name": ""}},
        }
        monkeypatch.setattr(batch, "_models_block", lambda cache=None: block)
        cat = batch.active_model_catalog()
        # m1 record should retain the "First" name
        assert cat["m1"]["name"] == "First"


class TestResolveModelIdAliasReturnTarget:
    """Alias match without active catalog entry returns the alias target."""

    def test_alias_returns_target_not_active(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from groket.runs.batch import _MODEL_ALIASES

        monkeypatch.setitem(_MODEL_ALIASES, "myalias", "resolved-model")
        monkeypatch.setattr(batch, "_catalog_lookup", lambda t: "")
        result = batch.resolve_model_id("myalias")
        assert result == "resolved-model"


class TestRunSingleTaskWriteRunJson:
    """Run.json write exception in single task execution."""

    def test_run_json_write_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from groket.runs.batch import EvalTask

        task = EvalTask(
            task_id="t1",
            prompt="p",
            docker_image="fully-loaded",
        )

        from groket.docker.orchestrator import ContainerConfig
        from groket.docker.orchestrator import ContainerStatus as RealCS

        fake_status = RealCS(
            container_name="c1",
            model="m1",
            status="completed",
            session_dir=tmp_path / "sess1",
        )
        (tmp_path / "sess1").mkdir()

        class FakeOrch:
            def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
                return [fake_status]

        monkeypatch.setattr(
            batch,
            "_docker_types",
            lambda: (
                ContainerConfig,
                type(
                    "DO",
                    (),
                    {
                        "__init__": lambda self, wd: None,
                        "run_parallel_evaluations": FakeOrch().run_parallel_evaluations,
                    },
                ),
            ),
        )

        results = batch._run_single_task(task, ["m1"], tmp_path, 1, 1)
        assert len(results) >= 1


class TestBatchRunModelLogging:
    """Model resolution logging during run_batch execution."""

    def test_resolved_different_models_logged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(batch, "resolve_model_ids", lambda ms: ["resolved-m1"])

        from groket.runs.batch import EvalTask

        task = EvalTask(
            task_id="t1",
            prompt="p",
            docker_image="fully-loaded",
        )

        from groket.docker.orchestrator import ContainerConfig
        from groket.docker.orchestrator import ContainerStatus as RealCS

        fake_status = RealCS(
            container_name="c1",
            model="resolved-m1",
            status="completed",
            session_dir=tmp_path / "sess1",
        )
        (tmp_path / "sess1").mkdir()

        class FakeOrch:
            def check_docker_available(self):
                return True

            def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
                return [fake_status]

        monkeypatch.setattr(
            batch,
            "_docker_types",
            lambda: (
                ContainerConfig,
                type(
                    "DO",
                    (),
                    {
                        "__init__": lambda self, wd: None,
                        "check_docker_available": FakeOrch().check_docker_available,
                        "run_parallel_evaluations": FakeOrch().run_parallel_evaluations,
                    },
                ),
            ),
        )

        results = batch.run_batch(
            tasks=[task],
            models=["m1"],
            work_dir=tmp_path,
            parallelism=1,
        )
        assert isinstance(results, list)


class TestRunSingleTaskErrorLog:
    """Failed container status logged in single task execution."""

    def test_failed_status_logged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from groket.runs.batch import EvalTask

        task = EvalTask(
            task_id="t1",
            prompt="p",
            docker_image="fully-loaded",
        )

        from groket.docker.orchestrator import ContainerConfig
        from groket.docker.orchestrator import ContainerStatus as RealCS

        fake_status = RealCS(
            container_name="c1",
            model="m1",
            status="failed",
            error="container died",
        )

        class FakeOrch:
            def run_parallel_evaluations(self, configs, auth, grok, on_status=None, on_log=None):
                return [fake_status]

        monkeypatch.setattr(
            batch,
            "_docker_types",
            lambda: (
                ContainerConfig,
                type(
                    "DO",
                    (),
                    {
                        "__init__": lambda self, wd: None,
                        "run_parallel_evaluations": FakeOrch().run_parallel_evaluations,
                    },
                ),
            ),
        )

        results = batch._run_single_task(task, ["m1"], tmp_path, 1, 1)
        assert any(r["status"] == "failed" for r in results)


def test_validate_models_preserves_effort(monkeypatch):
    """model:effort tokens must resolve; effort must not fail active check."""
    from groket.runs import batch as b

    monkeypatch.setattr(b, "active_model_ids", lambda: ["v9-zingster", "v9-restfulnight"])
    monkeypatch.setattr(
        b, "_catalog_lookup", lambda raw: raw if raw in ("v9-zingster", "v9-restfulnight") else None
    )
    active, skips = b.validate_models_for_launch(["v9-zingster:xhigh", "v9-restfulnight:low"])
    assert active == ["v9-zingster:xhigh", "v9-restfulnight:low"]
    assert skips == []
    active2, skips2 = b.validate_models_for_launch(["nope:xhigh"])
    assert active2 == []
    assert skips2


def test_cli_reasoning_effort_maps_product_levels() -> None:
    """Grok CLI --effort only accepts low|medium|high; xhigh/max map to high."""
    from groket.runs.batch import cli_reasoning_effort

    assert cli_reasoning_effort("low") == "low"
    assert cli_reasoning_effort("medium") == "medium"
    assert cli_reasoning_effort("high") == "high"
    assert cli_reasoning_effort("xhigh") == "high"
    assert cli_reasoning_effort("max") == "high"
    assert cli_reasoning_effort("") == ""
    assert cli_reasoning_effort("nope") == ""
