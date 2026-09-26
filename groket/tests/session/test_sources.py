"""Session catalog roots: work traces + optional host Grok."""

from __future__ import annotations

import json
from pathlib import Path

from groket.session.sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    classify_session_origin,
    collect_session_dirs,
    host_grok_sessions_root,
    is_host_grok_sessions_root,
    is_under_host_grok_sessions,
    session_scan_roots,
    work_traces_root,
)


def _seed_session(root: Path, *, cwd_token: str, sid: str, title: str = "t") -> Path:
    sess = root / cwd_token / sid
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        json.dumps({"session_id": sid, "generated_title": title}),
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sess / "updates.jsonl").write_text("", encoding="utf-8")
    return sess


def test_host_grok_sessions_root_default() -> None:
    assert host_grok_sessions_root() == Path.home() / ".grok" / "sessions"


def test_work_traces_root(tmp_path: Path) -> None:
    assert work_traces_root(tmp_path) == tmp_path / "runs" / "traces"


def test_session_scan_roots_work_only(tmp_path: Path) -> None:
    roots = session_scan_roots(tmp_path, include_host=False)
    assert len(roots) == 1
    assert roots[0].origin == ORIGIN_WORK
    assert roots[0].path == tmp_path / "runs" / "traces"


def test_session_scan_roots_with_host(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "host-sessions"
    host.mkdir()
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    roots = session_scan_roots(tmp_path, include_host=True)
    assert [r.origin for r in roots] == [ORIGIN_WORK, ORIGIN_HOST]
    assert roots[1].path == host


def test_collect_session_dirs_union(tmp_path: Path) -> None:
    work = tmp_path / "runs" / "traces"
    host = tmp_path / "host"
    w_sess = _seed_session(work / "groket-run-1", cwd_token="%2Fworkspace", sid="work-sid")
    h_sess = _seed_session(host, cwd_token="%2Fproj", sid="host-sid")
    roots = session_scan_roots(tmp_path, include_host=True, host_root=host)
    found = {str(p.resolve()): o for p, o in collect_session_dirs(roots)}
    assert found[str(w_sess.resolve())] == ORIGIN_WORK
    assert found[str(h_sess.resolve())] == ORIGIN_HOST


def test_classify_and_under_host(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2Fa", sid="s1")
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    assert is_under_host_grok_sessions(sess)
    assert (
        classify_session_origin(sess, work_traces=tmp_path / "runs" / "traces", host_root=host)
        == ORIGIN_HOST
    )
    work_sess = _seed_session(
        tmp_path / "runs" / "traces" / "groket-x", cwd_token="%2Fworkspace", sid="w1"
    )
    assert (
        classify_session_origin(work_sess, work_traces=tmp_path / "runs" / "traces", host_root=host)
        == ORIGIN_WORK
    )


def test_is_host_sessions_root(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / ".grok" / "sessions"
    host.mkdir(parents=True)
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    assert is_host_grok_sessions_root(host)
    assert not is_host_grok_sessions_root(tmp_path)
