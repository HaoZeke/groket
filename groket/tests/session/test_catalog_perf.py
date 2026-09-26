"""Catalog list stays cheap for large trees: no per-row timeline parse."""

from __future__ import annotations

import json
from pathlib import Path

import groket.parser as parser_mod
import pytest
from groket.session import catalog as catalog_mod
from groket.session.catalog import SessionCatalogCache, list_session_catalog
from groket.session.wire_timeline import fetch_session_browser_bundle


def _write_sess(root: Path, name: str, title: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": title,
                "num_messages": 2,
            }
        ),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_host_discovery_skips_encoded_cwd_and_workspace_junk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host catalog must not walk junk interiors looking for nested summary.json."""
    import os

    from groket.session.sources import collect_session_dirs, session_scan_roots

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    host = tmp_path / "host"
    host.mkdir()
    shallow = _write_sess(host, "019aaaaa-1111-2222-3333-444444444444", "Shallow host")
    bucket = host / "%2Fhome%2Fproj"
    known = _write_sess(bucket, "019bbbbb-1111-2222-3333-444444444444", "Known layout")
    work_sess = _write_sess(traces, "work-eval-1", "Eval row")

    junk_ws = host / "workspace" / "deep" / "nested"
    junk_ws.mkdir(parents=True)
    (junk_ws / "summary.json").write_text("{}", encoding="utf-8")
    (junk_ws / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    junk_under_cwd = bucket / "workspace" / "deep"
    junk_under_cwd.mkdir(parents=True)
    (junk_under_cwd / "summary.json").write_text("{}", encoding="utf-8")

    visited: list[Path] = []
    real_scandir = os.scandir
    real_walk = os.walk

    def track_scandir(path: str | os.PathLike[str], *args: object, **kwargs: object):
        visited.append(Path(path).resolve())
        return real_scandir(path, *args, **kwargs)

    def track_walk(path: str | os.PathLike[str], *args: object, **kwargs: object):
        visited.append(Path(path).resolve())
        return real_walk(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", track_scandir)
    monkeypatch.setattr(os, "walk", track_walk)

    roots = session_scan_roots(work, include_host=True, host_root=host)
    found = collect_session_dirs(roots)
    names = {p.name for p, _orig in found}
    assert shallow.name in names
    assert known.name in names
    assert work_sess.name in names
    assert junk_ws.name not in names
    assert "nested" not in names

    visited_res = {p.resolve() for p in visited}
    assert junk_ws.resolve() not in visited_res
    assert junk_under_cwd.resolve() not in visited_res
    assert not any(junk_ws.resolve() == p or junk_ws.resolve() in p.parents for p in visited_res)


def test_list_session_catalog_does_not_parse_timeline(tmp_path: Path, monkeypatch) -> None:
    traces = tmp_path / "work" / "runs" / "traces"
    traces.mkdir(parents=True)
    for i in range(240):
        _write_sess(traces, f"s{i:04d}", f"Title {i}")
    parsed: list[str] = []

    def _boom(session_dir: Path) -> list[object]:
        parsed.append(str(session_dir))
        raise AssertionError("parse_timeline must not run for catalog rows")

    monkeypatch.setattr(parser_mod, "parse_timeline", _boom)
    rows = list_session_catalog(tmp_path / "work", traces_path=traces, include_host=False)
    assert len(rows) == 240
    assert parsed == []
    assert rows[0]["sessionId"]
    assert "title" in rows[0]


def test_catalog_cache_second_get_skips_rebuild(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    for i in range(80):
        _write_sess(traces, f"c{i:03d}", f"C {i}")
    builds = {"n": 0}
    real = catalog_mod.list_session_catalog

    def wrapped(*args: object, **kwargs: object) -> object:
        builds["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", wrapped)
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    first = cache.get(force=True)
    second = cache.get()
    third = cache.get()
    assert len(first) == 80
    assert len(second) == 80
    assert len(third) == 80
    assert builds["n"] == 1
    assert cache.revision >= 1
    poll = cache.list_for_rpc(since_revision=cache.revision)
    assert poll["unchanged"] is True
    assert poll["sessions"] == []
    assert poll["matched"] == 80
    assert poll["total"] == 80


def test_catalog_list_for_rpc_delta_after_refresh(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    rev = cache.revision
    (one / "summary.json").write_text(
        json.dumps({"info": {"id": "one"}, "generated_title": "One updated"}),
        encoding="utf-8",
    )
    cache.refresh_rows([one])
    delta = cache.list_for_rpc(since_revision=rev)
    assert delta["delta"] is True
    assert delta["unchanged"] is False
    ids = [str(r["sessionId"]) for r in delta["sessions"]]
    assert ids == ["one"]
    assert any(r.get("title") == "One updated" for r in delta["sessions"])


def test_list_for_rpc_after_owner_restart_returns_full_snapshot(tmp_path: Path) -> None:
    """A new cache is a new generation: old sinceRevision must not look unchanged."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "alpha", "Alpha")
    _write_sess(traces, "beta", "Beta")
    owner1 = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    owner1.get(force=True)
    old_since = owner1.revision
    assert old_since > 0
    _write_sess(traces, "gamma", "Gamma")
    owner2 = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    owner2.get(force=True)
    listed = owner2.list_for_rpc(since_revision=old_since)
    ids = {str(r["sessionId"]) for r in listed["sessions"]}
    assert listed["unchanged"] is False
    assert listed["delta"] is False
    assert ids == {"alpha", "beta", "gamma"}
    assert listed["matched"] == 3
    assert listed["revision"] != old_since
    future = owner2.list_for_rpc(since_revision=old_since + 10**9)
    future_ids = {str(r["sessionId"]) for r in future["sessions"]}
    assert future["unchanged"] is False
    assert future["delta"] is False
    assert future_ids == {"alpha", "beta", "gamma"}


def test_fat_catalog_list_does_not_parse_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home-list rows for fat sessions must not parse updates.jsonl timelines."""
    from .highload_tree import write_fat_session

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    for i in range(6):
        write_fat_session(traces, f"fat-{i}", turns=50 + i, title=f"Fat {i}")
    parsed: list[str] = []

    def _boom(session_dir: Path) -> list[object]:
        parsed.append(Path(session_dir).name)
        raise AssertionError("parse_timeline must not run for catalog rows")

    monkeypatch.setattr(parser_mod, "parse_timeline", _boom)
    rows = list_session_catalog(work, traces_path=traces, include_host=False)
    assert len(rows) == 6
    assert parsed == []
    assert all(int(r.get("numEvents") or 0) >= 0 for r in rows)


def test_fat_overview_parses_only_opened_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overview + paged timeline for one fat session must not parse siblings."""
    from groket.session.control_views import build_session_overview, build_session_timeline

    from .highload_tree import write_fat_session

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    keep = write_fat_session(traces, "keep-fat", turns=60, title="Keep fat")
    write_fat_session(traces, "skip-fat", turns=80, title="Skip fat")
    parsed: list[str] = []
    real = parser_mod.parse_timeline

    def tracked(session_dir: Path) -> list[object]:
        parsed.append(Path(session_dir).name)
        return real(session_dir)

    import groket.session.control_views as views

    monkeypatch.setattr(parser_mod, "parse_timeline", tracked)
    monkeypatch.setattr(views, "parse_timeline", tracked)
    ov = build_session_overview(keep, work_dir=work)
    assert ov["turns"]["total"] >= 50
    tl = build_session_timeline(keep, offset=0, limit=40)
    assert tl["events"]
    assert "keep-fat" in parsed
    assert "skip-fat" not in parsed


@pytest.mark.asyncio
async def test_browser_bundle_reads_only_one_session(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    a = _write_sess(traces, "keep", "Keep")
    b = _write_sess(traces, "skip", "Skip")
    (a / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "only me"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    read_dirs: list[str] = []
    real_parse = parser_mod.parse_timeline

    def tracked(session_dir: Path) -> list[object]:
        read_dirs.append(Path(session_dir).name)
        return real_parse(session_dir)

    monkeypatch.setattr(parser_mod, "parse_timeline", tracked)

    class _Access:
        async def session_overview(self, session: str) -> dict:
            from groket.session.control_views import build_session_overview

            path = a if session in {a.name, str(a)} else b
            return build_session_overview(path)

        async def session_timeline(self, session: str, **kwargs: object) -> dict:
            from groket.session.control_views import build_session_timeline

            path = a if session in {a.name, str(a)} else b
            return build_session_timeline(
                path,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 50),
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    meta, events, _ov = await fetch_session_browser_bundle(_Access(), "keep", fallback_dir=a)
    assert meta.session_id == "keep"
    assert any("only me" in (e.content or "") for e in events)
    assert "skip" not in read_dirs
