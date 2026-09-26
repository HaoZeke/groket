"""Unit tests for forms helpers."""

from __future__ import annotations

from pathlib import Path

from groket.ui.forms import (
    DOCKER_IMAGE_OPTIONS,
    PERSONA_NONE,
    batch_parallel_options,
    default_model_selection,
    docker_select_options,
    docker_select_value_or_default,
    load_active_model_ids,
    model_selection_items,
    normalize_batch_parallel,
    normalize_docker_choice,
    normalize_persona_id,
    persona_select_options,
    persona_select_value,
    select_is_blank,
    select_null,
    select_value_str,
    selection_list_selected_ids,
)


class TestNormalizeBatchParallel:
    def test_none_uses_default(self) -> None:
        assert normalize_batch_parallel(None) >= 1

    def test_zero_clamps_to_1(self) -> None:
        assert normalize_batch_parallel(0) == 1

    def test_valid_int(self) -> None:
        assert normalize_batch_parallel(3) == 3

    def test_str_int(self) -> None:
        assert normalize_batch_parallel("2") == 2

    def test_bad_str_uses_default(self) -> None:
        assert normalize_batch_parallel("x") >= 1

    def test_large_clamps_to_32(self) -> None:
        assert normalize_batch_parallel(100) == 32

    def test_custom_default(self) -> None:
        assert normalize_batch_parallel(None, default=5) == 5


def test_batch_parallel_options_nonempty() -> None:
    opts = batch_parallel_options()
    assert opts
    assert all(isinstance(a, tuple) and len(a) == 2 for a in opts)


class TestSelectValueStr:
    def test_normal_str(self) -> None:
        assert select_value_str("a") == "a"

    def test_none_returns_default(self) -> None:
        assert select_value_str(None, default="d") == "d"

    def test_int_coerced(self) -> None:
        assert select_value_str(3) == "3"

    def test_bool_returns_default(self) -> None:
        assert select_value_str(True, default="d") == "d"
        assert select_value_str(False, default="d") == "d"

    def test_none_str_returns_default(self) -> None:
        assert select_value_str("None", default="d") == "d"

    def test_false_str_returns_default(self) -> None:
        assert select_value_str("False", default="d") == "d"

    def test_empty_str_returns_default(self) -> None:
        assert select_value_str("", default="d") == "d"

    def test_no_selection_sentinel(self) -> None:
        class NoSelection:
            pass

        assert select_value_str(NoSelection(), default="x") == "x"

    def test_select_null_and_is_blank(self) -> None:
        from textual.widgets import Select

        assert select_is_blank(None)
        assert select_is_blank(False)  # legacy Select.BLANK
        assert select_is_blank(select_null())
        null = getattr(Select, "NULL", None)
        if null is not None:
            assert select_is_blank(null)
            assert select_null() is null
        assert not select_is_blank("medium")
        assert select_value_str(select_null(), default="") == ""


class TestDockerSelect:
    def test_options_returns_list(self) -> None:
        opts = docker_select_options()
        assert len(opts) == len(DOCKER_IMAGE_OPTIONS)

    def test_normalize_known_value(self) -> None:
        assert normalize_docker_choice("fully-loaded") == "fully-loaded"
        assert normalize_docker_choice("minimal") == "minimal"

    def test_normalize_alias_full(self) -> None:
        assert normalize_docker_choice("full") == "fully-loaded"
        assert normalize_docker_choice("default") == "fully-loaded"

    def test_normalize_alias_min(self) -> None:
        assert normalize_docker_choice("min") == "minimal"
        assert normalize_docker_choice("bare") == "minimal"

    def test_normalize_unknown_falls_back(self) -> None:
        from groket.docker.base_profiles import DEFAULT_DOCKER_IMAGE

        assert normalize_docker_choice("random-image") == DEFAULT_DOCKER_IMAGE

    def test_normalize_none_uses_default(self) -> None:
        from groket.docker.base_profiles import DEFAULT_DOCKER_IMAGE

        assert normalize_docker_choice(None) == DEFAULT_DOCKER_IMAGE

    def test_select_value_or_default(self) -> None:
        assert docker_select_value_or_default("fully-loaded") == "fully-loaded"
        assert docker_select_value_or_default(None) is not None


class TestModelSelection:
    def test_items_defaults(self) -> None:
        items = model_selection_items(None, catalog=["m1", "m2"], default_select_all=False)
        assert items
        assert len(items) >= 1

    def test_items_with_initial(self) -> None:
        items = model_selection_items(["m1"], catalog=["m1", "m2"], default_select_all=False)
        assert items

    def test_items_select_all(self) -> None:
        items = model_selection_items(None, catalog=["m1", "m2"], default_select_all=True)
        selected = [i for i in items if i[2]]
        assert len(selected) == 2

    def test_items_stale_config(self) -> None:
        items = model_selection_items(["stale-model"], catalog=["m1", "m2"])
        extras = [i for i in items if "not in catalog" in i[0]]
        assert len(extras) == 1

    def test_load_active_model_ids_no_crash(self) -> None:
        ids = load_active_model_ids()
        assert isinstance(ids, list)

    def test_default_model_selection(self) -> None:
        result = default_model_selection()
        assert isinstance(result, list)


class TestPersonaSelect:
    def test_persona_select_options_no_work_dir(self) -> None:
        opts = persona_select_options(None)
        assert len(opts) >= 1
        assert opts[0][1] == PERSONA_NONE

    def test_persona_select_options_with_dir(self, tmp_path: Path) -> None:
        opts = persona_select_options(tmp_path)
        assert len(opts) >= 1

    def test_normalize_persona_id_none(self) -> None:
        assert normalize_persona_id(None) == ""

    def test_normalize_persona_id_none_sentinel(self) -> None:
        assert normalize_persona_id(PERSONA_NONE) == ""

    def test_normalize_persona_id_valid(self) -> None:
        assert normalize_persona_id("my-persona") == "my-persona"

    def test_normalize_persona_no_selection(self) -> None:
        class _NoSelection:
            pass

        assert normalize_persona_id(_NoSelection()) == ""

    def test_persona_select_value_empty(self) -> None:
        assert persona_select_value("") == PERSONA_NONE
        assert persona_select_value(None) == PERSONA_NONE

    def test_persona_select_value_valid(self) -> None:
        assert persona_select_value("my-persona") == "my-persona"


class TestSelectionListSelectedIds:
    def test_basic(self) -> None:
        class SL:
            selected = ["a", "b"]

        assert selection_list_selected_ids(SL()) == ["a", "b"]  # type: ignore[arg-type]  # stub for test

    def test_dedup(self) -> None:
        class SL:
            selected = ["a", "a", "b"]

        assert selection_list_selected_ids(SL()) == ["a", "b"]  # type: ignore[arg-type]  # stub for test

    def test_empty(self) -> None:
        class SL:
            selected: list[str] = []

        assert selection_list_selected_ids(SL()) == []  # type: ignore[arg-type]  # stub for test

    def test_exception_handling(self) -> None:
        class BadSL:
            @property
            def selected(self):
                raise RuntimeError("boom")

        assert selection_list_selected_ids(BadSL()) == []  # type: ignore[arg-type]  # stub for test


class TestLoadActiveModelIdsFallback:
    def test_active_model_ids_exception_uses_load_models(self) -> None:
        """load_active_model_ids falls back to load_models when active_model_ids raises."""
        from unittest.mock import patch

        with patch(
            "groket.ui.forms.active_model_ids",
            side_effect=RuntimeError("boom"),
        ):
            ids = load_active_model_ids()
            assert isinstance(ids, list)


class TestDefaultModelSelectionFallback:
    def test_default_model_id_exception(self) -> None:
        """default_model_selection returns a list when default_model_id raises."""
        from unittest.mock import patch

        with patch(
            "groket.runs.batch.default_model_id",
            side_effect=RuntimeError("no mod"),
        ):
            result = default_model_selection()
            assert isinstance(result, list)


class TestPersonaSelectOptionsGithub:
    def test_persona_with_github_token(self, tmp_path: Path) -> None:
        """Persona with github_token shows token marker in select options."""
        from groket.runs.personas import Persona, PersonaStore

        store = PersonaStore(tmp_path)
        store.save(
            Persona(
                persona_id="gh-persona",
                name="GH Persona",
                github_write=True,
                github_token="ghp_test123",
            )
        )
        opts = persona_select_options(tmp_path)
        labels = [o[0] for o in opts]
        assert any("token" in lab for lab in labels)

    def test_persona_with_github_token_env(self, tmp_path: Path) -> None:
        """Persona with github_token_env shows token-env marker in select options."""
        from groket.runs.personas import Persona, PersonaStore

        store = PersonaStore(tmp_path)
        store.save(
            Persona(
                persona_id="env-persona",
                name="Env Persona",
                github_write=True,
                github_token_env="MY_GH_TOKEN",
            )
        )
        opts = persona_select_options(tmp_path)
        labels = [o[0] for o in opts]
        assert any("token-env" in lab for lab in labels)
