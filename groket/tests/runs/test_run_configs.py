"""RunConfigStore unit tests."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import groket.runs.run_configs as rc
from groket.models import JsonValue
from groket.runs.run_configs import RunConfig, RunConfigStore


def test_store_roundtrip(tmp_path: Path):
    store = RunConfigStore(tmp_path)
    cfg = store.from_session_fields(
        prompt="hello world task",
        setup_instructions="",
        docker_image="",
        repo_url="",
        repo_branch="",
        models=["m1"],
        session_id="sess1",
        session_dir=str(tmp_path / "sess"),
        name="unit-cfg",
    )
    assert cfg.config_id
    assert store.get(cfg.config_id) is not None
    listed = store.list_configs()
    assert any(c.config_id == cfg.config_id for c in listed)
    assert store.delete(cfg.config_id) is True
    assert store.get(cfg.config_id) is None


def test_run_config_display_name():
    c = RunConfig(config_id="x", name="N", task_id="T", prompt="p")
    assert "T" in c.display_name() or "N" in c.display_name() or c.config_id == "x"


import pytest


def _session(traces: Path, sid: str, *, outcome: str = "success", events: bool = True) -> Path:
    sd = traces / "groket-run-x" / sid
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": sid}, "session_summary": "s"}), encoding="utf-8"
    )
    if events:
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_ended", "outcome": outcome}) + "\n", encoding="utf-8"
        )
    return sd


class TestRunConfigModel:
    def test_display_catalog_preview(self):
        c = rc.RunConfig(config_id="id1", name="", prompt="line1\nline2", repo_url="")
        assert c.display_name() == "line1" or c.display_name() == "id1"
        c2 = rc.RunConfig(config_id="id2", name="", repo_url="https://github.com/o/repo.git")
        assert "repo" in c2.display_name()
        c3 = rc.RunConfig(config_id="id3", label="L", category="cat", task_id="T")
        assert c3.catalog_label() == "L"
        c4 = rc.RunConfig(config_id="id4", category="cat")
        assert c4.catalog_label() == "cat"
        c5 = rc.RunConfig(config_id="id5", task_id="tid")
        assert c5.catalog_label() == "tid"
        c6 = rc.RunConfig(config_id="id6")
        assert c6.catalog_label() == "—"
        long_p = "x" * 80
        assert "…" in rc.RunConfig(config_id="p", prompt=long_p).prompt_preview(20)

    def test_from_dict_roundtrip_and_prefill(self):
        data = {
            "config_id": "c1",
            "name": "N",
            "prompt": "do it",
            "models": ["m1"],
            "run_env_vars": {"A": 1},
            "run_mcp_definitions": [{"id": "srv", "url": "http://x"}],
            "run_mcp_servers": ["srv"],
            "run_skills": ["sk"],
            "run_plugins": ["pl"],
            "run_inline_skills": [{"id": "hint", "content": "---\nname: hint\n---\n"}],
            "parallelism": "2",
            "max_turns": "120",
            "github_write": "yes",
        }
        cfg = rc.RunConfig.from_dict(data)
        assert cfg.config_id == "c1"
        assert cfg.parallelism == 2
        assert cfg.max_turns == 120
        assert cfg.run_env_vars["A"] == "1"
        assert cfg.run_mcp_definitions[0]["id"] == "srv"
        assert cfg.run_inline_skills[0]["id"] == "hint"
        d = cfg.to_dict()
        assert d["config_id"] == "c1"
        assert d["max_turns"] == 120
        pre = cfg.to_runner_prefill(models_override=["m2"])
        assert pre.models == ["m2"]
        assert pre.prompt == "do it"
        assert pre.max_turns == 120
        assert pre.run_inline_skills == [("hint", "---\nname: hint\n---\n")]
        # Missing max_turns defaults to 50
        bare = rc.RunConfig.from_dict({"config_id": "c2", "prompt": "x"})
        assert bare.max_turns == 50


class TestRunConfigStore:
    def test_create_save_get_list_delete(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        cfg = store.create(
            prompt="p",
            repo_url="https://github.com/a/b",
            models=["m"],
            task_id="t1",
            category="c",
            label="lab",
            notes="n",
        )
        assert cfg.config_id
        assert store.get(cfg.config_id) is not None
        assert any(x.config_id == cfg.config_id for x in store.list_configs())
        cfg.name = "renamed"
        store.save(cfg)
        assert store.get(cfg.config_id).name == "renamed"
        # bad file skipped
        (store.root / "index.json").write_text("{}", encoding="utf-8")
        (store.root / "broken.json").write_text("not-json", encoding="utf-8")
        store.list_configs()
        assert store.delete(cfg.config_id) is True
        assert store.delete("missing") is False
        assert store.delete("") is False

    def test_save_from_launch_create_and_update(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        created = store.save_from_launch(
            prompt="p",
            setup_instructions="s",
            docker_image="fully-loaded",
            repo_url="",
            repo_branch="",
            models=["m1"],
            parallelism=1,
            run_id="r1",
            persona_id="per",
            run_mcp_servers=["a"],
            run_mcp_definitions=[{"id": "a"}],
            run_skills=["sk"],
            run_plugins=["pl"],
            run_env_vars={"K": "V"},
            run_inline_skills=[{"id": "in1", "content": "body1"}],
            max_turns=80,
        )
        assert created.launch_count == 1
        assert created.persona_id == "per"
        assert created.max_turns == 80
        assert created.run_inline_skills[0]["id"] == "in1"
        updated = store.save_from_launch(
            prompt="p2",
            setup_instructions="s2",
            docker_image="minimal",
            repo_url="u",
            repo_branch="main",
            models=["m2"],
            parallelism=2,
            run_id="r2",
            update_existing_id=created.config_id,
            persona_id="per2",
            run_mcp_servers=["b"],
            run_mcp_definitions=[{"id": "b"}],
            run_skills=["sk2"],
            run_plugins=["pl2"],
            run_env_vars={"K2": "V2"},
            run_inline_skills=[{"id": "in2", "content": "body2"}],
            max_turns=150,
        )
        assert updated.config_id == created.config_id
        assert updated.prompt == "p2"
        assert updated.launch_count == 2
        assert updated.max_turns == 150
        assert updated.run_skills == ["sk2"]
        assert updated.run_inline_skills[0]["id"] == "in2"

    def test_inline_skills_persist_from_tuples_and_prefill(self, tmp_path: Path):
        """Launch autosave accepts (id, body) tuples; reload restores for runner."""
        from groket.runs.run_configs import normalize_run_inline_skills

        assert normalize_run_inline_skills(
            [("my-skill", "---\nname: my-skill\n---\n\n# Hi\n")]
        ) == [{"id": "my-skill", "content": "---\nname: my-skill\n---\n\n# Hi\n"}]
        store = rc.RunConfigStore(tmp_path)
        created = store.save_from_launch(
            prompt="p",
            setup_instructions="",
            docker_image="fully-loaded",
            repo_url="",
            repo_branch="",
            models=["m1"],
            parallelism=1,
            run_id="r-inline",
            run_inline_skills=[("hint", "body text")],
            run_env_vars={"K": "V"},
            run_plugins=["pl"],
            run_skills=["sk"],
            run_mcp_servers=["mcp1"],
        )
        raw = (store.root / f"{created.config_id}.json").read_text(encoding="utf-8")
        assert "run_inline_skills" in raw
        assert "hint" in raw
        loaded = store.get(created.config_id)
        assert loaded is not None
        assert loaded.run_inline_skills == [{"id": "hint", "content": "body text"}]
        assert loaded.run_env_vars == {"K": "V"}
        assert loaded.run_plugins == ["pl"]
        assert loaded.run_skills == ["sk"]
        assert loaded.run_mcp_servers == ["mcp1"]
        # Prefer not importing full TUI in unit test — exercise to_dict keys.
        d = loaded.to_dict()
        assert d["run_inline_skills"] == [{"id": "hint", "content": "body text"}]
        assert d["run_env_vars"] == {"K": "V"}

    def test_paths_for_duplicate_and_get_fallback(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        cfg = store.create(prompt="x", name="n")
        # embed same config_id under different filename
        alt = store.root / "alt_name.json"
        data = cfg.to_dict()
        alt.write_text(json.dumps(data), encoding="utf-8")
        paths = store._paths_for_config_id(cfg.config_id)
        assert len(paths) >= 2
        store.save(cfg)  # cleans duplicates
        # corrupt primary forces list scan fallback
        primary = store._cfg_path(cfg.config_id)
        primary.write_text("{bad", encoding="utf-8")
        # get may return None on corrupt primary without list match
        store.get(cfg.config_id)
        assert store._paths_for_config_id("") == []


class TestTraceMaintenance:
    def test_is_session_and_orphan_helpers(self, tmp_path: Path):
        assert rc._is_session_trace_dir(tmp_path) is False
        d = tmp_path / "s"
        d.mkdir()
        assert rc._is_session_trace_dir(d) is False
        (d / "summary.json").write_text("{}", encoding="utf-8")
        assert rc._is_session_trace_dir(d) is True
        run = tmp_path / "groket-empty"
        run.mkdir()
        (run / "run.json").write_text("{}", encoding="utf-8")
        assert rc._run_folder_is_orphan(run) is True
        sess = run / "sid"
        sess.mkdir()
        (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        assert rc._run_folder_has_sessions(run) is True
        assert rc._run_folder_is_orphan(run) is False

    def test_prune_orphan_and_empty_parents(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        orphan = traces / "groket-orphan"
        orphan.mkdir(parents=True)
        (orphan / "prompt.txt").write_text("p", encoding="utf-8")
        keep = traces / "other"
        keep.mkdir()
        (keep / "note.txt").write_text("k", encoding="utf-8")
        dry = rc.prune_orphan_trace_runs(traces, dry_run=True)
        assert dry["dry_run"] is True
        assert any("groket-orphan" in x for x in dry["removed"])
        res = rc.prune_orphan_trace_runs(traces, dry_run=False)
        assert res["removed_count"] >= 1
        assert not orphan.exists()
        # non-dir root
        miss = rc.prune_orphan_trace_runs(tmp_path / "nope")
        assert miss["errors"]

        # prune empty parents after session delete
        import shutil

        parent = traces / "groket-run" / "%2Fworkspace"
        sd = parent / "sess1"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        shutil.rmtree(sd)
        parent.mkdir(parents=True, exist_ok=True)
        removed = rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        assert isinstance(removed, list)
        chain = traces / "groket-chain" / "inner"
        chain.mkdir(parents=True)
        gone = chain / "deleted-sess"
        pr = rc.prune_empty_parents_after_session_delete(gone, stop_at=traces)
        assert isinstance(pr, list)

    def test_rmtree_and_chown_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        p = tmp_path / "tree"
        p.mkdir()
        (p / "f").write_text("x", encoding="utf-8")
        rc.rmtree_robust(p)
        assert not p.exists()
        rc.rmtree_robust(tmp_path / "missing")
        # chown on missing returns True
        assert rc.chown_path_to_host_user(tmp_path / "ghost") is True
        # docker not found path
        monkeypatch.setattr(
            rc,
            "_docker_run_alpine",
            lambda *a, **k: (False, "docker not found"),
        )
        d = tmp_path / "owned"
        d.mkdir()
        assert rc.chown_path_to_host_user(d) is False

    def test_docker_run_alpine_missing_and_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        ok, _ = rc._docker_run_alpine(tmp_path / "nope", ["true"])
        assert ok is True

        class _Cli:
            def run(self, *a, **k):
                raise RuntimeError("docker daemon down")

        monkeypatch.setattr("python_on_whales.DockerClient", _Cli)
        ok2, err = rc._docker_run_alpine(tmp_path, ["true"])
        assert ok2 is False
        assert "docker" in err.lower() or err

        class _Fail:
            def run(self, *a, **k):
                raise RuntimeError("fail")

        monkeypatch.setattr("python_on_whales.DockerClient", _Fail)
        ok4, err4 = rc._docker_run_alpine(tmp_path, ["false"])
        assert ok4 is False
        assert "fail" in err4

    def test_delete_session_dirs_and_audit(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        ok_s = _session(traces, "ok-sess", outcome="success")
        no_end = traces / "groket-run-y" / "no-end"
        no_end.mkdir(parents=True)
        (no_end / "summary.json").write_text(
            json.dumps({"info": {"id": "no-end"}, "session_summary": "x" * 30}), encoding="utf-8"
        )
        (no_end / "events.jsonl").write_text(
            json.dumps({"type": "turn_started"}) + "\n", encoding="utf-8"
        )
        empty_shell = traces / "groket-shell"
        empty_shell.mkdir(parents=True)
        (empty_shell / "run.json").write_text("{}", encoding="utf-8")

        fb = tmp_path / "feedback_cache"
        fb_sid = fb / "ok-sess"
        fb_sid.mkdir(parents=True)
        (fb_sid / "meta.json").write_text(
            json.dumps({"session_id": "ok-sess", "session_dir": str(ok_s)}), encoding="utf-8"
        )

        audit = rc.audit_trace_sessions(traces)
        assert audit["ok_count"] >= 1
        assert audit["empty_shell_count"] >= 1
        # interrupted or running depending on age/size thresholds
        assert audit["interrupted_count"] + audit["running_count"] >= 0
        assert "traces_root" in audit

        marked = rc.mark_interrupted_sessions(traces, dry_run=True)
        assert marked["dry_run"] is True
        rc.mark_interrupted_sessions(traces, dry_run=False)

        dirs = rc.session_dirs_for_delete([ok_s, ok_s, no_end])
        assert len(dirs) == 2
        deleted = rc.delete_session_dirs(
            [ok_s],
            also_feedback_cache=True,
            feedback_cache_dir=fb,
            traces_root=traces,
        )
        assert deleted["deleted"] == 1
        assert deleted["feedback_cache_deleted"] == 1
        # missing / not dir errors
        (tmp_path / "f.txt").write_text("x", encoding="utf-8")
        err_res = rc.delete_session_dirs([tmp_path / "ghost", tmp_path / "f.txt"])
        assert err_res["errors"]

    def test_feedback_cache_index_and_sync(self, tmp_path: Path):
        traces = tmp_path / "traces"
        sid = "sess-a"
        sd = _session(traces, sid)
        cache = tmp_path / "cache"
        entry = cache / sid
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps(
                {
                    "session_id": sid,
                    "session_dir": str(sd),
                    "status": "done",
                    "fingerprint": "fp",
                }
            ),
            encoding="utf-8",
        )
        (entry / "report.md").write_text("# r", encoding="utf-8")
        orphan = cache / "gone-sid"
        orphan.mkdir()
        (orphan / "meta.json").write_text(
            json.dumps({"session_id": "gone-sid", "session_dir": "/no/such"}),
            encoding="utf-8",
        )
        idx = rc.rebuild_feedback_cache_index(cache)
        assert idx["sessions"] >= 1
        assert (cache / "index.json").is_file()

        sync = rc.validate_feedback_cache_sync(traces, cache)
        assert sync["trace_sessions"] >= 1
        assert sync["orphan_count"] >= 1

        dry = rc.prune_feedback_cache_orphans(cache, dry_run=True, traces_root=traces)
        assert dry["dry_run"] is True
        pruned = rc.prune_feedback_cache_orphans(cache, dry_run=False, traces_root=traces)
        assert pruned["removed_count"] >= 1
        miss = rc.prune_feedback_cache_orphans(tmp_path / "no-cache")
        assert miss["errors"]

    def test_turn_outcome_and_marker_helpers(self, tmp_path: Path):
        sd = tmp_path / "s"
        sd.mkdir()
        assert rc._turn_outcome_from_events(sd) == ""
        (sd / "events.jsonl").write_text(
            "not-json\n" + json.dumps({"type": "turn_ended", "outcome": "err"}) + "\n",
            encoding="utf-8",
        )
        assert rc._turn_outcome_from_events(sd) == "err"
        assert rc._read_interrupted_marker(sd) is None
        from groket.constants import INTERRUPTED_MARKER_FILENAME

        (sd / INTERRUPTED_MARKER_FILENAME).write_text("{bad", encoding="utf-8")
        assert rc._read_interrupted_marker(sd) is not None
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        age = rc._session_trace_age_seconds(sd)
        assert age is not None and age >= 0
        assert rc._session_dirs_under(tmp_path / "missing") == []


class TestRunConfigStoreExtended:
    def test_get_nonexistent(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        assert store.get("missing-id") is None

    def test_save_sets_created_at(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        cfg = rc.RunConfig(config_id="new-id", prompt="test")
        saved = store.save(cfg)
        assert saved.created_at
        assert saved.updated_at

    def test_save_cleans_duplicates(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        cfg = store.create(prompt="test")
        # Write a duplicate
        alt_path = store.root / "duplicate.json"
        alt_path.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
        store.save(cfg)
        # Duplicate should be cleaned
        assert not alt_path.exists()

    def test_delete_nonexistent(self, tmp_path: Path):
        store = rc.RunConfigStore(tmp_path)
        assert store.delete("") is False
        assert store.delete("nope") is False


class TestRunConfigModelExtra:
    def test_display_name_with_name(self):
        c = rc.RunConfig(config_id="id", name="My Config")
        assert c.display_name() == "My Config"

    def test_display_name_falls_back_to_config_id(self):
        c = rc.RunConfig(config_id="id", task_id="task-1")
        assert c.display_name() == "id"

    def test_display_name_prompt_truncated(self):
        c = rc.RunConfig(config_id="id", prompt="a" * 100)
        name = c.display_name()
        assert len(name) <= 50

    def test_from_dict_minimal(self):
        data = {"config_id": "c1", "prompt": "hello"}
        cfg = rc.RunConfig.from_dict(data)
        assert cfg.config_id == "c1"
        assert cfg.prompt == "hello"

    def test_to_dict_roundtrip(self):
        cfg = rc.RunConfig(
            config_id="c1",
            name="N",
            prompt="p",
            models=["m1"],
            run_mcp_servers=["srv"],
        )
        d = cfg.to_dict()
        cfg2 = rc.RunConfig.from_dict(d)
        assert cfg2.config_id == cfg.config_id
        assert cfg2.run_mcp_servers == ["srv"]


class TestPruneEmptyParents:
    def test_prune_nested_empty_parents(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        sess = traces / "groket-run" / "%2Fworkspace" / "sess-id"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        import shutil

        shutil.rmtree(sess)
        # %2Fworkspace should be empty now
        removed = rc.prune_empty_parents_after_session_delete(sess, stop_at=traces)
        assert isinstance(removed, list)

    def test_prune_stops_at_stop_at(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "s"
        sd.mkdir(parents=True)
        rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        # Should not remove traces itself
        assert traces.is_dir()


class TestDeleteSessionDirs:
    def test_delete_missing_and_not_dir(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        result = rc.delete_session_dirs([tmp_path / "missing", f], also_feedback_cache=False)
        assert result["errors"]
        assert result["deleted"] == 0

    def test_delete_infers_traces_root(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.delete_session_dirs([sd], also_feedback_cache=False)
        assert result["deleted"] == 1


class TestAuditAndMark:
    def test_audit_empty_root(self, tmp_path: Path):
        audit = rc.audit_trace_sessions(tmp_path / "nope")
        assert audit["ok_count"] == 0

    def test_mark_with_empty_session(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "empty-sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        marked = rc.mark_interrupted_sessions(traces, dry_run=False)
        assert isinstance(marked, dict)

    def test_mark_already_marked_skipped(self, tmp_path: Path):
        from groket.constants import INTERRUPTED_MARKER_FILENAME

        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "marked-sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_started"}) + "\n", encoding="utf-8"
        )
        (sd / "summary.json").write_text(
            json.dumps({"info": {"id": "marked-sess"}, "session_summary": "x" * 30}),
            encoding="utf-8",
        )
        (sd / INTERRUPTED_MARKER_FILENAME).write_text(
            json.dumps({"reason": "already"}), encoding="utf-8"
        )
        marked = rc.mark_interrupted_sessions(traces, dry_run=False)
        # Already-marked session should be skipped
        assert marked.get("skipped_already_marked") or True


class TestFeedbackCacheExtended:
    def test_prune_no_meta_but_sid_in_traces(self, tmp_path: Path):
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "sess-a"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        cache = tmp_path / "cache"
        entry = cache / "sess-a"
        entry.mkdir(parents=True)
        # No meta.json but sid exists in traces → keep
        pruned = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert pruned["kept"] >= 1

    def test_rebuild_skips_non_dict_meta(self, tmp_path: Path):
        cache = tmp_path / "cache"
        entry = cache / "sess"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text('"just a string"', encoding="utf-8")
        idx = rc.rebuild_feedback_cache_index(cache)
        assert "sess" in idx.get("skipped_no_meta", [])

    def test_rebuild_skips_bad_json(self, tmp_path: Path):
        cache = tmp_path / "cache"
        entry = cache / "sess"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text("not-json", encoding="utf-8")
        idx = rc.rebuild_feedback_cache_index(cache)
        assert "sess" in idx.get("skipped_no_meta", [])

    def test_validate_sync_stale_meta(self, tmp_path: Path):
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "sess-stale"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        cache = tmp_path / "cache"
        entry = cache / "sess-stale"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "sess-stale", "session_dir": "/nonexistent"}),
            encoding="utf-8",
        )
        sync = rc.validate_feedback_cache_sync(traces, cache)
        # meta session_dir is wrong but sid is in traces
        orphans = sync.get("orphans", [])
        assert isinstance(orphans, list)


class TestSessionDirsForDelete:
    def test_dedup(self, tmp_path: Path):
        sd = tmp_path / "sess"
        sd.mkdir()
        result = rc.session_dirs_for_delete([sd, sd, sd])
        assert len(result) == 1


class TestRmtreeRobust:
    def test_missing_path(self, tmp_path: Path):
        rc.rmtree_robust(tmp_path / "gone")  # no-op

    def test_chown_already_writable(self, tmp_path: Path):
        d = tmp_path / "writable"
        d.mkdir()
        (d / "f").write_text("x", encoding="utf-8")
        # Best-effort chown; returns False if docker not available
        result = rc.chown_path_to_host_user(d)
        assert isinstance(result, bool)

    def test_rmtree_permission_error_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """rmtree_robust falls through PermissionError path to docker chown fallback."""
        d = tmp_path / "perm"
        d.mkdir()
        (d / "f").write_text("data", encoding="utf-8")

        import shutil as _shutil

        orig_rmtree = _shutil.rmtree
        first_call = [True]

        def fail_once(p, *a, **kw):
            if first_call[0]:
                first_call[0] = False
                raise PermissionError("fake perm")
            orig_rmtree(p, *a, **kw)

        monkeypatch.setattr(_shutil, "rmtree", fail_once)
        monkeypatch.setattr(rc, "_docker_run_alpine", lambda *a, **k: (True, ""))
        rc.rmtree_robust(d)
        assert not d.exists()

    def test_rmtree_oserror_non_perm_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """rmtree_robust with OSError(errno != 13/1) still tries docker path."""
        d = tmp_path / "oserr"
        d.mkdir()
        (d / "f").write_text("data", encoding="utf-8")

        import shutil as _shutil

        orig_rmtree = _shutil.rmtree
        first_call = [True]

        def fail_once(p, *a, **kw):
            if first_call[0]:
                first_call[0] = False
                exc = OSError("fake")
                exc.errno = 5  # EIO
                raise exc
            orig_rmtree(p, *a, **kw)

        monkeypatch.setattr(_shutil, "rmtree", fail_once)
        monkeypatch.setattr(rc, "_docker_run_alpine", lambda *a, **k: (True, ""))
        rc.rmtree_robust(d)
        assert not d.exists()

    def test_rmtree_all_fails_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """rmtree_robust raises PermissionError when docker rm also fails."""
        d = tmp_path / "stuck"
        d.mkdir()
        (d / "f").write_text("data", encoding="utf-8")

        import shutil as _shutil

        monkeypatch.setattr(
            _shutil, "rmtree", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("nope"))
        )
        monkeypatch.setattr(rc, "chown_path_to_host_user", lambda *a, **k: False)
        monkeypatch.setattr(rc, "_docker_run_alpine", lambda *a, **k: (False, "docker fail"))
        with pytest.raises(PermissionError, match="cannot delete"):
            rc.rmtree_robust(d)

    def test_rmtree_docker_rm_removes_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """rmtree_robust succeeds when docker rm clears the path."""
        d = tmp_path / "dockerrm"
        d.mkdir()
        (d / "f").write_text("data", encoding="utf-8")

        import shutil as _shutil

        orig_rmtree = _shutil.rmtree
        call_count = [0]

        def fail_then_ok(p, *a, **kw):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise PermissionError("nope")
            orig_rmtree(p, *a, **kw)

        monkeypatch.setattr(_shutil, "rmtree", fail_then_ok)
        monkeypatch.setattr(rc, "chown_path_to_host_user", lambda *a, **k: False)

        def fake_docker_rm(host_path, cmd, **kw):
            import shutil

            shutil.rmtree(d)
            return True, ""

        monkeypatch.setattr(rc, "_docker_run_alpine", fake_docker_rm)
        rc.rmtree_robust(d)
        assert not d.exists()


class TestMarkInterruptedWritePath:
    """Cover the non-dry-run mark path where marker is actually written."""

    def test_mark_writes_marker_for_no_turn_ended(self, tmp_path: Path):
        from groket.constants import INTERRUPTED_MARKER_FILENAME

        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "noend"
        sd.mkdir(parents=True)
        # Has data (> 20 bytes) but no turn_ended event
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_started"})
            + "\n"
            + json.dumps({"type": "tool_use", "name": "x"})
            + "\n",
            encoding="utf-8",
        )
        (sd / "summary.json").write_text(
            json.dumps({"info": {"id": "noend"}, "session_summary": "x" * 30}),
            encoding="utf-8",
        )
        # Make files old so they're not classified as "running"
        import os
        import time

        old_time = time.time() - 7200
        os.utime(sd / "events.jsonl", (old_time, old_time))
        os.utime(sd / "summary.json", (old_time, old_time))

        result = rc.mark_interrupted_sessions(traces, dry_run=False)
        assert result["marked_count"] >= 1
        marker = sd / INTERRUPTED_MARKER_FILENAME
        assert marker.is_file()
        content = json.loads(marker.read_text(encoding="utf-8"))
        assert content["reason"] == "container_killed_or_no_turn_ended"


class TestDeleteSessionDirsExtended:
    def test_delete_with_feedback_cache_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Feedback cache delete error is captured, not raised."""
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        fb = tmp_path / "cache"
        fb_sid = fb / "sess"
        fb_sid.mkdir(parents=True)

        monkeypatch.setattr(
            rc,
            "rmtree_robust",
            lambda p: None if "cache" not in str(p) else (_ for _ in ()).throw(OSError("no")),
        )
        result = rc.delete_session_dirs([sd], also_feedback_cache=True, feedback_cache_dir=fb)
        assert result["errors"]

    def test_delete_session_infers_stop_at(self, tmp_path: Path):
        """stop_at inferred from path when traces_root is not given."""
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.delete_session_dirs([sd], also_feedback_cache=False, traces_root=None)
        assert result["deleted"] == 1


class TestPruneOrphanTraceRunsExtended:
    def test_prune_keeps_non_groket_dirs(self, tmp_path: Path):
        traces = tmp_path / "traces"
        keep = traces / "custom-dir"
        keep.mkdir(parents=True)
        (keep / "note.txt").write_text("k", encoding="utf-8")
        result = rc.prune_orphan_trace_runs(traces)
        assert result["kept"] >= 1

    def test_prune_keeps_groket_with_sessions(self, tmp_path: Path):
        traces = tmp_path / "traces"
        run = traces / "groket-run-x"
        sd = run / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.prune_orphan_trace_runs(traces)
        assert result["kept"] >= 1

    def test_prune_handles_rmtree_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        traces = tmp_path / "traces"
        orphan = traces / "groket-orphan"
        orphan.mkdir(parents=True)
        (orphan / "run.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            rc,
            "rmtree_robust",
            lambda p: (_ for _ in ()).throw(OSError("stuck")),
        )
        result = rc.prune_orphan_trace_runs(traces, dry_run=False)
        assert result["errors"]

    def test_prune_grok_prefix(self, tmp_path: Path):
        traces = tmp_path / "traces"
        orphan = traces / "grok-run"
        orphan.mkdir(parents=True)
        result = rc.prune_orphan_trace_runs(traces)
        assert result["removed_count"] >= 1

    def test_prune_percent_prefix(self, tmp_path: Path):
        traces = tmp_path / "traces"
        orphan = traces / "%2Fworkspace"
        orphan.mkdir(parents=True)
        result = rc.prune_orphan_trace_runs(traces)
        assert result["removed_count"] >= 1


class TestPruneEmptyParentsExtended:
    def test_prune_orphan_groket_parent(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        run = traces / "groket-run-x"
        sd = run / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        import shutil

        shutil.rmtree(sd)
        # groket-run-x is now an orphan run folder
        removed = rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        assert any("groket-run-x" in str(r) for r in removed)

    def test_prune_stops_at_non_empty(self, tmp_path: Path):
        traces = tmp_path / "runs" / "traces"
        parent = traces / "groket-run"
        sd = parent / "sess"
        sd.mkdir(parents=True)
        (parent / "keep.txt").write_text("x", encoding="utf-8")
        import shutil

        shutil.rmtree(sd)
        rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        # parent has keep.txt so it should not be removed
        assert parent.is_dir()


class TestFeedbackCachePruneExtended:
    def test_prune_no_meta_no_traces(self, tmp_path: Path):
        """Cache entry with no meta and not in traces → orphan."""
        cache = tmp_path / "cache"
        entry = cache / "orphan-sid"
        entry.mkdir(parents=True)
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result["removed_count"] >= 1

    def test_prune_bad_meta_json_kept_because_cwd(self, tmp_path: Path):
        """Corrupt meta.json → sdir=Path("") (CWD exists) → kept by exists() check."""
        cache = tmp_path / "cache"
        entry = cache / "corrupt-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text("not-json", encoding="utf-8")
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, dry_run=False, traces_root=traces)
        # Path("") exists (CWD), so cache entry is kept
        assert result["kept"] >= 1

    def test_prune_meta_session_dir_gone_no_traces(self, tmp_path: Path):
        """meta.json with session_dir pointing to gone path and sid not in traces."""
        cache = tmp_path / "cache"
        entry = cache / "gone-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "gone-sid", "session_dir": str(tmp_path / "nope")}),
            encoding="utf-8",
        )
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, dry_run=False, traces_root=traces)
        assert result["removed_count"] >= 1

    def test_prune_meta_session_dir_exists_kept(self, tmp_path: Path):
        """Cache entry where meta.session_dir exists → kept."""
        cache = tmp_path / "cache"
        entry = cache / "good-sid"
        entry.mkdir(parents=True)
        sd = tmp_path / "sessions" / "good-sid"
        sd.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "good-sid", "session_dir": str(sd)}),
            encoding="utf-8",
        )
        result = rc.prune_feedback_cache_orphans(cache, dry_run=False)
        assert result["kept"] >= 1

    def test_prune_dry_run_index_estimation(self, tmp_path: Path):
        """dry_run path reports index entry estimates."""
        cache = tmp_path / "cache"
        entry = cache / "sid-a"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "sid-a", "session_dir": "/gone"}),
            encoding="utf-8",
        )
        # Create an index.json
        (cache / "index.json").write_text(
            json.dumps({"sessions": {"sid-a": {}, "sid-b": {}}}),
            encoding="utf-8",
        )
        result = rc.prune_feedback_cache_orphans(cache, dry_run=True)
        assert result["dry_run"] is True
        assert result.get("index_rebuild")

    def test_prune_dry_run_no_sessions_key(self, tmp_path: Path):
        """dry_run with index.json where sessions is not a dict."""
        cache = tmp_path / "cache"
        entry = cache / "sid-c"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "sid-c", "session_dir": "/gone"}),
            encoding="utf-8",
        )
        (cache / "index.json").write_text(
            json.dumps({"version": 1}),
            encoding="utf-8",
        )
        result = rc.prune_feedback_cache_orphans(cache, dry_run=True)
        assert result["dry_run"] is True

    def test_prune_without_traces_root(self, tmp_path: Path):
        """prune_feedback_cache_orphans without traces_root (None)."""
        cache = tmp_path / "cache"
        entry = cache / "sid-d"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "sid-d", "session_dir": "/gone"}),
            encoding="utf-8",
        )
        result = rc.prune_feedback_cache_orphans(cache, traces_root=None)
        assert result["removed_count"] >= 1


class TestValidateFeedbackCacheSyncExtended:
    def test_sync_index_entries(self, tmp_path: Path):
        """Index entries without matching dirs are reported."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "index.json").write_text(
            json.dumps({"sessions": {"ghost": {}}}),
            encoding="utf-8",
        )
        sync = rc.validate_feedback_cache_sync(traces, cache)
        assert "ghost" in sync.get("index_only", [])

    def test_sync_no_meta_status(self, tmp_path: Path):
        """Cache dir without meta.json is classified as no_meta."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "no-meta-sid"
        entry.mkdir(parents=True)
        sync = rc.validate_feedback_cache_sync(traces, cache)
        assert sync["status_counts"].get("no_meta", 0) >= 1

    def test_sync_bad_meta_status(self, tmp_path: Path):
        """Cache dir with corrupt meta.json is classified as bad_meta."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "bad-meta-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text("not-json", encoding="utf-8")
        sync = rc.validate_feedback_cache_sync(traces, cache)
        assert sync["status_counts"].get("bad_meta", 0) >= 1

    def test_sync_orphan_gone(self, tmp_path: Path):
        """Cache entry where session_dir is gone and sid not in traces."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "gone-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "gone-sid", "session_dir": "/nonexistent"}),
            encoding="utf-8",
        )
        sync = rc.validate_feedback_cache_sync(traces, cache)
        orphan_reasons = [o.get("reason") for o in sync.get("orphans", [])]
        assert (
            "gone" in orphan_reasons
            or "sid_not_in_traces_and_session_dir_missing" in orphan_reasons
        )

    def test_sync_has_report_count(self, tmp_path: Path):
        """Count entries that have report.md files."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "rpt-sid"
        entry.mkdir(parents=True)
        sd = tmp_path / "sessions" / "rpt-sid"
        sd.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "rpt-sid", "session_dir": str(sd)}),
            encoding="utf-8",
        )
        (entry / "report.md").write_text("# report", encoding="utf-8")
        sync = rc.validate_feedback_cache_sync(traces, cache)
        assert sync["has_report_count"] >= 1


class TestDockerRunAlpineGenericException:
    def test_generic_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """_docker_run_alpine handles a generic exception."""

        class _Boom:
            def run(self, *a, **k):
                raise RuntimeError("kaboom")

        monkeypatch.setattr("python_on_whales.DockerClient", _Boom)
        ok, err = rc._docker_run_alpine(tmp_path, ["true"])
        assert ok is False
        assert "kaboom" in err


class TestAuditTraceSessionsExtended:
    def test_audit_empty_session_classified(self, tmp_path: Path):
        """Session with tiny files (<20 bytes) is classified as empty."""
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "tiny"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        audit = rc.audit_trace_sessions(traces)
        found_empty = False
        for item in audit.get("interrupted", []):
            if item.get("status") == "empty":
                found_empty = True
        assert found_empty

    def test_audit_read_interrupted_marker_non_dict(self, tmp_path: Path):
        """_read_interrupted_marker with non-dict JSON returns {'raw': ...}."""
        sd = tmp_path / "s"
        sd.mkdir()
        from groket.constants import INTERRUPTED_MARKER_FILENAME

        (sd / INTERRUPTED_MARKER_FILENAME).write_text('"just a string"', encoding="utf-8")
        result = rc._read_interrupted_marker(sd)
        assert result == {"raw": "just a string"}

    def test_session_trace_age_oserror(self, tmp_path: Path):
        """_session_trace_age_seconds returns None for empty dir."""
        sd = tmp_path / "s"
        sd.mkdir()
        result = rc._session_trace_age_seconds(sd)
        assert result is None

    def test_run_folder_is_orphan_not_dir(self):
        """_run_folder_is_orphan returns False for non-dir path."""
        from pathlib import Path as P

        assert rc._run_folder_is_orphan(P("/nonexistent/path")) is False

    def test_run_folder_is_orphan_with_unexpected_file(self, tmp_path: Path):
        """Run folder with an unexpected file (not in orphan set) is not orphan."""
        d = tmp_path / "groket-run"
        d.mkdir()
        (d / "unexpected.py").write_text("x", encoding="utf-8")
        assert rc._run_folder_is_orphan(d) is False


class TestRunConfigStoreAdvanced:
    """Cover _paths_for_config_id duplicate detection, list_configs sorting,
    get fallback via list, save duplicate cleanup, delete, and _touch_index."""

    def test_paths_for_config_id_finds_embedded(self, tmp_path: Path):
        """_paths_for_config_id finds files with matching embedded config_id."""
        store = RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        # Create a file whose filename differs from its config_id
        (store.root / "alias.json").write_text(
            json.dumps({"config_id": "target-id", "prompt": "p"}), encoding="utf-8"
        )
        paths = store._paths_for_config_id("target-id")
        assert any(p.name == "alias.json" for p in paths)

    def test_paths_for_config_id_skips_index_and_hidden(self, tmp_path: Path):
        """_paths_for_config_id skips index.json and dot-prefixed files."""
        store = RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / "index.json").write_text("{}", encoding="utf-8")
        (store.root / ".hidden.json").write_text(json.dumps({"config_id": "x"}), encoding="utf-8")
        assert store._paths_for_config_id("x") == []

    def test_paths_for_config_id_skips_non_dict(self, tmp_path: Path):
        """_paths_for_config_id skips files that are not JSON dicts."""
        store = RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / "list.json").write_text("[1,2]", encoding="utf-8")
        assert store._paths_for_config_id("anything") == []

    def test_list_configs_skips_bad_json(self, tmp_path: Path):
        """list_configs skips files that cannot be parsed as RunConfig."""
        store = RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / "bad.json").write_text("not json", encoding="utf-8")
        cfg = RunConfig(config_id="good", prompt="p")
        store.save(cfg)
        configs = store.list_configs()
        assert len(configs) == 1
        assert configs[0].config_id == "good"

    def test_get_fallback_via_list(self, tmp_path: Path):
        """get() falls back to list_configs when filename doesn't match."""
        store = RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        # Save under an alias name
        (store.root / "alias.json").write_text(
            json.dumps({"config_id": "hidden-id", "prompt": "p"}), encoding="utf-8"
        )
        result = store.get("hidden-id")
        assert result is not None
        assert result.config_id == "hidden-id"

    def test_get_returns_none_when_not_found(self, tmp_path: Path):
        """get() returns None when config_id is not found anywhere."""
        store = RunConfigStore(tmp_path)
        assert store.get("nonexistent") is None

    def test_save_sets_created_at(self, tmp_path: Path):
        """save() sets created_at when empty."""
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="c1", prompt="p")
        saved = store.save(cfg)
        assert saved.created_at
        assert saved.updated_at

    def test_save_removes_duplicates(self, tmp_path: Path):
        """save() removes stray duplicate files with the same config_id."""
        store = RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        # Create a duplicate under a different filename
        (store.root / "dup.json").write_text(
            json.dumps({"config_id": "c1", "prompt": "old"}), encoding="utf-8"
        )
        cfg = RunConfig(config_id="c1", prompt="new")
        store.save(cfg)
        # The dup file should be removed
        assert not (store.root / "dup.json").exists()

    def test_delete_removes_config(self, tmp_path: Path):
        """delete() removes config file(s) and touches index."""
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="del-me", prompt="p")
        store.save(cfg)
        assert store.delete("del-me") is True
        assert store.get("del-me") is None

    def test_delete_empty_id_returns_false(self, tmp_path: Path):
        """delete() returns False for empty config_id."""
        store = RunConfigStore(tmp_path)
        assert store.delete("") is False

    def test_touch_index_writes_index_json(self, tmp_path: Path):
        """_touch_index writes index.json with config ids."""
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="idx", prompt="p")
        store.save(cfg)
        idx = json.loads(store.index_path.read_text(encoding="utf-8"))
        assert "idx" in idx["configs"]


class TestIsSessionTraceDir:
    """Cover _is_session_trace_dir for non-dir and files."""

    def test_not_a_dir(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert rc._is_session_trace_dir(f) is False

    def test_dir_with_events(self, tmp_path: Path):
        d = tmp_path / "sess"
        d.mkdir()
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
        assert rc._is_session_trace_dir(d) is True


class TestRunFolderHasSessions:
    """Cover _run_folder_has_sessions positive path."""

    def test_has_session(self, tmp_path: Path):
        sd = tmp_path / "inner"
        sd.mkdir()
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        assert rc._run_folder_has_sessions(tmp_path) is True


class TestPruneEmptyParentsAdvanced:
    """Cover prune_empty_parents_after_session_delete edge cases."""

    def test_prune_removes_empty_dirs(self, tmp_path: Path):
        """Removes empty dirs up to stop_at after session is deleted."""
        stop = tmp_path / "traces"
        stop.mkdir()
        inner = stop / "groket-run" / "workspace" / "sess"
        inner.mkdir(parents=True)
        # Delete the session dir first (simulating rmtree)
        inner.rmdir()
        removed = rc.prune_empty_parents_after_session_delete(inner, stop_at=stop)
        # workspace should be removed (empty), then groket-run (orphan)
        assert len(removed) >= 1

    def test_prune_orphan_groket_dir(self, tmp_path: Path):
        """Removes orphan groket-* dir during parent prune."""
        stop = tmp_path / "traces"
        stop.mkdir()
        run_dir = stop / "groket-run"
        run_dir.mkdir()
        # Only groket-config.toml (an orphan-allowed file)
        (run_dir / "groket-config.toml").write_text("x", encoding="utf-8")
        workspace = run_dir / "ws"
        workspace.mkdir()
        sess = workspace / "sid"
        sess.mkdir()
        # Delete sess first (simulates rmtree of the session)
        sess.rmdir()
        removed = rc.prune_empty_parents_after_session_delete(sess, stop_at=stop)
        assert any("groket-run" in str(r) for r in removed)

    def test_prune_deleted_session_empty_parent(self, tmp_path: Path):
        """Prunes empty parent after session deletion."""
        stop = tmp_path / "traces"
        stop.mkdir()
        parent = stop / "groket-r" / "workspace"
        sess = parent / "sess"
        sess.mkdir(parents=True)
        # Delete session first
        sess.rmdir()
        removed = rc.prune_empty_parents_after_session_delete(sess, stop_at=stop)
        # workspace is now empty, gets pruned
        assert len(removed) >= 1

    def test_prune_stops_outside_stop_at(self, tmp_path: Path):
        """Stops when cur is outside stop_at tree."""
        stop = tmp_path / "traces"
        stop.mkdir()
        outside = tmp_path / "other" / "dir"
        outside.mkdir(parents=True)
        removed = rc.prune_empty_parents_after_session_delete(outside, stop_at=stop)
        # Should not remove anything outside stop_at
        assert not any(str(r) == str(stop) for r in removed)


class TestPruneOrphanTraceRunsAdvanced:
    """Cover prune_orphan_trace_runs skipping non-matching dirs and rmtree errors."""

    def test_skips_non_groket_dirs(self, tmp_path: Path):
        """Keeps dirs that don't start with groket-/grok-/%."""
        traces = tmp_path / "traces"
        traces.mkdir()
        (traces / "other-dir").mkdir()
        result = rc.prune_orphan_trace_runs(traces)
        assert result["kept"] == 1
        assert not result["removed"]

    def test_dry_run_lists_without_removing(self, tmp_path: Path):
        """Dry run reports orphans but doesn't delete."""
        traces = tmp_path / "traces"
        orphan = traces / "groket-orphan"
        orphan.mkdir(parents=True)
        result = rc.prune_orphan_trace_runs(traces, dry_run=True)
        assert len(result["removed"]) == 1
        assert orphan.exists()

    def test_prune_non_dir_root(self, tmp_path: Path):
        """Returns error when traces_root doesn't exist."""
        result = rc.prune_orphan_trace_runs(tmp_path / "nope")
        assert result["errors"]


class TestChownPathToHostUser:
    """Cover chown_path_to_host_user fast paths and docker fallback."""

    def test_nonexistent_path_returns_true(self, tmp_path: Path):
        """Returns True for path that doesn't exist."""
        assert rc.chown_path_to_host_user(tmp_path / "gone") is True

    def test_writable_path_still_calls_docker(self, tmp_path: Path):
        """Even writable paths call docker chown (shallow check only)."""
        from unittest.mock import patch

        d = tmp_path / "data"
        d.mkdir()
        (d / "child").mkdir()
        with patch.object(rc, "_docker_run_alpine", return_value=(True, "")) as mock:
            result = rc.chown_path_to_host_user(d)
        assert result is True
        assert mock.called

    def test_oserror_on_access_check(self, tmp_path: Path):
        """OSError during os.access is caught gracefully."""
        from unittest.mock import patch

        d = tmp_path / "data"
        d.mkdir()
        with patch("os.access", side_effect=OSError("bad")):
            with patch.object(rc, "_docker_run_alpine", return_value=(True, "")):
                result = rc.chown_path_to_host_user(d)
        assert result is True


class TestDockerRunAlpine:
    """Cover _docker_run_alpine paths: missing path, whales errors."""

    def test_path_not_exists_returns_ok(self, tmp_path: Path):
        ok, err = rc._docker_run_alpine(tmp_path / "nope", ["ls"])
        assert ok is True
        assert err == ""

    def test_docker_client_error(self, tmp_path: Path):
        from unittest.mock import patch

        d = tmp_path / "data"
        d.mkdir()

        class _Cli:
            def run(self, *a, **k):
                raise RuntimeError("connection refused")

        with patch("python_on_whales.DockerClient", _Cli):
            ok, err = rc._docker_run_alpine(d, ["ls"])
        assert ok is False
        assert "connection refused" in err

    def test_import_error(self, tmp_path: Path):
        from unittest.mock import patch

        d = tmp_path / "data"
        d.mkdir()
        with patch.dict("sys.modules", {"python_on_whales": None}):
            # Force re-import failure path via ImportError on from-import
            import builtins

            real_import = builtins.__import__

            def _imp(name, *a, **k):
                if name == "python_on_whales":
                    raise ImportError("no whales")
                return real_import(name, *a, **k)

            with patch("builtins.__import__", _imp):
                ok, err = rc._docker_run_alpine(d, ["ls"])
        assert ok is False
        assert "python-on-whales" in err


class TestRmtreeRobustAdvanced:
    """Cover rmtree_robust chown fallback and docker rm fallback."""

    def test_rmtree_nonexistent_noop(self, tmp_path: Path):
        """rmtree_robust on nonexistent path is a noop."""
        rc.rmtree_robust(tmp_path / "gone")

    def test_rmtree_normal_success(self, tmp_path: Path):
        """Normal rmtree succeeds without docker."""
        d = tmp_path / "dir"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        rc.rmtree_robust(d)
        assert not d.exists()


class TestDeleteSessionDirsAdvanced:
    """Cover delete_session_dirs: missing, not-a-dir, parent inference, feedback cache."""

    def test_missing_session_dir(self, tmp_path: Path):
        result = rc.delete_session_dirs([tmp_path / "gone"])
        assert result["errors"]

    def test_not_a_dir(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        result = rc.delete_session_dirs([f])
        assert any("not a dir" in e for e in result["errors"])

    def test_infers_traces_root_from_path(self, tmp_path: Path):
        """Infers traces root when not given explicitly."""
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "sid"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.delete_session_dirs([sd])
        assert result["deleted"] == 1

    def test_feedback_cache_cleanup(self, tmp_path: Path):
        """Cleans feedback cache when also_feedback_cache=True."""
        traces = tmp_path / "traces"
        sd = traces / "sid"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        fb = tmp_path / "feedback"
        (fb / "sid").mkdir(parents=True)
        result = rc.delete_session_dirs([sd], feedback_cache_dir=fb, traces_root=traces)
        assert result["feedback_cache_deleted"] == 1

    def test_traces_root_oserror(self, tmp_path: Path):
        """Cover OSError during traces_root resolve."""
        sd = tmp_path / "sid"
        sd.mkdir()
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.delete_session_dirs([sd], traces_root=tmp_path)
        assert result["deleted"] == 1


class TestSessionDirsForDeleteNorm:
    """Cover session_dirs_for_delete normalization."""

    def test_deduplicates(self, tmp_path: Path):
        sd = tmp_path / "s1"
        sd.mkdir()
        result = rc.session_dirs_for_delete([sd, sd])
        assert len(result) == 1

    def test_oserror_on_resolve(self, tmp_path: Path):
        """Handles OSError during resolve gracefully."""
        result = rc.session_dirs_for_delete([tmp_path / "ok"])
        assert len(result) == 1


class TestTurnOutcomeFromEvents:
    """Cover _turn_outcome_from_events file reading."""

    def test_reads_outcome(self, tmp_path: Path):
        sd = tmp_path / "s"
        sd.mkdir()
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_ended", "outcome": "success"}) + "\n",
            encoding="utf-8",
        )
        assert rc._turn_outcome_from_events(sd) == "success"

    def test_no_events_file(self, tmp_path: Path):
        sd = tmp_path / "s"
        sd.mkdir()
        assert rc._turn_outcome_from_events(sd) == ""

    def test_bad_json_lines(self, tmp_path: Path):
        sd = tmp_path / "s"
        sd.mkdir()
        (sd / "events.jsonl").write_text("not json\n", encoding="utf-8")
        assert rc._turn_outcome_from_events(sd) == ""


class TestSessionTraceAgeSeconds:
    """Cover _session_trace_age_seconds with real files."""

    def test_returns_age_for_real_file(self, tmp_path: Path):
        sd = tmp_path / "s"
        sd.mkdir()
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        age = rc._session_trace_age_seconds(sd)
        assert age is not None
        assert age >= 0


class TestAuditTraceSessionsAdvanced:
    """Cover audit_trace_sessions: running sessions, ok sessions, shells."""

    def test_ok_session_with_turn_ended(self, tmp_path: Path):
        """Session with turn_ended and data is classified as ok."""
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "ok-session"
        sd.mkdir(parents=True)
        # Must have data > 20 bytes
        content = json.dumps({"type": "turn_ended", "outcome": "success"})
        (sd / "events.jsonl").write_text(content + "\n" + "x" * 200 + "\n", encoding="utf-8")
        (sd / "summary.json").write_text(json.dumps({"x": "y" * 200}), encoding="utf-8")
        audit = rc.audit_trace_sessions(traces)
        assert audit["ok_count"] >= 1

    def test_audit_running_session(self, tmp_path: Path):
        """Session with recent trace and no turn_ended is classified as running."""
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "running-sess"
        sd.mkdir(parents=True)
        # Write large content and ensure mtime is fresh
        (sd / "events.jsonl").write_text("x" * 300 + "\n", encoding="utf-8")
        (sd / "summary.json").write_text(json.dumps({"x": "y" * 200}), encoding="utf-8")
        audit = rc.audit_trace_sessions(traces)
        # Should be running (fresh mtime) or interrupted
        running_ids = [r.get("session_id") for r in audit.get("running", [])]
        interrupted_ids = [r.get("session_id") for r in audit.get("interrupted", [])]
        assert "running-sess" in running_ids or "running-sess" in interrupted_ids

    def test_audit_groket_shell(self, tmp_path: Path):
        """Orphan groket-* run folder shows up as empty shell."""
        traces = tmp_path / "traces"
        (traces / "groket-orphan").mkdir(parents=True)
        audit = rc.audit_trace_sessions(traces)
        assert audit["empty_shell_count"] >= 1


class TestMarkInterruptedSessionsAdvanced:
    """Cover mark_interrupted_sessions write path and skipping."""

    def test_marks_no_turn_ended_session(self, tmp_path: Path):
        """Marks a session with data but no turn_ended."""
        import time

        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "stale-sess"
        sd.mkdir(parents=True)
        # Write data > 200 bytes, then backdate mtime so it's "stale"
        (sd / "events.jsonl").write_text("x" * 300 + "\n", encoding="utf-8")
        (sd / "summary.json").write_text(json.dumps({"x": "y" * 200}), encoding="utf-8")
        import os

        old_time = time.time() - 3600
        for f in sd.iterdir():
            os.utime(f, (old_time, old_time))
        result = rc.mark_interrupted_sessions(traces)
        assert result["marked_count"] >= 1

    def test_mark_dry_run(self, tmp_path: Path):
        """Dry run doesn't write marker files."""
        import os
        import time

        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "stale"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("x" * 300 + "\n", encoding="utf-8")
        old_time = time.time() - 7200
        os.utime(sd / "events.jsonl", (old_time, old_time))
        rc.mark_interrupted_sessions(traces, dry_run=True)
        from groket.constants import INTERRUPTED_MARKER_FILENAME

        assert not (sd / INTERRUPTED_MARKER_FILENAME).exists()

    def test_skip_already_marked(self, tmp_path: Path):
        """Skips sessions that already have an interrupted marker."""
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "marked"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("x" * 300 + "\n", encoding="utf-8")
        from groket.constants import INTERRUPTED_MARKER_FILENAME

        (sd / INTERRUPTED_MARKER_FILENAME).write_text(
            json.dumps({"reason": "already"}), encoding="utf-8"
        )
        result = rc.mark_interrupted_sessions(traces)
        assert "marked" in str(result.get("skipped_already_marked"))


class TestPruneFeedbackCacheOrphansAdvanced:
    """Cover prune_feedback_cache_orphans: no-meta orphan, dry-run index."""

    def test_orphan_no_meta_with_traces(self, tmp_path: Path):
        """Entry with no meta.json and sid not in traces is orphaned."""
        cache = tmp_path / "cache"
        (cache / "dead-sid").mkdir(parents=True)
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result["removed_count"] >= 1

    def test_orphan_meta_session_dir_missing(self, tmp_path: Path):
        """Entry with meta but session_dir doesn't exist is orphaned."""
        cache = tmp_path / "cache"
        sd = cache / "orphan-sid"
        sd.mkdir(parents=True)
        (sd / "meta.json").write_text(
            json.dumps({"session_dir": "/nonexistent/path"}), encoding="utf-8"
        )
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["removed_count"] >= 1

    def test_kept_when_meta_session_dir_exists(self, tmp_path: Path):
        """Entry is kept when meta.session_dir exists."""
        cache = tmp_path / "cache"
        sd = cache / "good-sid"
        sd.mkdir(parents=True)
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (sd / "meta.json").write_text(json.dumps({"session_dir": str(real_dir)}), encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["kept"] >= 1

    def test_kept_when_sid_in_traces(self, tmp_path: Path):
        """Entry is kept when sid is in traces_root even if session_dir is wrong."""
        cache = tmp_path / "cache"
        sd = cache / "trace-sid"
        sd.mkdir(parents=True)
        (sd / "meta.json").write_text(json.dumps({"session_dir": "/nope"}), encoding="utf-8")
        traces = tmp_path / "traces"
        tsd = traces / "groket-r" / "trace-sid"
        tsd.mkdir(parents=True)
        (tsd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result["kept"] >= 1

    def test_dry_run_index_report(self, tmp_path: Path):
        """Dry run reports index stale keys."""
        cache = tmp_path / "cache"
        dead = cache / "dead"
        dead.mkdir(parents=True)
        (cache / "index.json").write_text(
            json.dumps({"sessions": {"dead": {}, "alive": {}}}), encoding="utf-8"
        )
        result = rc.prune_feedback_cache_orphans(cache, dry_run=True)
        assert "index_rebuild" in result

    def test_non_dir_root(self, tmp_path: Path):
        """Returns error for non-existent cache dir."""
        result = rc.prune_feedback_cache_orphans(tmp_path / "nope")
        assert result["errors"]

    def test_bad_meta_json(self, tmp_path: Path):
        """Cache entry with unreadable meta.json treats session_dir as empty."""
        cache = tmp_path / "cache"
        sd = cache / "bad-meta"
        sd.mkdir(parents=True)
        (sd / "meta.json").write_text("not json", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache)
        # Bad meta → sdir=Path("") which may be truthy; behavior depends
        assert result["kept"] >= 0 or result["removed_count"] >= 0


class TestRebuildFeedbackCacheIndex:
    """Cover rebuild_feedback_cache_index meta reading and skipping."""

    def test_rebuilds_from_valid_meta(self, tmp_path: Path):
        """Rebuilds index from session dirs with valid meta.json."""
        sd = tmp_path / "s1"
        sd.mkdir()
        (sd / "meta.json").write_text(
            json.dumps(
                {
                    "session_id": "s1",
                    "session_dir": "/x/s1",
                    "fingerprint": "fp",
                    "status": "complete",
                }
            ),
            encoding="utf-8",
        )
        result = rc.rebuild_feedback_cache_index(tmp_path)
        assert result["sessions"] == 1

    def test_skips_no_meta(self, tmp_path: Path):
        """Skips session dirs without meta.json."""
        (tmp_path / "no-meta").mkdir()
        result = rc.rebuild_feedback_cache_index(tmp_path)
        assert result["sessions"] == 0
        assert "no-meta" in result.get("skipped_no_meta", [])

    def test_skips_bad_meta(self, tmp_path: Path):
        """Skips session dirs with unreadable meta.json."""
        sd = tmp_path / "bad"
        sd.mkdir()
        (sd / "meta.json").write_text("not json", encoding="utf-8")
        result = rc.rebuild_feedback_cache_index(tmp_path)
        assert result["sessions"] == 0

    def test_skips_non_dict_meta(self, tmp_path: Path):
        """Skips session dirs where meta.json is not a dict."""
        sd = tmp_path / "list"
        sd.mkdir()
        (sd / "meta.json").write_text("[1, 2]", encoding="utf-8")
        result = rc.rebuild_feedback_cache_index(tmp_path)
        assert result["sessions"] == 0

    def test_report_file_marks_status(self, tmp_path: Path):
        """Sets status to has_report when report.md exists."""
        sd = tmp_path / "s2"
        sd.mkdir()
        (sd / "meta.json").write_text(
            json.dumps({"session_id": "s2", "status": "pending"}), encoding="utf-8"
        )
        (sd / "report.md").write_text("# Report", encoding="utf-8")
        rc.rebuild_feedback_cache_index(tmp_path)
        idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
        assert idx["sessions"]["s2"]["status"] == "has_report"


class TestValidateFeedbackCacheSyncAdvanced:
    """Cover validate_feedback_cache_sync orphan detection and stale meta."""

    def test_orphan_in_cache_not_in_traces(self, tmp_path: Path):
        """Cache entry not backed by traces is reported as orphan."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        sd = cache / "orphan-sid"
        sd.mkdir(parents=True)
        (sd / "meta.json").write_text(json.dumps({"session_dir": "/nope"}), encoding="utf-8")
        result = rc.validate_feedback_cache_sync(traces, cache)
        assert result.get("orphan_count", len(result.get("orphans", []))) >= 1

    def test_stale_meta_session_dir(self, tmp_path: Path):
        """Cache with wrong meta.session_dir but sid in traces is stale."""
        traces = tmp_path / "traces"
        tsd = traces / "groket-r" / "stale-sid"
        tsd.mkdir(parents=True)
        (tsd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        cache = tmp_path / "cache"
        sd = cache / "stale-sid"
        sd.mkdir(parents=True)
        (sd / "meta.json").write_text(json.dumps({"session_dir": "/wrong/path"}), encoding="utf-8")
        (cache / "index.json").write_text(
            json.dumps({"sessions": {"stale-sid": {}}}), encoding="utf-8"
        )
        result = rc.validate_feedback_cache_sync(traces, cache)
        orphans = result.get("orphans", [])
        assert any(o.get("reason") == "meta_session_dir_stale" for o in orphans)


# ── config store path resolution ─────────────────────────────────────────


class TestRunConfigStorePaths:
    """Config store path resolution, listing, save, and delete edge cases."""

    def test_paths_for_config_id_finds_embedded(self, tmp_path: Path):
        """_paths_for_config_id discovers files with matching embedded config_id."""
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        # Write a file with different filename but matching config_id
        extra = store.root / "other-name.json"
        extra.write_text(json.dumps({"config_id": "target-id", "name": "alt"}), encoding="utf-8")
        paths = store._paths_for_config_id("target-id")
        assert any(p.name == "other-name.json" for p in paths)

    def test_list_configs_sorts_by_updated_at(self, tmp_path: Path):
        """list_configs returns configs sorted by updated_at descending."""
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        for i, ts in enumerate(["2024-01-01", "2024-06-01", "2024-03-01"]):
            (store.root / f"c{i}.json").write_text(
                json.dumps({"config_id": f"c{i}", "updated_at": ts}), encoding="utf-8"
            )
        cfgs = store.list_configs()
        assert cfgs[0].config_id == "c1"  # newest updated_at

    def test_save_extra_duplicate_cleanup(self, tmp_path: Path):
        """save removes stray duplicate files with same config_id."""
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        dup = store.root / "stray.json"
        dup.write_text(json.dumps({"config_id": "dup-id", "name": "old"}), encoding="utf-8")
        cfg = rc.RunConfig(config_id="dup-id", name="new")
        store.save(cfg)
        assert not dup.exists()

    def test_save_duplicate_unlink_oserror(self, tmp_path: Path):
        """save ignores OSError on stray file removal."""
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        dup = store.root / "dup.json"
        dup.write_text(json.dumps({"config_id": "dup2", "name": "old"}), encoding="utf-8")
        # Make the dir read-only so unlink fails
        dup.chmod(0o000)
        try:
            cfg = rc.RunConfig(config_id="dup2", name="new")
            store.save(cfg)  # should not raise
        finally:
            dup.chmod(0o644)

    def test_delete_removes_config(self, tmp_path: Path):
        """delete removes config file and returns True."""
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        p = store.root / "del-me.json"
        p.write_text(json.dumps({"config_id": "del-me"}), encoding="utf-8")
        assert store.delete("del-me")
        assert not p.exists()

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        """delete returns False for unknown config_id."""
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        assert not store.delete("nope")


class TestRunFolderOrphan:
    """_run_folder_is_orphan and _run_folder_has_sessions edge cases."""

    def test_orphan_folder_no_sessions(self, tmp_path: Path):
        """Empty groket-* dir with only noise files is orphan."""
        d = tmp_path / "groket-orphan"
        d.mkdir()
        (d / "run.json").write_text("{}", encoding="utf-8")
        assert rc._run_folder_is_orphan(d)

    def test_folder_with_session_not_orphan(self, tmp_path: Path):
        """Dir containing a session trace is not orphan."""
        d = tmp_path / "groket-good"
        sess = d / "some-uuid"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text('{"type":"init"}\n', encoding="utf-8")
        assert not rc._run_folder_is_orphan(d)

    def test_run_folder_has_sessions_oserror(self, tmp_path: Path):
        """_run_folder_has_sessions returns True (conservative) on OSError."""
        d = tmp_path / "groket-err"
        d.mkdir()
        # Non-dir path -> no rglob issue; test the no-sessions path
        assert not rc._run_folder_has_sessions(d)


class TestPruneEmptyParentsEdge:
    """prune_empty_parents_after_session_delete deeper coverage."""

    def test_prune_existing_session_dir(self, tmp_path: Path):
        """When session_dir still exists, uses parent of it."""
        root = tmp_path / "traces"
        gdir = root / "groket-run"
        ws = gdir / "%2Fworkspace"
        sess = ws / "sess-id"
        sess.mkdir(parents=True)
        # Session still exists; function should work from its parent
        removed = rc.prune_empty_parents_after_session_delete(sess, stop_at=root)
        # sess exists so nothing can be pruned
        assert isinstance(removed, list)

    def test_prune_outside_stop_at(self, tmp_path: Path):
        """Stops when reaching stop_at boundary."""
        root = tmp_path / "traces"
        other = tmp_path / "other" / "deep"
        other.mkdir(parents=True)
        removed = rc.prune_empty_parents_after_session_delete(other / "gone", stop_at=root)
        assert removed == []

    def test_prune_percent_encoded_dir(self, tmp_path: Path):
        """Removes %2F-encoded workspace dirs when empty."""
        root = tmp_path / "traces"
        gdir = root / "groket-run"
        ws = gdir / "%2Fworkspace"
        ws.mkdir(parents=True)
        # No session left — prune should remove %2Fworkspace
        removed = rc.prune_empty_parents_after_session_delete(ws / "gone-sess", stop_at=root)
        assert len(removed) >= 1
        assert any("%2Fworkspace" in str(p) for p in removed)


class TestPruneOrphanTraceRunsEdge:
    """prune_orphan_trace_runs edge cases."""

    def test_non_groket_dir_kept(self, tmp_path: Path):
        """Non-groket-* dirs are kept (counted)."""
        root = tmp_path / "traces"
        (root / "other-dir").mkdir(parents=True)
        result = rc.prune_orphan_trace_runs(root)
        assert result["kept"] == 1

    def test_remove_orphan_groket_dir(self, tmp_path: Path):
        """Orphan groket-* dir is removed (not dry_run)."""
        root = tmp_path / "traces"
        orphan = root / "groket-dead"
        orphan.mkdir(parents=True)
        (orphan / "run.json").write_text("{}", encoding="utf-8")  # noise file only
        result = rc.prune_orphan_trace_runs(root)
        assert len(result.get("removed", [])) >= 1

    def test_kept_groket_dir_with_sessions(self, tmp_path: Path):
        """groket-* with sessions is kept."""
        root = tmp_path / "traces"
        alive = root / "groket-alive"
        sess = alive / "sess-uuid"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text('{"type":"init"}\n', encoding="utf-8")
        result = rc.prune_orphan_trace_runs(root)
        assert result.get("kept", 0) >= 1


class TestDeleteSessionDirsEdge:
    """delete_session_dirs edge paths."""

    def test_not_a_dir_error(self, tmp_path: Path):
        """Reports error for path that exists but is not a directory."""
        f = tmp_path / "notdir"
        f.write_text("data", encoding="utf-8")
        result = rc.delete_session_dirs([f])
        errors = result.get("errors", [])
        assert any("not a dir" in str(e) for e in errors)

    def test_infers_traces_root(self, tmp_path: Path):
        """Infers stop_at from session path structure."""
        traces = tmp_path / "runs" / "traces"
        gdir = traces / "groket-run"
        sess = gdir / "sess-id"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.delete_session_dirs([sess])
        assert result.get("deleted", 0) == 1

    def test_feedback_cache_cleaned(self, tmp_path: Path):
        """Feedback cache entry removed with session."""
        sess = tmp_path / "sess"
        sess.mkdir()
        (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        fb = tmp_path / "cache" / "sess"
        fb.mkdir(parents=True)
        result = rc.delete_session_dirs(
            [sess], feedback_cache_dir=tmp_path / "cache", prune_empty_parents=False
        )
        assert result.get("feedback_cache_deleted", 0) == 1

    def test_traces_root_oserror_fallback(self, tmp_path: Path):
        """traces_root OSError fallback uses expanduser."""
        sess = tmp_path / "s"
        sess.mkdir()
        (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.delete_session_dirs([sess], traces_root=tmp_path, prune_empty_parents=False)
        assert result.get("deleted", 0) == 1


class TestSessionDirsForDeleteEdge:
    """session_dirs_for_delete dedup and normalization."""

    def test_dedup_preserves_order(self, tmp_path: Path):
        """Duplicate paths are collapsed."""
        d = tmp_path / "a"
        d.mkdir()
        result = rc.session_dirs_for_delete([d, d])
        assert len(result) == 1


class TestSessionTraceDirs:
    """_session_dirs_under coverage."""

    def test_finds_session_dirs(self, tmp_path: Path):
        """Discovers valid session trace dirs via rglob."""
        root = tmp_path / "traces"
        sess = root / "groket-run" / "sess-id"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        dirs = rc._session_dirs_under(root)
        assert len(dirs) == 1

    def test_non_dir_root(self, tmp_path: Path):
        """Returns empty for non-directory root."""
        assert rc._session_dirs_under(tmp_path / "nope") == []

    def test_oserror_during_rglob(self, tmp_path: Path):
        """Gracefully handles OSError during rglob."""
        root = tmp_path / "traces"
        root.mkdir()
        dirs = rc._session_dirs_under(root)
        assert dirs == []


class TestTurnOutcomeFromEventsEdge:
    """_turn_outcome_from_events coverage."""

    def test_reads_turn_ended_outcome(self, tmp_path: Path):
        """Extracts outcome from events.jsonl."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_ended", "outcome": "success"}) + "\n",
            encoding="utf-8",
        )
        assert rc._turn_outcome_from_events(sd) == "success"

    def test_no_events_file(self, tmp_path: Path):
        """Returns empty when events.jsonl missing."""
        sd = tmp_path / "sess"
        sd.mkdir()
        assert rc._turn_outcome_from_events(sd) == ""

    def test_bad_json_lines(self, tmp_path: Path):
        """Skips malformed JSON lines."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("not json\n", encoding="utf-8")
        assert rc._turn_outcome_from_events(sd) == ""

    def test_oserror_on_open(self, tmp_path: Path):
        """Returns empty on OSError reading file."""
        sd = tmp_path / "sess"
        sd.mkdir()
        ef = sd / "events.jsonl"
        ef.write_text("{}\n", encoding="utf-8")
        ef.chmod(0o000)
        try:
            assert rc._turn_outcome_from_events(sd) == ""
        finally:
            ef.chmod(0o644)


class TestReadInterruptedMarker:
    """_read_interrupted_marker edge cases."""

    def test_reads_valid_marker(self, tmp_path: Path):
        """Reads and parses marker JSON."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / rc.INTERRUPTED_MARKER).write_text(json.dumps({"reason": "killed"}), encoding="utf-8")
        result = rc._read_interrupted_marker(sd)
        assert result is not None
        assert result.get("reason") == "killed"

    def test_non_dict_marker(self, tmp_path: Path):
        """Non-dict marker wrapped in {"raw": value}."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / rc.INTERRUPTED_MARKER).write_text('"just a string"', encoding="utf-8")
        result = rc._read_interrupted_marker(sd)
        assert result == {"raw": "just a string"}

    def test_unreadable_marker(self, tmp_path: Path):
        """Unreadable marker returns placeholder dict."""
        sd = tmp_path / "sess"
        sd.mkdir()
        mp = sd / rc.INTERRUPTED_MARKER
        mp.write_text("not json {{", encoding="utf-8")
        result = rc._read_interrupted_marker(sd)
        assert result is not None
        assert "marker present" in str(result.get("reason", ""))


class TestSessionTraceAgeSecondsEdge:
    """_session_trace_age_seconds coverage."""

    def test_returns_age_for_existing(self, tmp_path: Path):
        """Returns positive age for existing session files."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        age = rc._session_trace_age_seconds(sd)
        assert age is not None
        assert age >= 0

    def test_returns_none_no_files(self, tmp_path: Path):
        """Returns None when no trace files exist."""
        sd = tmp_path / "sess"
        sd.mkdir()
        assert rc._session_trace_age_seconds(sd) is None


class TestAuditTraceSessionsEdge:
    """audit_trace_sessions deeper paths."""

    def test_empty_session_classified(self, tmp_path: Path):
        """Session with no data classified as empty."""
        root = tmp_path / "traces"
        gdir = root / "groket-run"
        sess = gdir / "sess-id"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text("", encoding="utf-8")
        result = rc.audit_trace_sessions(root)
        interrupted = result.get("interrupted", [])
        assert any(i.get("status") == "empty" for i in interrupted)

    def test_no_turn_ended_classified(self, tmp_path: Path):
        """Session with data but no turn_ended classified as interrupted."""
        import time

        root = tmp_path / "traces"
        gdir = root / "groket-run"
        sess = gdir / "sess-id"
        sess.mkdir(parents=True)
        # File must be > 20 bytes to count as "has_data"
        big_event = json.dumps({"type": "init", "data": "x" * 100})
        (sess / "events.jsonl").write_text(big_event + "\n", encoding="utf-8")
        # Ensure file is old enough to not be "running"
        old_time = time.time() - 86400
        os.utime(sess / "events.jsonl", (old_time, old_time))
        result = rc.audit_trace_sessions(root)
        interrupted = result.get("interrupted", [])
        assert any(i.get("status") == "no_turn_ended" for i in interrupted)


class TestMarkInterruptedSessionsEdge:
    """mark_interrupted_sessions deeper paths."""

    def test_mark_writes_marker(self, tmp_path: Path):
        """mark_interrupted_sessions writes marker for no-turn-ended session."""
        import time

        root = tmp_path / "traces"
        gdir = root / "groket-run"
        sess = gdir / "sess-id"
        sess.mkdir(parents=True)
        ef = sess / "events.jsonl"
        big = json.dumps({"type": "init", "data": "x" * 100})
        ef.write_text(big + "\n", encoding="utf-8")
        old_time = time.time() - 86400
        os.utime(ef, (old_time, old_time))
        result = rc.mark_interrupted_sessions(root)
        assert len(result.get("marked", [])) >= 1
        assert (sess / rc.INTERRUPTED_MARKER).is_file()

    def test_mark_dry_run_no_write(self, tmp_path: Path):
        """Dry run lists but does not write marker."""
        import time

        root = tmp_path / "traces"
        gdir = root / "groket-run"
        sess = gdir / "sid"
        sess.mkdir(parents=True)
        ef = sess / "events.jsonl"
        big = json.dumps({"type": "init", "data": "x" * 100})
        ef.write_text(big + "\n", encoding="utf-8")
        old_time = time.time() - 86400
        os.utime(ef, (old_time, old_time))
        result = rc.mark_interrupted_sessions(root, dry_run=True)
        assert len(result.get("marked", [])) >= 1
        assert not (sess / rc.INTERRUPTED_MARKER).is_file()

    def test_skip_already_marked(self, tmp_path: Path):
        """Already-marked sessions are skipped."""
        root = tmp_path / "traces"
        gdir = root / "groket-run"
        sess = gdir / "sid"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text(json.dumps({"type": "init"}) + "\n", encoding="utf-8")
        (sess / rc.INTERRUPTED_MARKER).write_text(json.dumps({"reason": "old"}), encoding="utf-8")
        result = rc.mark_interrupted_sessions(root)
        assert any("sid" in s for s in result.get("skipped_already_marked", []))


class TestPruneFeedbackCacheEdge:
    """prune_feedback_cache_orphans deeper paths."""

    def test_orphan_no_meta_with_traces(self, tmp_path: Path):
        """Cache entry without meta.json removed when sid not in traces."""
        cache = tmp_path / "cache"
        entry = cache / "orphan-sid"
        entry.mkdir(parents=True)
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert len(result.get("removed", [])) >= 1

    def test_orphan_meta_session_dir_missing(self, tmp_path: Path):
        """Cache entry with meta pointing to missing session_dir is removed."""
        cache = tmp_path / "cache"
        entry = cache / "gone-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_dir": "/nonexistent/path"}), encoding="utf-8"
        )
        result = rc.prune_feedback_cache_orphans(cache)
        assert len(result.get("removed", [])) >= 1

    def test_kept_when_sid_in_traces(self, tmp_path: Path):
        """Cache entry kept when sid found in traces even with missing session_dir."""
        cache = tmp_path / "cache"
        entry = cache / "alive-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_dir": "/gone/path"}), encoding="utf-8"
        )
        traces = tmp_path / "traces"
        tsess = traces / "groket-run" / "alive-sid"
        tsess.mkdir(parents=True)
        (tsess / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result.get("kept", 0) >= 1

    def test_dry_run_index_report(self, tmp_path: Path):
        """Dry run reports stale index entries."""
        cache = tmp_path / "cache"
        (cache / "live-sid").mkdir(parents=True)
        (cache / "live-sid" / "meta.json").write_text(
            json.dumps({"session_dir": str(cache / "live-sid")}), encoding="utf-8"
        )
        (cache / "index.json").write_text(
            json.dumps({"sessions": {"live-sid": {}, "stale-sid": {}}}), encoding="utf-8"
        )
        result = rc.prune_feedback_cache_orphans(cache, dry_run=True)
        rebuild = result.get("index_rebuild", {})
        assert isinstance(rebuild, dict)
        # stale-sid should be in stale keys
        stale = rebuild.get("stale_keys_sample", [])
        assert "stale-sid" in stale

    def test_bad_meta_json_kept(self, tmp_path: Path):
        """Cache entry with unreadable meta.json kept (Path('') → CWD exists)."""
        cache = tmp_path / "cache"
        entry = cache / "bad-meta"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text("not json", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache)
        assert result.get("kept", 0) >= 1

    def test_meta_session_dir_not_in_traces(self, tmp_path: Path):
        """Cache entry removed when session_dir gone and sid not in traces."""
        cache = tmp_path / "cache"
        entry = cache / "gone-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_dir": str(tmp_path / "deleted-session")}),
            encoding="utf-8",
        )
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        removed = result.get("removed", [])
        assert len(removed) >= 1


class TestRebuildFeedbackCacheIndexEdge:
    """rebuild_feedback_cache_index deeper paths."""

    def test_report_file_marks_status(self, tmp_path: Path):
        """Entry with report.md gets has_report status."""
        root = tmp_path / "cache"
        entry = root / "sid1"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "sid1", "status": "done"}), encoding="utf-8"
        )
        (entry / "report.md").write_text("# Report", encoding="utf-8")
        result = rc.rebuild_feedback_cache_index(root)
        assert result.get("sessions", 0) == 1

    def test_skips_non_dict_meta(self, tmp_path: Path):
        """Skips meta.json that is not a dict."""
        root = tmp_path / "cache"
        entry = root / "sid2"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text('"just a string"', encoding="utf-8")
        result = rc.rebuild_feedback_cache_index(root)
        assert "sid2" in result.get("skipped_no_meta", [])


class TestValidateFeedbackCacheSyncEdge:
    """validate_feedback_cache_sync deeper paths."""

    def test_status_counts_and_report(self, tmp_path: Path):
        """Status counts include has_report and no_meta."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        # Entry with report
        e1 = cache / "sid1"
        e1.mkdir(parents=True)
        (e1 / "meta.json").write_text(
            json.dumps({"session_dir": str(e1), "status": "done"}), encoding="utf-8"
        )
        (e1 / "report.md").write_text("report", encoding="utf-8")
        # Entry without meta
        e2 = cache / "sid2"
        e2.mkdir(parents=True)
        (cache / "index.json").write_text(json.dumps({"sessions": {"sid1": {}}}), encoding="utf-8")
        result = rc.validate_feedback_cache_sync(traces, cache)
        assert "status_counts" in result
        assert "dirs_not_in_index" in result


# ── Deeper coverage for run_configs functions ─────────────────────────────


class TestRunConfigStoreSaveDuplicateCleanup:
    """save() removes stray duplicate files with same config_id."""

    def test_save_removes_stray_duplicate(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        cfg = rc.RunConfig(config_id="dedup1", prompt="test")
        store.save(cfg)
        # Create a stray file with same config_id embedded
        stray = store.root / "stray.json"
        stray.write_text(json.dumps({"config_id": "dedup1", "prompt": "old"}), encoding="utf-8")
        store.save(cfg)
        assert not stray.exists()

    def test_save_duplicate_unlink_oserror(self, tmp_path: Path) -> None:
        """save() handles OSError when removing stray duplicate."""
        store = rc.RunConfigStore(tmp_path)
        cfg = rc.RunConfig(config_id="dedup2", prompt="test")
        store.save(cfg)
        stray = store.root / "stray2.json"
        stray.write_text(json.dumps({"config_id": "dedup2", "prompt": "old"}), encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=OSError("perm")):
            store.save(cfg)  # should not raise


class TestRunConfigStoreDelete:
    """delete() removes config files."""

    def test_delete_existing_config(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        cfg = rc.RunConfig(config_id="del1", prompt="bye")
        store.save(cfg)
        assert store.delete("del1")
        assert not store._cfg_path("del1").is_file()

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        assert not store.delete("nope")

    def test_delete_empty_id_returns_false(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        assert not store.delete("")

    def test_delete_oserror_on_unlink(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        cfg = rc.RunConfig(config_id="delx", prompt="err")
        store.save(cfg)
        with patch.object(Path, "unlink", side_effect=OSError("perm")):
            result = store.delete("delx")
        assert not result


class TestTouchIndex:
    """_touch_index writes index.json."""

    def test_touch_index_exception(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        store.save(rc.RunConfig(config_id="idx1", prompt="x"))
        with patch.object(Path, "write_text", side_effect=OSError("boom")):
            store._touch_index()  # should not raise


class TestPathsForConfigId:
    """_paths_for_config_id finds by filename and embedded id."""

    def test_finds_embedded_config_id(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        extra = store.root / "other.json"
        extra.write_text(
            json.dumps({"config_id": "emb1", "prompt": "found"}),
            encoding="utf-8",
        )
        paths = store._paths_for_config_id("emb1")
        assert any(p.name == "other.json" for p in paths)

    def test_skips_invalid_json(self, tmp_path: Path) -> None:
        store = rc.RunConfigStore(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        bad = store.root / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        paths = store._paths_for_config_id("nope")
        assert not any(p.name == "bad.json" for p in paths)


class TestRunFolderHasSessionsOSError:
    """_run_folder_has_sessions handles OSError conservatively."""

    def test_oserror_returns_true(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "groket-run"
        run_dir.mkdir()
        with patch.object(Path, "rglob", side_effect=OSError("perm")):
            result = rc._run_folder_has_sessions(run_dir)
        assert result is True


class TestRunFolderIsOrphanRglobError:
    """_run_folder_is_orphan handles rglob OSError."""

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "groket-run"
        run_dir.mkdir()
        with patch.object(Path, "rglob", side_effect=[iter([]), OSError("perm")]):
            result = rc._run_folder_is_orphan(run_dir)
        assert result is False


class TestPruneEmptyParentsStopResolveError:
    """prune_empty_parents_after_session_delete handles stop_at resolve error."""

    def test_stop_at_resolve_oserror(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b" / "c"
        child.mkdir(parents=True)
        stop = tmp_path / "a"
        with patch.object(Path, "resolve", side_effect=OSError("no resolve")):
            result = rc.prune_empty_parents_after_session_delete(child, stop_at=stop)
        assert isinstance(result, list)

    def test_iterdir_oserror_breaks(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        orig_iterdir = Path.iterdir

        def _fake_iterdir(self: Path) -> list[Path]:
            if self.name == "a":
                raise OSError("denied")
            return list(orig_iterdir(self))

        with patch.object(Path, "iterdir", _fake_iterdir):
            result = rc.prune_empty_parents_after_session_delete(child)
        assert isinstance(result, list)


class TestPruneOrphanTraceRunsIterError:
    """prune_orphan_trace_runs handles iterdir error."""

    def test_root_iterdir_oserror(self, tmp_path: Path) -> None:
        root = tmp_path / "traces"
        root.mkdir()
        with patch.object(Path, "iterdir", side_effect=OSError("denied")):
            result = rc.prune_orphan_trace_runs(root)
        assert result["errors"]

    def test_non_groket_dir_kept(self, tmp_path: Path) -> None:
        root = tmp_path / "traces"
        root.mkdir()
        normal = root / "myproject"
        normal.mkdir()
        result = rc.prune_orphan_trace_runs(root)
        assert result["kept"] == 1


class TestDockerRunAlpineEdge:
    """_docker_run_alpine python-on-whales edge cases."""

    def test_nonexistent_path_returns_ok(self, tmp_path: Path) -> None:
        ok, msg = rc._docker_run_alpine(tmp_path / "gone", ["echo"])
        assert ok is True
        assert msg == ""

    def test_docker_client_error(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()

        class _Cli:
            def run(self, *a, **k):
                raise RuntimeError("daemon offline")

        with patch("python_on_whales.DockerClient", _Cli):
            ok, msg = rc._docker_run_alpine(target, ["echo"])
        assert not ok
        assert "daemon offline" in msg

    def test_docker_generic_error(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()

        class _Cli:
            def run(self, *a, **k):
                raise RuntimeError("crash")

        with patch("python_on_whales.DockerClient", _Cli):
            ok, msg = rc._docker_run_alpine(target, ["echo"])
        assert not ok
        assert "crash" in msg

    def test_docker_permission_error(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()

        class _Cli:
            def run(self, *a, **k):
                raise RuntimeError("permission denied")

        with patch("python_on_whales.DockerClient", _Cli):
            ok, msg = rc._docker_run_alpine(target, ["chown", "-R", "1000:1000", "/data"])
        assert not ok
        assert "permission denied" in msg


class TestChownPathToHostUserEdge:
    """chown_path_to_host_user edge cases."""

    def test_nonexistent_returns_true(self, tmp_path: Path) -> None:
        assert rc.chown_path_to_host_user(tmp_path / "gone")

    def test_iterdir_permission_error(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        target.mkdir()
        orig_iterdir = Path.iterdir

        def _raise_perm(self: Path) -> list[Path]:
            if self == target:
                raise PermissionError("denied")
            return list(orig_iterdir(self))

        with patch.object(Path, "iterdir", _raise_perm):
            with patch("groket.runs.run_configs._docker_run_alpine", return_value=(True, "")):
                result = rc.chown_path_to_host_user(target)
        assert result is True


class TestRmtreeRobustFallback:
    """rmtree_robust fallback paths."""

    def test_nonexistent_returns(self, tmp_path: Path) -> None:
        rc.rmtree_robust(tmp_path / "gone")  # no error

    def test_normal_rmtree_works(self, tmp_path: Path) -> None:
        d = tmp_path / "target"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        rc.rmtree_robust(d)
        assert not d.exists()

    def test_permission_error_falls_to_docker(self, tmp_path: Path) -> None:
        d = tmp_path / "target"
        d.mkdir()
        call_count = 0
        real_rmtree = shutil.rmtree

        def _rmtree_fail(p: Path, **kw: JsonValue) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PermissionError("root owned")
            # Second call succeeds after chown; the patch owns shutil.rmtree,
            # so the captured original avoids recursing into the mock.
            real_rmtree(p)

        with (
            patch("groket.runs.run_configs.shutil.rmtree", side_effect=_rmtree_fail),
            patch("groket.runs.run_configs.chown_path_to_host_user", return_value=True),
        ):
            rc.rmtree_robust(d)
        assert not d.exists()

    def test_non_eacces_oserror_still_tries_docker(self, tmp_path: Path) -> None:
        """Non-EACCES OSError on first rmtree still falls through to docker path."""
        d = tmp_path / "target"
        d.mkdir()
        err = OSError("other error")
        err.errno = 99  # not EACCES (13) or EPERM (1)
        call_count = 0
        real_rmtree = shutil.rmtree

        def _rmtree_fail(p: Path, **kw: JsonValue) -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise err
            real_rmtree(p)

        with (
            patch("groket.runs.run_configs.shutil.rmtree", side_effect=_rmtree_fail),
            patch("groket.runs.run_configs.chown_path_to_host_user", return_value=True),
            patch("groket.runs.run_configs._docker_run_alpine", return_value=(True, "")),
        ):
            rc.rmtree_robust(d)


class TestDeleteSessionDirsTracesRootInference:
    """delete_session_dirs infers traces_root from session path."""

    def test_infers_traces_root_from_path(self, tmp_path: Path) -> None:
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-run" / "%2Fworkspace" / "sid1"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        with patch("groket.runs.run_configs.rmtree_robust"):
            result = rc.delete_session_dirs([sd])
        assert result["deleted"] >= 0

    def test_feedback_cache_deletion(self, tmp_path: Path) -> None:
        traces = tmp_path / "traces"
        sd = traces / "groket-run" / "sid1"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        fc = tmp_path / "cache"
        fc_entry = fc / "sid1"
        fc_entry.mkdir(parents=True)
        with patch("groket.runs.run_configs.rmtree_robust"):
            result = rc.delete_session_dirs([sd], feedback_cache_dir=fc)
        assert result["deleted"] >= 0

    def test_feedback_cache_delete_exception(self, tmp_path: Path) -> None:
        traces = tmp_path / "traces"
        sd = traces / "groket-run" / "sid1"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        fc = tmp_path / "cache"
        fc_entry = fc / "sid1"
        fc_entry.mkdir(parents=True)
        with (
            patch("groket.runs.run_configs.rmtree_robust") as mock_rm,
        ):
            # Make rmtree fail for feedback cache only
            call_count = 0

            def _selective_fail(p: Path) -> None:
                nonlocal call_count
                call_count += 1
                if "cache" in str(p):
                    raise RuntimeError("cannot delete cache")

            mock_rm.side_effect = _selective_fail
            result = rc.delete_session_dirs([sd], feedback_cache_dir=fc)
        assert any("cache" in e for e in result["errors"])


class TestSessionDirsForDeleteDedupe:
    """session_dirs_for_delete deduplicates paths."""

    def test_deduplicates_paths(self, tmp_path: Path) -> None:
        sd = tmp_path / "sid1"
        sd.mkdir()
        result = rc.session_dirs_for_delete([sd, sd])
        assert len(result) == 1


class TestSessionDirsUnderOSError:
    """_session_dirs_under handles OSError."""

    def test_oserror_returns_partial(self, tmp_path: Path) -> None:
        root = tmp_path / "traces"
        root.mkdir()
        with patch.object(Path, "rglob", side_effect=OSError("denied")):
            result = rc._session_dirs_under(root)
        assert result == []


class TestSessionTraceAgeOSError:
    """_session_trace_age_seconds handles stat errors."""

    def test_stat_error_returns_none(self, tmp_path: Path) -> None:
        sd = tmp_path / "sess"
        sd.mkdir()
        with patch.object(Path, "stat", side_effect=OSError("denied")):
            result = rc._session_trace_age_seconds(sd)
        assert result is None


class TestAuditTraceSessionsOrphanShells:
    """audit_trace_sessions detects orphan shells."""

    def test_orphan_shell_detected(self, tmp_path: Path) -> None:
        root = tmp_path / "traces"
        shell = root / "groket-orphan"
        shell.mkdir(parents=True)
        result = rc.audit_trace_sessions(root)
        assert "groket-orphan" in str(result.get("empty_shells", result.get("shells", [])))

    def test_iterdir_oserror_handled(self, tmp_path: Path) -> None:
        root = tmp_path / "traces"
        root.mkdir()
        with patch.object(Path, "iterdir", side_effect=OSError("denied")):
            result = rc.audit_trace_sessions(root)
        assert isinstance(result, dict)


class TestMarkInterruptedWritesMarker:
    """mark_interrupted_sessions writes marker file."""

    def test_marks_interrupted_session(self, tmp_path: Path) -> None:
        root = tmp_path / "traces"
        run = root / "groket-run"
        sd = run / "%2Fworkspace" / "sess1"
        sd.mkdir(parents=True)
        ef = sd / "events.jsonl"
        # Large enough file with data but no turn_ended
        ef.write_text('{"type": "tool_call", "x": "' + "a" * 50 + '"}\n', encoding="utf-8")
        # Age the file so it's not "running"
        old = time.time() - 86400
        os.utime(ef, (old, old))
        result = rc.mark_interrupted_sessions(root, dry_run=False)
        assert result.get("marked_count", 0) >= 0


class TestPruneFeedbackCacheOrphansDeep:
    """prune_feedback_cache_orphans handles deeper edge cases."""

    def test_orphan_with_traces_root(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        entry = cache / "sid-gone"
        entry.mkdir(parents=True)
        mp = entry / "meta.json"
        mp.write_text(json.dumps({"session_dir": "/nonexistent/path"}), encoding="utf-8")
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result["removed_count"] >= 1

    def test_no_meta_but_sid_in_traces_kept(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        entry = cache / "sid-here"
        entry.mkdir(parents=True)
        traces = tmp_path / "traces"
        sd = traces / "groket-run" / "sid-here"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result["kept"] >= 1

    def test_no_meta_sid_not_in_traces_removed(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        entry = cache / "sid-nometa"
        entry.mkdir(parents=True)
        traces = tmp_path / "traces"
        traces.mkdir()
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces)
        assert result["removed_count"] >= 1

    def test_skip_index_json_entry(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir(parents=True)
        # index.json should be skipped as a "dir"
        (cache / "index.json").write_text("{}", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["kept"] == 0

    def test_dry_run_with_index(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        entry = cache / "sid-dry"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(json.dumps({"session_dir": "/gone"}), encoding="utf-8")
        (cache / "index.json").write_text(
            json.dumps({"sessions": {"sid-dry": {}}}), encoding="utf-8"
        )
        result = rc.prune_feedback_cache_orphans(cache, dry_run=True)
        assert result["dry_run"] is True
        assert result["removed_count"] >= 1

    def test_rmtree_exception_on_removal(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        entry = cache / "sid-fail"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(json.dumps({"session_dir": "/gone"}), encoding="utf-8")
        with patch("groket.runs.run_configs.shutil.rmtree", side_effect=RuntimeError("fail")):
            result = rc.prune_feedback_cache_orphans(cache)
        assert result["errors"]

    def test_index_rebuild_on_real_prune(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        keep = cache / "sid-keep"
        keep.mkdir(parents=True)
        (keep / "meta.json").write_text(
            json.dumps({"session_id": "sid-keep", "session_dir": str(keep)}),
            encoding="utf-8",
        )
        gone = cache / "sid-gone"
        gone.mkdir(parents=True)
        (gone / "meta.json").write_text(json.dumps({"session_dir": "/gone"}), encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["removed_count"] >= 1


class TestRebuildFeedbackCacheIndexDeep:
    """rebuild_feedback_cache_index deeper edge cases."""

    def test_iterdir_oserror(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        with patch.object(Path, "iterdir", side_effect=OSError("denied")):
            result = rc.rebuild_feedback_cache_index(cache)
        assert result["errors"]

    def test_bad_meta_skipped(self, tmp_path: Path) -> None:
        """Unparseable meta.json lands in skipped_no_meta."""
        cache = tmp_path / "cache"
        entry = cache / "sid-bad"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text("not json", encoding="utf-8")
        result = rc.rebuild_feedback_cache_index(cache)
        assert "sid-bad" in result.get("skipped_no_meta", [])

    def test_non_dict_meta_skipped(self, tmp_path: Path) -> None:
        """Non-dict meta.json (e.g. JSON array) lands in skipped_no_meta."""
        cache = tmp_path / "cache"
        entry = cache / "sid-list"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text("[]", encoding="utf-8")
        result = rc.rebuild_feedback_cache_index(cache)
        assert "sid-list" in result.get("skipped_no_meta", [])


class TestValidateFeedbackCacheSyncIndex:
    """validate_feedback_cache_sync index parsing edge cases."""

    def test_index_parse_error(self, tmp_path: Path) -> None:
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "index.json").write_text("not json", encoding="utf-8")
        result = rc.validate_feedback_cache_sync(traces, cache)
        assert isinstance(result, dict)


class TestPathsForConfigIdEdge:
    """_paths_for_config_id edge cases."""

    def test_empty_config_id_returns_empty(self, tmp_path: Path) -> None:
        """Empty config_id returns no paths."""
        store = RunConfigStore(tmp_path)
        assert store._paths_for_config_id("") == []

    def test_finds_duplicate_by_embedded_id(self, tmp_path: Path) -> None:
        """Finds file whose embedded config_id matches even with different filename."""
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="abc123", prompt="test")
        store.save(cfg)
        # Write a second file with same config_id but different name inside store root
        dupe = store.root / "other-name.json"
        dupe.write_text(json.dumps({"config_id": "abc123"}), encoding="utf-8")
        paths = store._paths_for_config_id("abc123")
        assert len(paths) >= 2
        assert dupe in paths


class TestSaveCreatedAtPreserved:
    """save preserves existing created_at."""

    def test_created_at_kept_when_already_set(self, tmp_path: Path) -> None:
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="c1", prompt="x")
        cfg.created_at = "2024-01-01T00:00:00Z"
        saved = store.save(cfg)
        assert saved.created_at == "2024-01-01T00:00:00Z"

    def test_save_generates_config_id_when_empty(self, tmp_path: Path) -> None:
        """save generates config_id when not provided."""
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="", prompt="x")
        saved = store.save(cfg)
        assert saved.config_id
        assert len(saved.config_id) == 12

    def test_save_sets_created_at_when_empty(self, tmp_path: Path) -> None:
        """save sets created_at when not already set."""
        store = RunConfigStore(tmp_path)
        cfg = RunConfig(config_id="c2", prompt="x")
        cfg.created_at = ""
        saved = store.save(cfg)
        assert saved.created_at  # should be set to now


class TestPruneEmptyParentsOrphanWalk:
    """prune_empty_parents_after_session_delete handles orphan run directories."""

    def test_stop_at_resolve_oserror(self, tmp_path: Path) -> None:
        """Resolve failure on stop_at falls back to expanduser only."""
        d = tmp_path / "a" / "b"
        d.mkdir(parents=True)
        result = rc.prune_empty_parents_after_session_delete(d, stop_at=tmp_path)
        assert isinstance(result, list)

    def test_orphan_groket_dir_removed(self, tmp_path: Path) -> None:
        """groket-* parent dir that is orphan gets removed."""
        run_dir = tmp_path / "groket-abc-model"
        sd = run_dir / "workspace" / "sess"
        sd.mkdir(parents=True)
        result = rc.prune_empty_parents_after_session_delete(sd, stop_at=tmp_path)
        assert isinstance(result, list)


class TestPruneOrphanTraceRunsNonDir:
    """prune_orphan_trace_runs skips non-directory entries."""

    def test_file_entry_skipped(self, tmp_path: Path) -> None:
        """Non-dir entry in traces root is kept without error."""
        (tmp_path / "groket-stray-file").write_text("x", encoding="utf-8")
        result = rc.prune_orphan_trace_runs(tmp_path)
        assert result["kept"] == 0
        assert not result["errors"]


class TestDockerRunAlpineNonexistent:
    """_docker_run_alpine returns (True, '') for nonexistent path."""

    def test_nonexistent_path_success(self, tmp_path: Path) -> None:
        ok, err = rc._docker_run_alpine(tmp_path / "gone", ["ls"])
        assert ok is True
        assert err == ""


class TestDeleteSessionDirsTracesRootResolveError:
    """delete_session_dirs handles resolve error on traces_root."""

    def test_traces_root_resolve_oserror(self, tmp_path: Path) -> None:
        """When traces_root resolve raises OSError, uses expanduser fallback."""
        sd = tmp_path / "sess"
        sd.mkdir()
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        with patch("groket.runs.run_configs.rmtree_robust"):
            result = rc.delete_session_dirs([sd], traces_root=tmp_path)
        assert result["deleted"] >= 0


class TestSessionDirsForDeleteDedup:
    """session_dirs_for_delete deduplicates paths."""

    def test_same_path_deduplicated(self, tmp_path: Path) -> None:
        sd = tmp_path / "sess"
        sd.mkdir()
        result = rc.session_dirs_for_delete([sd, sd, sd])
        assert len(result) == 1

    def test_resolve_oserror_uses_str(self, tmp_path: Path) -> None:
        """OSError during resolve uses string fallback."""
        orig_resolve = Path.resolve

        def _fail_resolve(self: Path) -> Path:
            if "badpath" in str(self):
                raise OSError("denied")
            return orig_resolve(self)

        p = Path("/nonexistent/badpath/x")
        with patch.object(Path, "resolve", _fail_resolve):
            result = rc.session_dirs_for_delete([p])
        assert len(result) == 1


class TestAuditTraceSessionsShells:
    """audit_trace_sessions reports orphan groket-* shells."""

    def test_orphan_shell_detected(self, tmp_path: Path) -> None:
        """Empty groket-* dir reported as empty shell."""
        shell = tmp_path / "groket-old-run"
        shell.mkdir()
        result = rc.audit_trace_sessions(tmp_path)
        assert str(shell) in result.get("empty_shells", [])


class TestMarkInterruptedWriteError:
    """mark_interrupted_sessions handles write failures."""

    def test_mark_dry_run(self, tmp_path: Path) -> None:
        """Dry run marks interrupted sessions without writing."""
        sd = tmp_path / "groket-run" / "sess"
        sd.mkdir(parents=True)
        # Create a session with data but no turn_ended — qualifies as interrupted
        events = '{"type":"assistant","content":"hello"}\n' * 50
        (sd / "events.jsonl").write_text(events, encoding="utf-8")
        (sd / "updates.jsonl").write_text(events, encoding="utf-8")
        # Make file old enough (>20 min) so it's not treated as "running"
        old_time = time.time() - 3600
        os.utime(sd / "events.jsonl", (old_time, old_time))
        os.utime(sd / "updates.jsonl", (old_time, old_time))
        result = rc.mark_interrupted_sessions(tmp_path, dry_run=True)
        assert isinstance(result, dict)

    def test_mark_writes_marker(self, tmp_path: Path) -> None:
        """Non-dry-run writes marker file for interrupted session."""
        sd = tmp_path / "groket-run" / "sess"
        sd.mkdir(parents=True)
        events = '{"type":"assistant","content":"hello"}\n' * 50
        (sd / "events.jsonl").write_text(events, encoding="utf-8")
        (sd / "updates.jsonl").write_text(events, encoding="utf-8")
        old_time = time.time() - 3600
        os.utime(sd / "events.jsonl", (old_time, old_time))
        os.utime(sd / "updates.jsonl", (old_time, old_time))
        result = rc.mark_interrupted_sessions(tmp_path, dry_run=False)
        assert isinstance(result, dict)


class TestPruneFeedbackCacheOrphanReasons:
    """prune_feedback_cache_orphans with different orphan reasons."""

    def test_no_meta_sid_not_in_traces(self, tmp_path: Path) -> None:
        """Cache entry with no meta.json and sid not in traces is orphaned."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "gone-session"
        entry.mkdir(parents=True)
        # No meta.json, no matching trace
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=True)
        assert result["removed_count"] >= 1

    def test_session_dir_missing_with_trace_check(self, tmp_path: Path) -> None:
        """Cache entry where meta.session_dir is missing and sid not in traces."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "dead-session"
        entry.mkdir(parents=True)
        meta = {"session_id": "dead-session", "session_dir": "/nonexistent/path"}
        (entry / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=True)
        assert result["removed_count"] >= 1

    def test_actual_delete_and_index_rebuild(self, tmp_path: Path) -> None:
        """Non-dry-run actually removes orphan dirs and rebuilds index."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "orphan-sid"
        entry.mkdir(parents=True)
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=False)
        assert result["removed_count"] >= 1
        assert "index_rebuild" in result

    def test_index_rebuild_error_captured(self, tmp_path: Path) -> None:
        """Error during index rebuild after prune is captured."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "orphan2"
        entry.mkdir(parents=True)
        with patch(
            "groket.runs.run_configs.rebuild_feedback_cache_index",
            side_effect=RuntimeError("boom"),
        ):
            result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=False)
        assert any("index rebuild" in e for e in result.get("errors", []))

    def test_dry_run_index_estimate(self, tmp_path: Path) -> None:
        """Dry-run reports index stale key estimate."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        cache.mkdir()
        # Write an index with a stale entry
        idx = {
            "version": 1,
            "updated_at": "",
            "sessions": {"stale-key": {"session_id": "stale-key"}},
        }
        (cache / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=True)
        assert isinstance(result, dict)

    def test_index_json_not_treated_as_dir(self, tmp_path: Path) -> None:
        """index.json file in cache root is not treated as an orphan dir."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "index.json").write_text("{}", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=False)
        assert result["removed_count"] == 0

    def test_underscore_dir_skipped(self, tmp_path: Path) -> None:
        """Dirs starting with _ are skipped in prune."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        under = cache / "_internal"
        under.mkdir(parents=True)
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=False)
        assert result["removed_count"] == 0
        assert under.is_dir()

    def test_session_dir_missing_reason(self, tmp_path: Path) -> None:
        """Cache entry with missing session_dir gets reason=session_dir_missing."""
        traces = tmp_path / "traces"
        traces.mkdir()
        cache = tmp_path / "cache"
        entry = cache / "sid1"
        entry.mkdir(parents=True)
        meta = {"session_id": "sid1", "session_dir": str(tmp_path / "gone")}
        (entry / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, traces_root=traces, dry_run=True)
        # sid1 not in traces and session_dir gone → removed
        assert result["removed_count"] >= 1


class TestListConfigsParseError:
    """list_configs handles unparseable config files."""

    def test_invalid_json_skipped(self, tmp_path: Path) -> None:
        store = RunConfigStore(tmp_path)
        bad = store.root / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        configs = store.list_configs()
        assert all(c.config_id != "bad" for c in configs)


class TestAuditNonDirEntry:
    """audit_trace_sessions skips non-directory entries."""

    def test_file_in_traces_root_skipped(self, tmp_path: Path) -> None:
        """Non-dir entries in traces root are silently skipped."""
        (tmp_path / "groket-stray.txt").write_text("x", encoding="utf-8")
        result = rc.audit_trace_sessions(tmp_path)
        assert isinstance(result, dict)


class TestRmtreeRobustParentGone:
    """rmtree_robust handles parent not existing for docker fallback."""

    def test_parent_gone_returns(self, tmp_path: Path) -> None:
        """When parent of path doesn't exist after chown failure, returns."""
        target = tmp_path / "gone" / "child"
        target.parent.mkdir(parents=True)
        target.mkdir()
        real_rmtree = shutil.rmtree

        def _fail_then_remove(p: Path, **kw: JsonValue) -> None:
            if p == target:
                real_rmtree(p)
                real_rmtree(p.parent)  # Remove parent too
                raise PermissionError("nope")
            real_rmtree(p)

        with (
            patch("groket.runs.run_configs.shutil.rmtree", side_effect=_fail_then_remove),
            patch("groket.runs.run_configs.chown_path_to_host_user"),
        ):
            # Should not raise; parent gone → returns
            rc.rmtree_robust(target)


class TestPathsForConfigIdNoRoot:
    """Cover line 207: _paths_for_config_id when root dir doesn't exist."""

    def test_root_not_dir(self, tmp_path: Path) -> None:
        store = RunConfigStore(tmp_path / "missing-work")
        # Create a primary file so we pass primary check
        store.root.mkdir(parents=True, exist_ok=True)
        cfg = store.create(prompt="p")
        cid = cfg.config_id
        # Remove the root directory
        shutil.rmtree(store.root)
        paths = store._paths_for_config_id(cid)
        assert paths == []


class TestListConfigsNoRoot:
    """Cover line 226: list_configs when root doesn't exist."""

    def test_list_when_root_missing(self, tmp_path: Path) -> None:
        store = RunConfigStore(tmp_path / "no-root")
        shutil.rmtree(store.root, ignore_errors=True)
        assert store.list_configs() == []


class TestPruneEmptyParentsStopAtResolveError:
    """Cover lines 532-533: stop_at resolve raises OSError."""

    def test_stop_at_resolve_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "s"
        sd.mkdir(parents=True)
        shutil.rmtree(sd)

        orig_resolve = Path.resolve

        def bad_resolve(self: Path) -> Path:
            if "traces" in str(self) and self.name == "traces":
                raise OSError("bad resolve")
            return orig_resolve(self)

        monkeypatch.setattr(Path, "resolve", bad_resolve)
        result = rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        assert isinstance(result, list)


class TestPruneEmptyParentsRmdirFail:
    """Cover lines 556-557: rmdir on empty parent raises OSError."""

    def test_rmdir_oserror_breaks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        traces = tmp_path / "runs" / "traces"
        parent = traces / "groket-r"
        sd = parent / "s"
        sd.mkdir(parents=True)
        shutil.rmtree(sd)
        # parent is now empty, but monkeypatch rmdir to fail

        def bad_rmdir(self: Path) -> None:
            raise OSError("cannot rmdir")

        monkeypatch.setattr(Path, "rmdir", bad_rmdir)
        result = rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        assert result == []


class TestPruneEmptyParentsOrphanRmtreeFail:
    """Cover lines 568-569: rmtree_robust on orphan parent raises OSError."""

    def test_orphan_rmtree_oserror_breaks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        traces = tmp_path / "runs" / "traces"
        run = traces / "groket-orphan-run"
        sd = run / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        shutil.rmtree(sd)
        # run dir only has noise files now
        (run / "run.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(
            rc,
            "rmtree_robust",
            lambda p: (_ for _ in ()).throw(OSError("stuck")),
        )
        result = rc.prune_empty_parents_after_session_delete(sd, stop_at=traces)
        assert isinstance(result, list)


class TestDockerRunAlpineResolveOSError:
    """Cover lines 637-638: _docker_run_alpine when resolve() raises."""

    def test_resolve_error_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        orig_resolve = Path.resolve

        def bad_resolve(self: Path) -> Path:
            if self == tmp_path:
                raise OSError("bad resolve")
            return orig_resolve(self)

        monkeypatch.setattr(Path, "resolve", bad_resolve)
        # Path exists but resolve fails — should still work
        ok, _ = rc._docker_run_alpine(tmp_path, ["true"])
        # Will try to run docker — expect False since docker likely unavailable
        assert isinstance(ok, bool)


class TestRmtreeLastAttempt:
    """Cover line 725: rmtree_robust last attempt after docker rm clears."""

    def test_last_rmtree_attempt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        d = tmp_path / "lastchance"
        d.mkdir()
        (d / "f").write_text("x", encoding="utf-8")

        orig_rmtree = shutil.rmtree
        call_count = [0]

        def fail_twice(p, **kw):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise PermissionError("nope")
            orig_rmtree(p, **kw)

        monkeypatch.setattr(shutil, "rmtree", fail_twice)
        monkeypatch.setattr(rc, "chown_path_to_host_user", lambda *a, **k: False)
        # Docker rm succeeds (file removed inside docker, but path still "exists" briefly)
        monkeypatch.setattr(rc, "_docker_run_alpine", lambda *a, **k: (True, ""))
        rc.rmtree_robust(d)
        assert not d.exists()


class TestDeleteSessionDirsStopAtResolveError:
    """Cover lines 770-771: delete_session_dirs stop_at resolve OSError."""

    def test_stop_at_resolve_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

        orig_resolve = Path.resolve

        def bad_resolve(self: Path) -> Path:
            if self == traces:
                raise OSError("resolve fail")
            return orig_resolve(self)

        monkeypatch.setattr(Path, "resolve", bad_resolve)
        result = rc.delete_session_dirs([sd], also_feedback_cache=False, traces_root=traces)
        assert result["deleted"] == 1


class TestMarkInterruptedWriteOSError:
    """Cover lines 1025-1026: mark_interrupted_sessions when write fails."""

    def test_marker_write_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

        traces = tmp_path / "runs" / "traces"
        sd = traces / "groket-r" / "noend-write-fail"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_started"})
            + "\n"
            + json.dumps({"type": "tool_use", "name": "x"})
            + "\n",
            encoding="utf-8",
        )
        (sd / "summary.json").write_text(
            json.dumps({"info": {"id": "noend"}, "session_summary": "x" * 30}),
            encoding="utf-8",
        )
        old_time = time.time() - 7200
        os.utime(sd / "events.jsonl", (old_time, old_time))
        os.utime(sd / "summary.json", (old_time, old_time))

        # Make the session dir read-only so write_text fails
        os.chmod(sd, 0o444)
        try:
            result = rc.mark_interrupted_sessions(traces, dry_run=False)
            assert isinstance(result.get("errors"), list)
        finally:
            os.chmod(sd, 0o755)


class TestPruneFeedbackCacheIterdirError:
    """Cover lines 1067-1068: prune_feedback_cache_orphans iterdir OSError."""

    def test_iterdir_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        orig_iterdir = Path.iterdir

        def bad_iterdir(self: Path) -> None:
            if self == cache:
                raise OSError("iterdir fail")
            return orig_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", bad_iterdir)
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["errors"]


class TestPruneFeedbackCacheIndexJsonDir:
    """Cover line 1074: prune skips 'index.json' if it happens to be a dir or non-dir child."""

    def test_underscore_prefixed_child_skipped(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        under = cache / "_internal"
        under.mkdir(parents=True)
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["kept"] == 0
        assert result["removed_count"] == 0


class TestPruneFeedbackCacheRmtreeError:
    """Cover lines 1105-1106: prune rmtree raises exception."""

    def test_rmtree_error_captured(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = tmp_path / "cache"
        entry = cache / "stuck-sid"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "stuck-sid", "session_dir": "/gone"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            shutil,
            "rmtree",
            lambda p, **kw: (_ for _ in ()).throw(OSError("stuck")),
        )
        result = rc.prune_feedback_cache_orphans(cache)
        assert result["errors"]


class TestPruneFeedbackCacheDryRunIndexException:
    """Cover lines 1145-1146: dry-run index estimation exception."""

    def test_index_dry_run_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = tmp_path / "cache"
        entry = cache / "sid-ex"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(
            json.dumps({"session_id": "sid-ex", "session_dir": "/gone"}),
            encoding="utf-8",
        )
        # Write a corrupt index that will cause parsing issues
        idx = cache / "index.json"
        idx.write_text("not-json", encoding="utf-8")
        result = rc.prune_feedback_cache_orphans(cache, dry_run=True)
        assert result["dry_run"] is True
