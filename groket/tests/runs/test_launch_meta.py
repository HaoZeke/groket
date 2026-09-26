"""Per-container groket-launch.json write/read."""

from __future__ import annotations

import json
from pathlib import Path

from groket.docker.orchestrator import ContainerConfig
from groket.models import SessionMeta
from groket.parser import load_session_meta
from groket.runs.launch_meta import (
    LAUNCH_META_FILENAME,
    apply_launch_meta,
    build_launch_meta,
    find_launch_meta_file,
    launch_meta_from_config,
    read_launch_meta,
    write_launch_meta,
    write_launch_meta_for_config,
)


def test_build_and_write_roundtrip(tmp_path: Path) -> None:
    meta = build_launch_meta(
        model="v9-goldbond",
        reasoning_effort="xhigh",
        container_name="groket-abc-goldbond-xhigh",
        run_id="abc",
    )
    assert meta.model_token == "v9-goldbond:xhigh"
    vol = tmp_path / "groket-abc-goldbond-xhigh"
    path = write_launch_meta(vol, meta)
    assert path.name == LAUNCH_META_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "v9-goldbond"
    assert data["reasoning_effort"] == "xhigh"
    assert data["model_token"] == "v9-goldbond:xhigh"
    assert read_launch_meta(tmp_path / "unrelated" / "sess") is None
    sess = vol / "%2Fworkspace" / "019f-sess"
    sess.mkdir(parents=True)
    loaded = read_launch_meta(sess)
    assert loaded is not None
    assert loaded.display_token == "v9-goldbond:xhigh"


def test_write_from_container_config(tmp_path: Path) -> None:
    cfg = ContainerConfig(
        model="v9-tomato",
        prompt="p",
        container_name="groket-run1-tomato-xhigh",
        reasoning_effort="xhigh",
        run_id="run1",
    )
    write_launch_meta_for_config(tmp_path, cfg)
    launch = launch_meta_from_config(cfg)
    assert launch.model == "v9-tomato"
    assert find_launch_meta_file(tmp_path / "s") == tmp_path / LAUNCH_META_FILENAME


def test_fork_resume_fields_in_launch_meta(tmp_path: Path) -> None:
    from groket.runs.launch_meta import is_fork_resume_parent_session

    cfg = ContainerConfig(
        model="v9",
        prompt="continue",
        container_name="groket-fork",
        resume_session_id="parent-id",
        resume_fork_session_id="child-id",
        resume_source_dir="/tmp/unused",
    )
    write_launch_meta_for_config(tmp_path, cfg)
    data = json.loads((tmp_path / LAUNCH_META_FILENAME).read_text(encoding="utf-8"))
    assert data["resume_parent_session_id"] == "parent-id"
    assert data["resume_fork_session_id"] == "child-id"
    parent = tmp_path / "%2Fworkspace" / "parent-id"
    child = tmp_path / "%2Fworkspace" / "child-id"
    parent.mkdir(parents=True)
    child.mkdir(parents=True)
    assert is_fork_resume_parent_session(parent) is True
    assert is_fork_resume_parent_session(child) is False


def test_load_session_meta_prefers_launch_over_summary_and_slug(tmp_path: Path) -> None:
    vol = tmp_path / "traces" / "groket-deadbeef-xhighx2"
    sess = vol / "%2Fworkspace" / "019f-sess"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        json.dumps({"current_model_id": "v9-goldbond", "reasoning_effort": "high"}),
        encoding="utf-8",
    )
    write_launch_meta(
        vol,
        build_launch_meta(
            model="v9-goldbond",
            reasoning_effort="xhigh",
            container_name=vol.name,
            run_id="deadbeef",
        ),
    )
    meta = load_session_meta(sess, include_timeline_count=False)
    assert meta.model_id == "v9-goldbond"
    assert meta.reasoning_effort == "xhigh"
    assert meta.model_display == "v9-goldbond:xhigh"


def test_apply_launch_meta_sets_run_and_task_ids(tmp_path: Path) -> None:
    meta = SessionMeta(session_id="s", session_dir=tmp_path)
    launch = build_launch_meta(
        model="v9-tomato:xhigh",
        run_id="rid",
        task_id="tid",
    )
    apply_launch_meta(meta, launch)
    assert meta.model_id == "v9-tomato"
    assert meta.reasoning_effort == "xhigh"
    assert meta.run_id == "rid"
    assert meta.task_id == "tid"


def test_read_ignores_invalid_json(tmp_path: Path) -> None:
    (tmp_path / LAUNCH_META_FILENAME).write_text("{not json", encoding="utf-8")
    assert read_launch_meta(tmp_path) is None
