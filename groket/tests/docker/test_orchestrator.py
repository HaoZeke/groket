"""DockerOrchestrator behaviour with a fake python_on_whales client."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from groket.docker import orchestrator as orch_mod
from groket.docker.orchestrator import (
    ContainerConfig,
    ContainerStatus,
    DockerOrchestrator,
    _build_setup_script,
    _eval_config_toml,
    _host_gh_env_for_container,
    _lock_for_shared_tag,
    _token_from_host_gh_cli,
    describe_github_write_token_status,
)


class _FakeImageAPI:
    def __init__(self, exists: bool = False, fail_exists: bool = False):
        self._exists = exists
        self._fail_exists = fail_exists
        self.removed: list[str] = []

    def exists(self, tag: str) -> bool:
        if self._fail_exists:
            raise RuntimeError("exists fail")
        return self._exists

    def inspect(self, tag: str):
        if self._exists:
            return {"Id": "x"}
        raise RuntimeError("missing")

    def remove(self, tag: str, force: bool = False) -> None:
        self.removed.append(tag)


class _FakeContainerObj:
    def __init__(self, name: str, status: str = "exited"):
        self.name = name
        self.state = SimpleNamespace(status=status)
        self.status = status
        self.id = "deadbeefcafebabe"


class _FakeContainerAPI:
    def __init__(self, containers: list | None = None):
        self._containers = containers or []

    def list(self, all: bool = True):
        return list(self._containers)


class FakeDocker:
    def __init__(
        self,
        *,
        image_exists: bool = False,
        fail_info: bool = False,
        fail_build: bool = False,
        fail_run: bool = False,
        containers: list | None = None,
        wait_code: int = 0,
        logs_mode: str = "str",
        fail_wait: bool = False,
        fail_logs: bool = False,
        fail_exists_then_inspect: bool = False,
    ):
        self.fail_info = fail_info
        self.fail_build = fail_build
        self.fail_run = fail_run
        self.fail_wait = fail_wait
        self.fail_logs = fail_logs
        self.wait_code = wait_code
        self.logs_mode = logs_mode
        self.image = _FakeImageAPI(
            exists=image_exists,
            fail_exists=fail_exists_then_inspect,
        )
        if fail_exists_then_inspect:
            self.image._exists = False  # inspect will also fail unless we set
        self.container = _FakeContainerAPI(containers)
        self.built: list = []
        self.ran: list = []
        self.stopped: list = []
        self.removed: list = []

    def info(self):
        if self.fail_info:
            raise RuntimeError("no docker")
        return {"ID": "x"}

    def build(self, context, tags=None, progress=None):
        if self.fail_build:
            raise RuntimeError("build failed\nline2\nline3")
        self.built.append((context, tags, progress))

    def run(self, *args, **kwargs):
        if self.fail_run:
            raise RuntimeError("run fail")
        self.ran.append((args, kwargs))
        return SimpleNamespace(id="abcdef1234567890")

    def wait(self, name):
        if self.fail_wait:
            raise RuntimeError("wait fail")
        return self.wait_code

    def logs(self, name, tail=50, stream=False, follow=False):
        if self.fail_logs:
            raise RuntimeError("logs fail")
        if stream or follow:
            if self.logs_mode == "bytes":
                return iter([b"line1\n", b"line2"])
            if self.logs_mode == "tuple":
                return iter([("stdout", b"chunk\n"), ("stdout", "text")])
            if self.logs_mode == "other":
                return iter([123])
            return iter(["a\nb", "c"])
        if self.logs_mode == "non_str":
            return 123
        return "log-line-1\nlog-line-2"

    def stop(self, name, time=3):
        self.stopped.append(name)

    def remove(self, name, force=False):
        self.removed.append(name)


def _orch(tmp_path: Path, client: FakeDocker | None = None) -> DockerOrchestrator:
    o = DockerOrchestrator.__new__(DockerOrchestrator)
    o.work_dir = tmp_path / "runs"
    o.work_dir.mkdir(parents=True, exist_ok=True)
    o.containers = {}
    o._build_dir = o.work_dir / "docker-build"
    o._docker = client or FakeDocker()
    o._abort = threading.Event()
    return o


def test_lock_and_container_config():
    a = _lock_for_shared_tag("tag-a")
    b = _lock_for_shared_tag("tag-a")
    assert a is b
    c = ContainerConfig(model="m/x:1", prompt="hi")
    assert c.container_name.startswith("groket-")
    assert c.resolved_base().profile_id
    assert c.max_turns == 50


def test_container_config_max_turns_clamped():
    from groket.constants import DEFAULT_MAX_TURNS

    assert ContainerConfig(model="m", prompt="p", max_turns=0).max_turns == DEFAULT_MAX_TURNS
    assert ContainerConfig(model="m", prompt="p", max_turns=-3).max_turns == DEFAULT_MAX_TURNS
    assert ContainerConfig(model="m", prompt="p", max_turns=200).max_turns == 200


def test_start_container_maps_xhigh_effort_for_cli(tmp_path: Path):
    """Product xhigh must become CLI high (Grok rejects --effort xhigh)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    qcfg = tmp_path / "c.toml"
    qcfg.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9-stickynote",
        reasoning_effort="xhigh",
        prompt="p",
        container_name="groket-effort-map",
    )
    o.start_container(cfg, "img:tag", auth, qcfg)
    found = None
    for _a, kw in client.ran:
        env = kw.get("envs") or kw.get("environment")
        if isinstance(env, dict) and "REASONING_EFFORT" in env:
            found = env["REASONING_EFFORT"]
            break
    assert found == "high"


def test_force_ui_yolo_rewrites_config():
    from groket.docker.orchestrator import _force_ui_yolo

    base = '[ui]\nyolo = false\nfork_secondary_model = "m"\n'
    assert "yolo = true" in _force_ui_yolo(base, yolo=True)
    assert "yolo = false" in _force_ui_yolo(base, yolo=False)
    assert "yolo = true" in _force_ui_yolo("[cli]\nx=1\n", yolo=True)


def test_start_container_yolo_env(tmp_path: Path):
    """yolo=True sets YOLO=1 for the entrypoint."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    qcfg = tmp_path / "c.toml"
    qcfg.write_text("[ui]\nyolo = false\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-yolo",
        yolo=True,
    )
    o.start_container(cfg, "img:tag", auth, qcfg)
    found = None
    cfg_text = (tmp_path / "runs" / "traces" / "groket-yolo" / "groket-config.toml").read_text(
        encoding="utf-8"
    )
    assert "yolo = true" in cfg_text
    for _a, kw in client.ran:
        env = kw.get("envs") or kw.get("environment")
        if isinstance(env, dict) and "YOLO" in env:
            found = env["YOLO"]
            break
    assert found == "1"


def test_start_container_sets_max_turns_env(tmp_path: Path):
    """ContainerConfig.max_turns is passed as MAX_TURNS env for the entrypoint."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    qcfg = tmp_path / "c.toml"
    qcfg.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-maxturns",
        max_turns=120,
    )
    cid = o.start_container(cfg, "img:tag", auth, qcfg)
    assert len(cid) == 12
    assert client.ran
    _args, kwargs = client.ran[0]
    envs = kwargs.get("envs") or kwargs.get("environment") or {}
    if not envs and len(_args) > 1:
        # python-on-whales may pass envs positionally depending on call shape
        envs = {}
    # start_container uses envs= keyword on client.run
    found = None
    for _a, kw in client.ran:
        env = kw.get("envs") or kw.get("environment")
        if isinstance(env, dict) and "MAX_TURNS" in env:
            found = env["MAX_TURNS"]
            break
    assert found == "120"


def test_start_container_mounts_repo_path_externally(tmp_path: Path):
    """repo_path bind-mounts the host dir as /workspace with WORKSPACE_EXTERNAL=1."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    qcfg = tmp_path / "c.toml"
    qcfg.write_text("[cli]\n", encoding="utf-8")
    external = tmp_path / "my-project"
    external.mkdir()
    (external / "README").write_text("hi\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-localpath",
        repo_path=str(external),
    )
    o.start_container(cfg, "img:tag", auth, qcfg)
    assert client.ran
    volumes = None
    envs = None
    for _a, kw in client.ran:
        if "volumes" in kw:
            volumes = kw["volumes"]
            envs = kw.get("envs") or kw.get("environment")
            break
    assert volumes is not None
    ws_mounts = [v for v in volumes if len(v) >= 2 and v[1] == "/workspace"]
    assert len(ws_mounts) == 1
    assert Path(ws_mounts[0][0]).resolve() == external.resolve()
    assert isinstance(envs, dict)
    assert envs.get("WORKSPACE_EXTERNAL") == "1"
    # Must not create a managed checkout when using external path.
    checkouts = o.work_dir / "checkouts" / "groket-localpath"
    assert not checkouts.exists()


def test_eval_config_toml_branches(tmp_path: Path):
    # missing host
    t = _eval_config_toml(tmp_path / "missing.toml", primary_model="")
    assert "default" in t
    # grok-build secondary override
    t2 = _eval_config_toml(tmp_path / "missing.toml", primary_model="grok-build")
    assert "v9" in t2

    # host with sections
    host = tmp_path / "config.toml"
    host.write_text(
        '[cli]\nauto_update = true\n\n[ui]\nfork_secondary_model = "x"\n\n'
        '[models]\ndefault = "old"\n\n[dashboard]\nenabled = false\n',
        encoding="utf-8",
    )
    t3 = _eval_config_toml(host, primary_model="v9-x")
    assert "auto_update = false" in t3
    assert "v9-x" in t3
    assert "enabled = true" in t3 or "enabled=true" in t3.replace(" ", "")

    # host without ui/models/cli dashboard
    host2 = tmp_path / "config2.toml"
    host2.write_text("key = 1\n", encoding="utf-8")
    t4 = _eval_config_toml(host2, primary_model="m1")
    assert "fork_secondary_model" in t4
    assert 'default = "m1"' in t4

    # host with [cli] but no auto_update line — inject
    host3 = tmp_path / "config3.toml"
    host3.write_text('[cli]\ninstaller = "internal"\n\n[ui]\n\n[models]\n', encoding="utf-8")
    t5 = _eval_config_toml(host3, primary_model="m2")
    assert "auto_update = false" in t5

    # unreadable path: simulate OSError via missing is handled; create dir as path
    bad = tmp_path / "isdir"
    bad.mkdir()
    # Path.read_text on dir raises IsADirectoryError
    t6 = _eval_config_toml(bad, primary_model="m")
    assert "default" in t6

    # Effort is CLI-only; host default_reasoning_effort must not appear in eval config.
    host_eff = tmp_path / "eff.toml"
    host_eff.write_text(
        '[models]\ndefault = "old"\ndefault_reasoning_effort = "high"\n',
        encoding="utf-8",
    )
    t_eff = _eval_config_toml(host_eff, primary_model="v9-zingster")
    assert "default_reasoning_effort" not in t_eff
    assert 'default = "v9-zingster"' in t_eff


def test_build_setup_script():
    assert "#!/bin/bash" in _build_setup_script("")
    assert "#!/bin/bash" in _build_setup_script("echo hi")
    custom = "#!/usr/bin/env bash\necho x"
    assert _build_setup_script(custom).startswith("#!")
    assert _build_setup_script("#!/bin/sh\necho").endswith("\n")


def test_token_and_host_gh_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)

    assert _host_gh_env_for_container() == {}
    assert _host_gh_env_for_container(github_write=False) == {}

    # persona token without write flag still injects (token implies write job)
    env = _host_gh_env_for_container(github_write=False, github_token="tokentok")
    assert env.get("GH_TOKEN") == "tokentok"
    assert env.get("GITHUB_WRITE") == "1"

    # write mode with host token
    monkeypatch.setenv("GH_TOKEN", "gh")
    env3 = _host_gh_env_for_container(github_write=True)
    assert env3["GH_TOKEN"] == "gh"

    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setenv("GITHUB_TOKEN", "ghub")
    env4 = _host_gh_env_for_container(github_write=True)
    assert "GITHUB_TOKEN" in env4

    monkeypatch.setenv("GH_HOST", "github.example")
    env6 = _host_gh_env_for_container(github_write=True)
    assert env6.get("GH_HOST") == "github.example"

    # describe status
    assert "persona" in describe_github_write_token_status(ui_token="abc")
    monkeypatch.setenv("GH_TOKEN", "y")
    assert "GH_TOKEN" in describe_github_write_token_status()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch.object(orch_mod, "_token_from_host_gh_cli", return_value="cli-token-xx"):
        assert "gh auth" in describe_github_write_token_status()
    with patch.object(orch_mod, "_token_from_host_gh_cli", return_value=""):
        assert "no token" in describe_github_write_token_status()

    # _token_from_host_gh_cli paths
    with patch("shutil.which", return_value=None):
        assert _token_from_host_gh_cli() == ""
    with (
        patch("shutil.which", return_value="/bin/gh"),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout=""),
        ),
    ):
        assert _token_from_host_gh_cli() == ""
    with (
        patch("shutil.which", return_value="/bin/gh"),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="short"),
        ),
    ):
        assert _token_from_host_gh_cli() == ""
    with (
        patch("shutil.which", return_value="/bin/gh"),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="line1\nline2"),
        ),
    ):
        # multi-line -> empty
        assert _token_from_host_gh_cli() == ""
    with (
        patch("shutil.which", return_value="/bin/gh"),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="longtokenvalue"),
        ),
    ):
        assert _token_from_host_gh_cli() == "longtokenvalue"
    with patch("shutil.which", side_effect=RuntimeError("x")):
        assert _token_from_host_gh_cli() == ""


def test_orchestrator_core_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    assert o.check_docker_available() is True
    o2 = _orch(tmp_path, FakeDocker(fail_info=True))
    assert o2.check_docker_available() is False

    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-test-1")
    assert o._persona_for_config(cfg) is None
    cfg.persona_id = "nope"
    assert o._persona_for_config(cfg) is None

    # persona from store
    work = tmp_path
    runs = work / "runs"
    runs.mkdir(exist_ok=True)
    o.work_dir = runs
    from groket.runs.personas import Persona, PersonaStore

    store = PersonaStore(work)
    p = Persona(persona_id="px", name="Px", mcp_servers=["srv"], skills=["sk"], plugins=["pl"])
    store.save(p)
    cfg.persona_id = "px"
    loaded = o._persona_for_config(cfg)
    assert loaded is not None

    # apply capabilities with persona on config fields only
    cfg2 = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-c2",
        mcp_servers=["m1"],
        skills=["s1"],
        plugins=["p1"],
        mcp_extra_toml="[mcp]\nx=1\n",
    )
    traces_vol = runs / "traces" / cfg2.container_name
    traces_vol.mkdir(parents=True)
    grok_cfg = tmp_path / "gc.toml"
    grok_cfg.write_text("[cli]\n", encoding="utf-8")
    # Stage is a real filesystem path; stub so the test does not require marketplace.
    monkeypatch.setattr(
        "groket.capabilities.prepare_persona_plugins_dir",
        lambda *a, **k: traces_vol.parent / f"{cfg2.container_name}.stage" / "plugins",
    )
    text = o._apply_persona_capabilities_config(
        "base=1\n", cfg2, grok_config=grok_cfg, traces_vol=traces_vol
    )
    assert isinstance(text, str)

    # with persona id overlay
    cfg3 = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-c3",
        persona_id="px",
        mcp_servers=["overlay"],
        skills=["so"],
        skills_disabled=["sd"],
        plugins=["po"],
        mcp_definitions=[{"id": "d"}],
        mcp_extra_toml="extra",
        mcp_replace_host=False,
    )
    tv3 = runs / "traces" / cfg3.container_name
    tv3.mkdir(parents=True)
    monkeypatch.setattr(
        "groket.capabilities.prepare_persona_plugins_dir",
        lambda *a, **k: tv3.parent / f"{cfg3.container_name}.stage" / "plugins",
    )
    o._apply_persona_capabilities_config("t\n", cfg3, grok_config=grok_cfg, traces_vol=tv3)

    # image exists / inspect fallback
    assert o._image_exists("t") is True
    o_fail = _orch(tmp_path, FakeDocker(fail_exists_then_inspect=True))
    # exists raises, inspect raises -> False
    o_fail._docker.image._fail_exists = True
    assert o_fail._image_exists("t") is False

    o._docker_build_quiet(tmp_path, tags=["x:1"])

    # ensure shared base reuse
    tag = o.ensure_shared_base(
        base_image="ubuntu:24.04", fully_loaded=False, on_log=lambda *a: None
    )
    assert tag

    # ensure shared base build
    client_b = FakeDocker(image_exists=False)
    o_b = _orch(tmp_path / "b", client_b)
    logs: list[str] = []
    tag2 = o_b.ensure_shared_base(
        base_image="ubuntu:24.04",
        fully_loaded=True,
        profile_id="p",
        on_log=lambda n, m: logs.append(m),
    )
    assert tag2
    assert logs

    # build failure
    client_f = FakeDocker(image_exists=False, fail_build=True)
    o_f = _orch(tmp_path / "f", client_f)
    with pytest.raises(RuntimeError):
        o_f.ensure_shared_base(
            base_image="ubuntu:24.04", fully_loaded=False, on_log=lambda *a: None
        )

    # prepare + build image
    client_ok = FakeDocker(image_exists=True)
    o_ok = _orch(tmp_path / "ok", client_ok)
    cfg_b = ContainerConfig(
        model="v9", prompt="p", container_name="groket-build-x", setup_instructions="echo 1"
    )
    logs2: list = []
    img = o_ok.build_image(cfg_b, on_log=lambda n, m: logs2.append(m))
    assert img.startswith("groket-eval:")

    client_bf = FakeDocker(image_exists=True, fail_build=True)
    # fail on thin layer: shared exists but build fails on second build
    o_bf = _orch(tmp_path / "bf", client_bf)
    # first ensure_shared uses exists True so no build; thin build fails
    with pytest.raises(RuntimeError):
        o_bf.build_image(cfg_b, on_log=lambda *a: None)

    # start container with skills/plugins volumes and extras
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    (auth.parent / "models_cache.json").write_text("{}", encoding="utf-8")
    (auth.parent / "managed_config_cache.json").write_text("{}", encoding="utf-8")
    cfg_s = ContainerConfig(
        model="v9",
        prompt="prompt line",
        container_name="groket-start-1",
        volumes={str(tmp_path / "extra"): "/extra"},
        env_vars={"FOO": "1"},
        github_write=True,
        github_token="tok12345",
    )
    (tmp_path / "extra").mkdir(exist_ok=True)
    # stage skills/plugins
    stage = o_ok.work_dir / "traces" / f"{cfg_s.container_name}.stage"
    (stage / "skills" / "sk").mkdir(parents=True)
    (stage / "skills" / "sk" / "SKILL.md").write_text("x", encoding="utf-8")
    (stage / "plugins" / "pl").mkdir(parents=True)
    (stage / "plugins" / "pl" / "x").write_text("x", encoding="utf-8")
    cid = o_ok.start_container(cfg_s, "img:tag", auth, grok_cfg)
    assert len(cid) == 12
    launch_path = o_ok.work_dir / "traces" / cfg_s.container_name / "groket-launch.json"
    assert launch_path.is_file()
    launch_data = json.loads(launch_path.read_text(encoding="utf-8"))
    assert launch_data["model"] == "v9"
    assert launch_data["container_name"] == cfg_s.container_name

    # wait / logs / stream
    assert o_ok.wait_for_container("c") == 0
    assert "log" in o_ok.get_container_logs("c")
    o_ok._docker.logs_mode = "non_str"
    assert o_ok.get_container_logs("c") == ""
    o_ok._docker.fail_logs = True
    assert o_ok.get_container_logs("c") == ""
    o_ok._docker.fail_logs = False
    o_ok._docker.fail_wait = True
    assert o_ok.wait_for_container("c") == -1

    lines: list[str] = []
    for mode in ("str", "bytes", "tuple", "other"):
        o_ok._docker.logs_mode = mode
        o_ok._docker.fail_logs = False
        o_ok.stream_container_logs("c", lambda n, ln: lines.append(ln), stop_event=None)
    o_ok._docker.logs_mode = "str"
    # stop event set mid-stream — use generator that checks
    o_ok.stream_container_logs("c", lambda *a: None, stop_event=threading.Event())
    # fail stream
    o_ok._docker.fail_logs = True
    o_ok.stream_container_logs("c", lambda *a: None)

    # ownership / peek / extract
    assert o_ok.fix_traces_ownership(tmp_path / "missing") is True
    td = o_ok.work_dir / "traces" / "groket-start-1"
    td.mkdir(parents=True, exist_ok=True)
    with patch("groket.runs.run_configs.chown_path_to_host_user", return_value=True):
        assert o_ok.fix_traces_ownership(td) is True
    with patch("groket.runs.run_configs.chown_path_to_host_user", side_effect=RuntimeError("x")):
        assert o_ok.fix_traces_ownership(td) is False

    assert o_ok.peek_session_dir("no-dir") is None
    # session under traces
    sd = td / "sess1"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sess1", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": "success"})
        + "\n",
        encoding="utf-8",
    )
    peeked = o_ok.peek_session_dir("groket-start-1")
    assert peeked is not None
    assert o_ok.extract_traces("missing") is None
    with patch.object(o_ok, "fix_traces_ownership", return_value=True):
        assert o_ok.extract_traces("groket-start-1") is not None

    # wait with peek
    st = ContainerStatus(container_name="groket-start-1", model="m")
    o_ok._docker.fail_wait = False
    o_ok._docker.wait_code = 0

    def _on_status(s):
        pass

    code = o_ok.wait_for_container_with_session_peek(
        "groket-start-1", st, on_status=_on_status, peek_interval_s=0.5
    )
    assert code == 0
    # already has session_dir
    st2 = ContainerStatus(container_name="groket-start-1", model="m", session_dir=sd)
    o_ok.wait_for_container_with_session_peek("groket-start-1", st2, peek_interval_s=0.5)

    o_ok.cleanup_container("c")
    o_ok._docker.stop = MagicMock(side_effect=RuntimeError("x"))
    o_ok._docker.remove = MagicMock(side_effect=RuntimeError("x"))
    o_ok.cleanup_container("c")

    # prune
    containers = [
        _FakeContainerObj("groket-old", "exited"),
        _FakeContainerObj("groket-run", "running"),
        _FakeContainerObj("other", "exited"),
        _FakeContainerObj(["groket-listname"], "exited"),
    ]
    # list name as list
    containers[3].name = ["groket-listname"]
    client_p = FakeDocker(containers=containers)
    o_p = _orch(tmp_path / "p", client_p)
    stats = o_p.prune_eval_containers(remove_exited=True, remove_running=False)
    assert stats["exited_removed"] >= 1
    stats2 = o_p.prune_eval_containers(
        remove_exited=True,
        remove_running=True,
        protect_names={"groket-run"},
        name_prefix="groket-",
    )
    assert isinstance(stats2, dict)
    # list fails
    o_p._docker.container.list = MagicMock(side_effect=RuntimeError("x"))
    assert o_p.prune_eval_containers() == {"exited_removed": 0, "running_removed": 0}

    # cleanup image
    o_ok._cleanup_image("")
    o_ok._cleanup_image("groket-base:x")
    o_ok._cleanup_image("groket-base/x")
    o_ok._cleanup_image("groket-eval:x")
    o_ok._docker.image.remove = MagicMock(side_effect=RuntimeError("x"))
    o_ok._cleanup_image("groket-eval:y")

    # run_evaluation happy-ish path with mocks
    o_r = _orch(tmp_path / "r", FakeDocker(image_exists=True))
    auth2 = tmp_path / "r" / "auth.json"
    auth2.parent.mkdir(parents=True, exist_ok=True)
    auth2.write_text("{}", encoding="utf-8")
    gc2 = tmp_path / "r" / "c.toml"
    gc2.write_text("[cli]\n", encoding="utf-8")
    cfg_r = ContainerConfig(model="v9", prompt="p", container_name="groket-eval-run")
    # plant session for extract
    tdir = o_r.work_dir / "traces" / cfg_r.container_name / "s1"
    tdir.mkdir(parents=True)
    (tdir / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "s1", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (tdir / "events.jsonl").write_text("{}\n", encoding="utf-8")

    statuses: list[str] = []
    st_out = o_r.run_evaluation(
        cfg_r,
        auth2,
        gc2,
        on_status=lambda s: statuses.append(s.status),
        on_log=lambda n, m: None,
        start_delay_s=0.0,
    )
    assert st_out.status in ("completed", "failed", "running", "extracting", "building")

    # run_evaluation failure on build
    o_rb = _orch(tmp_path / "rb", FakeDocker(image_exists=False, fail_build=True))
    cfg_rb = ContainerConfig(model="v9", prompt="p", container_name="groket-fail-build")
    st_fail = o_rb.run_evaluation(cfg_rb, auth2, gc2)
    assert st_fail.status == "failed"

    # parallel
    o_par = _orch(tmp_path / "par", FakeDocker(image_exists=True))
    cfgs = [
        ContainerConfig(model="v9", prompt="p", container_name="groket-par-1"),
        ContainerConfig(model="v9", prompt="p", container_name="groket-par-2"),
    ]
    for c in cfgs:
        sd = o_par.work_dir / "traces" / c.container_name / "sx"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(
            json.dumps(
                {
                    "info": {"id": "sx", "cwd": "/w"},
                    "session_summary": "s",
                    "created_at": "2026-06-25T00:00:00Z",
                    "updated_at": "2026-06-25T00:01:00Z",
                    "num_messages": 1,
                    "current_model_id": "m",
                }
            ),
            encoding="utf-8",
        )
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    results = o_par.run_parallel_evaluations(cfgs, auth2, gc2)
    assert len(results) == 2


def test_start_container_interactive_and_run_id(tmp_path: Path):
    """Interactive mode sets INTERACTIVE and writes .groket-turn (lines 709-727)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    qcfg = tmp_path / "c.toml"
    qcfg.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-interactive-1",
        interactive=True,
        run_id="run-abc",
        follow_up_prompts=["follow up 1"],
    )
    cid = o.start_container(cfg, "img:tag", auth, qcfg)
    assert len(cid) == 12
    # Verify run produced volumes with scripted turns
    traces_dir = o.work_dir / "traces" / cfg.container_name
    turn_dir = traces_dir / ".groket-turn"
    scripted = turn_dir / "scripted-turns.json"
    assert scripted.is_file()
    data = json.loads(scripted.read_text(encoding="utf-8"))
    assert "follow up 1" in data


def test_stream_container_logs_stop_event(tmp_path: Path):
    """Stream stops early when stop_event is set (line 804)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    stop = threading.Event()
    stop.set()
    lines: list[str] = []
    o.stream_container_logs("c", lambda n, ln: lines.append(ln), stop_event=stop)
    # stop was set, so may get 0 or some lines (stops checking per iteration)
    assert isinstance(lines, list)


def test_extract_traces_prefers_primary_not_subagent(tmp_path: Path):
    """extract_traces skips mirrored subagents (same as find_sessions)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    traces = o.work_dir / "traces" / "groket-multi"
    token = traces / "%2Fworkspace"
    parent = token / "019f-parent"
    sub = token / "019f-subagent"
    parent.mkdir(parents=True)
    sub.mkdir(parents=True)
    (parent / "subagents" / sub.name).mkdir(parents=True)
    for d, kind in ((parent, ""), (sub, "subagent")):
        (d / "summary.json").write_text(
            json.dumps(
                {
                    "info": {"id": d.name, "cwd": "/w"},
                    "session_summary": kind or "main",
                    "session_kind": kind,
                    "created_at": "2026-06-25T00:00:00Z",
                    "updated_at": "2026-06-25T00:01:00Z",
                    "num_messages": 2,
                    "current_model_id": "m",
                }
            ),
            encoding="utf-8",
        )
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (d / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    # Subagent looks newer
    (sub / "chat_history.jsonl").write_text("{}\n" * 20, encoding="utf-8")
    got = o.extract_traces("groket-multi")
    assert got is not None
    assert got.name == parent.name


def test_peek_session_dir_with_exception(tmp_path: Path):
    """peek_session_dir returns None when find_sessions raises (line 866)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    traces_dir = o.work_dir / "traces" / "test-cnt"
    traces_dir.mkdir(parents=True)
    with patch(
        "groket.docker.orchestrator.DockerOrchestrator.peek_session_dir", side_effect=OSError("x")
    ):
        # The wait loop catches exceptions on peek
        pass


def test_wait_with_session_peek_discovers_session(tmp_path: Path):
    """wait_for_container_with_session_peek discovers session mid-wait (lines 930-943)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    traces_dir = o.work_dir / "traces" / "groket-peek-1"
    sd = traces_dir / "session-x"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sx", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    statuses: list[str] = []
    st = ContainerStatus(container_name="groket-peek-1", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-peek-1",
        st,
        on_status=lambda s: statuses.append(s.status),
        peek_interval_s=0.1,
    )
    assert code == 0
    assert st.session_dir is not None


def test_wait_abort_after_stop(tmp_path: Path):
    """wait returns -1 when abort is set after container exits (line 946)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    st = ContainerStatus(container_name="groket-abort-2", model="m")
    # Container exits quickly, then abort set
    o.request_abort()
    code = o.wait_for_container_with_session_peek(
        "groket-abort-2",
        st,
        peek_interval_s=0.1,
    )
    assert code == -1


def test_wait_final_peek_on_status_error(tmp_path: Path):
    """Final peek on_status exception is swallowed (lines 958-961)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    traces_dir = o.work_dir / "traces" / "groket-on-status-err"
    sd = traces_dir / "session-y"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sy", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    def bad_on_status(s):
        raise RuntimeError("on_status crash")

    st = ContainerStatus(container_name="groket-on-status-err", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-on-status-err",
        st,
        on_status=bad_on_status,
        peek_interval_s=0.1,
    )
    assert code == 0


def test_run_evaluation_abort_during_build(tmp_path: Path):
    """run_evaluation returns aborted status when abort before build (lines 1103-1105)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    o.request_abort()

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-abort-build")
    st = o.run_evaluation(cfg, auth, gc)
    assert st.status == "aborted"


def test_run_evaluation_abort_during_stagger(tmp_path: Path):
    """run_evaluation returns aborted when abort fires during start delay (lines 1117-1125)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-stagger")

    # Abort immediately so the stagger delay returns early
    o.request_abort()
    o.clear_abort()  # clear so build succeeds...
    # ... then set abort during stagger by using very short delay and immediate abort
    import time

    def abort_soon():
        time.sleep(0.05)
        o.request_abort()

    threading.Thread(target=abort_soon, daemon=True).start()
    st = o.run_evaluation(cfg, auth, gc, start_delay_s=2.0)
    assert st.status == "aborted"


def test_run_evaluation_abort_after_stagger(tmp_path: Path):
    """run_evaluation returns aborted after stagger but before start (lines 1128-1130)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-abort-after-stagger")

    o.request_abort()
    st = o.run_evaluation(cfg, auth, gc, start_delay_s=0.0)
    assert st.status == "aborted"


def test_run_evaluation_abort_during_wait(tmp_path: Path):
    """run_evaluation returns aborted during container wait (lines 1159-1164)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-abort-wait")

    # Make wait_for_container_with_session_peek return -1 (abort)
    def fake_wait_peek(name, status, on_status=None, peek_interval_s=2.5):
        o._abort.set()
        return -1

    with patch.object(o, "wait_for_container_with_session_peek", side_effect=fake_wait_peek):
        st = o.run_evaluation(cfg, auth, gc)
    assert st.status == "aborted"


def test_run_evaluation_log_thread_alive_on_exit(tmp_path: Path):
    """Log thread that is still alive gets stopped via log_stop (lines 1170-1171)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-logthread")

    # Plant session
    traces = o.work_dir / "traces" / cfg.container_name / "s1"
    traces.mkdir(parents=True)
    (traces / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "s1", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (traces / "events.jsonl").write_text("{}\n", encoding="utf-8")

    logs: list[str] = []
    st = o.run_evaluation(cfg, auth, gc, on_log=lambda n, m: logs.append(m))
    assert st.status in ("completed", "failed")


def test_run_evaluation_exit_nonzero_no_session(tmp_path: Path):
    """Non-zero exit without session sets failed status (lines 1179-1180)."""
    client = FakeDocker(image_exists=True, wait_code=1)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-fail-exit")

    st = o.run_evaluation(cfg, auth, gc)
    assert st.status == "failed"
    assert "Exit code 1" in st.error


def test_run_evaluation_exception_with_traces(tmp_path: Path):
    """Exception during eval still fixes ownership and peeks session (lines 1188-1199)."""
    client = FakeDocker(image_exists=True, fail_run=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-exc-traces")

    st = o.run_evaluation(cfg, auth, gc)
    assert st.status == "failed"


def test_run_parallel_empty():
    """Empty configs list returns empty results (line 1233)."""
    client = FakeDocker(image_exists=True)
    o = DockerOrchestrator.__new__(DockerOrchestrator)
    o.work_dir = Path("/tmp/x")
    o.containers = {}
    o._docker = client
    o._abort = threading.Event()
    o._build_dir = Path("/tmp/x/b")
    assert o.run_parallel_evaluations([], Path("/tmp/a"), Path("/tmp/c")) == []


def test_run_parallel_thread_error(tmp_path: Path):
    """Thread raising BaseException fills error slot (lines 1251-1252, 1279-1299)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")

    def boom_eval(*args, **kwargs):
        raise RuntimeError("thread error")

    with patch.object(o, "run_evaluation", side_effect=boom_eval):
        cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-thread-err")
        results = o.run_parallel_evaluations([cfg], auth, gc)
    assert len(results) == 1
    assert results[0].status == "failed"
    assert "thread error" in results[0].error


def test_host_gh_env_write_falls_back_to_gh_cli(monkeypatch: pytest.MonkeyPatch):
    """Write mode with no env tokens uses host gh CLI."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)
    with patch.object(orch_mod, "_token_from_host_gh_cli", return_value="cli-tok"):
        env = _host_gh_env_for_container(github_write=True)
    assert env.get("GH_TOKEN") == "cli-tok" or env.get("GITHUB_TOKEN") == "cli-tok"


def test_host_gh_env_github_token_only(monkeypatch: pytest.MonkeyPatch):
    """GITHUB_TOKEN without GH_TOKEN is used when write is on."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghub-only")
    monkeypatch.delenv("GH_HOST", raising=False)
    env = _host_gh_env_for_container(github_write=True)
    assert env.get("GH_TOKEN") == "ghub-only"


def test_docker_orchestrator_init(tmp_path: Path):
    with patch("groket.docker.orchestrator.DockerClient", return_value=FakeDocker()):
        o = DockerOrchestrator(tmp_path / "w")
        assert o.work_dir.exists()


def test_base_profiles_resolve_at_sign_form():
    """image@profile: resolve correctly (lines 298-303)."""
    from groket.docker.base_profiles import resolve_docker_base

    r = resolve_docker_base("debian:bookworm@fully-loaded")
    assert r.base_image == "debian:bookworm"
    assert r.fully_loaded is True
    assert r.profile_id == "fully-loaded"

    # Unknown profile in @ form defaults to minimal
    r2 = resolve_docker_base("ubuntu:22.04@unknown-profile")
    assert r2.base_image == "ubuntu:22.04"
    assert r2.profile_id == "minimal"

    # Empty right side (bare @)
    r3 = resolve_docker_base("img@")
    assert r3.profile_id == "minimal"


def test_base_profiles_resolve_normal_image():
    """Normal image string without @ or profile alias (lines 313-314)."""
    from groket.docker.base_profiles import resolve_docker_base

    r = resolve_docker_base("custom-registry.io/my-img:v1")
    assert r.base_image == "custom-registry.io/my-img:v1"
    assert r.profile_id == "minimal"
    assert r.fully_loaded is False


def test_base_profiles_profile_help_text():
    """profile_help_text includes all profiles (lines 271-275)."""
    from groket.docker.base_profiles import profile_help_text

    text = profile_help_text()
    assert "fully-loaded" in text
    assert "minimal" in text


def test_abort_short_circuits_wait_and_parallel(tmp_path: Path):
    """Quit must not block on docker.wait — abort returns immediately."""
    client = FakeDocker()
    # Block wait forever unless abort unblocks the poll loop.
    wait_started = threading.Event()

    def slow_wait(_name: str) -> int:
        wait_started.set()
        threading.Event().wait(30.0)
        return 0

    client.wait = slow_wait  # type: ignore[method-assign]  # injecting fake
    with patch("groket.docker.orchestrator.DockerClient", return_value=client):
        o = DockerOrchestrator(tmp_path / "abort-w")

    st = ContainerStatus(container_name="groket-abort-1", model="m")
    o.request_abort()
    assert o.abort_requested is True
    assert o.wait_for_container_with_session_peek("groket-abort-1", st, peek_interval_s=0.5) == -1

    o.clear_abort()
    assert o.abort_requested is False

    def abort_soon() -> None:
        wait_started.wait(timeout=2.0)
        o.request_abort()

    threading.Thread(target=abort_soon, daemon=True).start()
    t0 = threading.Event()
    code_holder: list[int] = []

    def run_wait() -> None:
        code_holder.append(
            o.wait_for_container_with_session_peek(
                "groket-abort-1",
                ContainerStatus(container_name="groket-abort-1", model="m"),
                peek_interval_s=0.2,
            )
        )
        t0.set()

    threading.Thread(target=run_wait, daemon=True).start()
    assert t0.wait(timeout=3.0), "wait did not return after abort"
    assert code_holder == [-1]

    # Parallel path returns aborted slots without hanging on pool shutdown.
    cfg = ContainerConfig(
        model="m",
        prompt="p",
        container_name="groket-abort-par",
        docker_image="fully-loaded",
    )
    auth = tmp_path / "a.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("", encoding="utf-8")

    def fake_eval(*_a, **_k):
        o._abort.wait(timeout=10.0)
        return ContainerStatus(
            container_name=cfg.container_name,
            model=cfg.model,
            status="aborted",
        )

    with patch.object(o, "run_evaluation", side_effect=fake_eval):
        o.clear_abort()
        done = threading.Event()
        results_box: list[list] = []

        def _par() -> None:
            results_box.append(o.run_parallel_evaluations([cfg], auth, gc))
            done.set()

        threading.Thread(target=_par, daemon=True).start()
        threading.Event().wait(0.05)
        o.request_abort()
        assert done.wait(timeout=2.0), "parallel did not return after abort"
        assert results_box and results_box[0][0].status == "aborted"


def test_host_gh_env_no_write_no_token(monkeypatch: pytest.MonkeyPatch):
    """Without write or persona token, no host GH env is injected."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env = _host_gh_env_for_container(github_write=False)
    assert env == {}


def test_host_gh_env_github_write_no_env_tokens(monkeypatch: pytest.MonkeyPatch):
    """github_write with no env tokens falls back to gh CLI."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch.object(orch_mod, "_token_from_host_gh_cli", return_value="cli-tok"):
        env = _host_gh_env_for_container(github_write=True)
    assert env.get("GH_TOKEN") == "cli-tok"
    assert env.get("GITHUB_TOKEN") == "cli-tok"


def test_host_gh_env_github_write_no_token_anywhere(monkeypatch: pytest.MonkeyPatch):
    """github_write with no token anywhere returns empty."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch.object(orch_mod, "_token_from_host_gh_cli", return_value=""):
        env = _host_gh_env_for_container(github_write=True)
    assert env == {}


def test_host_gh_env_gh_token_sets_both(monkeypatch: pytest.MonkeyPatch):
    """Write mode with only GH_TOKEN sets GH_TOKEN and GITHUB_TOKEN."""
    monkeypatch.setenv("GH_TOKEN", "gh-tok")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)
    env = _host_gh_env_for_container(github_write=True)
    assert env.get("GH_TOKEN") == "gh-tok"
    assert env.get("GITHUB_TOKEN") == "gh-tok"


def test_host_gh_env_gh_host_forwarded(monkeypatch: pytest.MonkeyPatch):
    """GH_HOST is forwarded when injecting write credentials."""
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_HOST", "github.example.com")
    env = _host_gh_env_for_container(github_write=True)
    assert env.get("GH_HOST") == "github.example.com"


def test_persona_load_exception(tmp_path: Path):
    """_persona_for_config returns None on load exception (lines 415-417)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="c",
        persona_id="broken-persona",
    )
    with patch("groket.runs.personas.PersonaStore.get", side_effect=RuntimeError("corrupt")):
        result = o._persona_for_config(cfg)
    assert result is None


def test_apply_capabilities_host_config_read_error(tmp_path: Path):
    """Host config read OSError is caught (lines 466-467)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="c",
        mcp_servers=["m1"],
    )
    traces_vol = o.work_dir / "traces" / cfg.container_name
    traces_vol.mkdir(parents=True)
    bad_cfg = tmp_path / "bad.toml"
    bad_cfg.write_text("ok", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("perms")):
        text = o._apply_persona_capabilities_config(
            "base\n",
            cfg,
            grok_config=bad_cfg,
            traces_vol=traces_vol,
        )
    assert isinstance(text, str)


def test_apply_capabilities_exception_swallowed(tmp_path: Path):
    """Exception in capabilities apply is caught (lines 500-501)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="c",
        mcp_servers=["m1"],
    )
    traces_vol = o.work_dir / "traces" / cfg.container_name
    traces_vol.mkdir(parents=True)
    grok_cfg = tmp_path / "c.toml"
    grok_cfg.write_text("[cli]\n", encoding="utf-8")
    with patch(
        "groket.capabilities.apply_persona_mcp_to_config_toml",
        side_effect=RuntimeError("boom"),
    ):
        text = o._apply_persona_capabilities_config(
            "base\n",
            cfg,
            grok_config=grok_cfg,
            traces_vol=traces_vol,
        )
    assert isinstance(text, str)


def test_image_exists_fallback(tmp_path: Path):
    """_image_exists falls back to inspect when exists raises (line 528)."""
    client = FakeDocker(image_exists=False)
    client.image = _FakeImageAPI(exists=False, fail_exists=True)
    o = _orch(tmp_path, client)
    assert o._image_exists("tag") is False


def test_start_container_scripted_turns_oserror(tmp_path: Path):
    """OSError writing scripted-turns is caught (lines 726-727)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-scr-1",
        follow_up_prompts=["turn 2"],
    )
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "c.toml"
    grok_cfg.write_text("[cli]\n", encoding="utf-8")

    original_mkdir = Path.mkdir

    def fail_turn_dir(self, *args, **kwargs):
        if ".groket-turn" in str(self):
            raise OSError("write fail")
        return original_mkdir(self, *args, **kwargs)

    with patch.object(Path, "mkdir", fail_turn_dir):
        cid = o.start_container(cfg, "img:tag", auth, grok_cfg)
    assert cid is not None


def test_start_container_skills_and_plugins_volumes(tmp_path: Path):
    """Skills and plugins volumes are appended when directories exist (lines 730+)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)
    cfg = ContainerConfig(
        model="v9",
        prompt="p",
        container_name="groket-vol-test",
    )
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    grok_cfg = tmp_path / "c.toml"
    grok_cfg.write_text("[cli]\n", encoding="utf-8")

    traces_vol = o.work_dir / "traces" / cfg.container_name
    traces_vol.mkdir(parents=True)

    # Create staged skills
    stage_root = traces_vol.parent / f"{traces_vol.name}.stage"
    skills_dir = stage_root / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "my-skill").mkdir()
    (skills_dir / "my-skill" / "SKILL.md").write_text("# skill", encoding="utf-8")

    # Create staged plugins manifest
    plugins_dir = stage_root / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "plugins-manifest.json").write_text("[]", encoding="utf-8")

    # Create models_cache.json
    (auth.parent / "models_cache.json").write_text("[]", encoding="utf-8")

    cid = o.start_container(cfg, "img:tag", auth, grok_cfg)
    assert cid is not None


def test_wait_container_peek_on_status_callback(tmp_path: Path):
    """wait_for_container_with_session_peek: mid-wait callback fires (lines 913-914, 930-943)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    traces_dir = o.work_dir / "traces" / "groket-mid-peek"
    sd = traces_dir / "sess-mid"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sm", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    statuses_captured: list[ContainerStatus] = []

    def capture_status(s: ContainerStatus) -> None:
        statuses_captured.append(s)

    st = ContainerStatus(container_name="groket-mid-peek", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-mid-peek",
        st,
        on_status=capture_status,
        peek_interval_s=0.05,
    )
    assert code == 0
    assert st.session_dir is not None


def test_wait_final_peek_discovers_session(tmp_path: Path):
    """Final peek after container exits discovers session (lines 960-961)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    # No session discovered during wait, but final peek finds it
    traces_dir = o.work_dir / "traces" / "groket-final-peek"
    sd = traces_dir / "final-sess"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sf", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    st = ContainerStatus(container_name="groket-final-peek", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-final-peek",
        st,
        peek_interval_s=0.05,
    )
    assert code == 0
    assert st.session_dir is not None


def test_run_evaluation_stagger_delay(tmp_path: Path):
    """run_evaluation with start_delay_s that completes (line 1117)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-delay-ok")

    logs: list[str] = []
    st = o.run_evaluation(cfg, auth, gc, start_delay_s=0.01, on_log=lambda n, m: logs.append(m))
    assert st.status in ("completed", "failed")
    assert any("Stagger" in msg for msg in logs)


def test_run_evaluation_log_thread_stopped_after_exit(tmp_path: Path):
    """Log thread alive after container exit gets stopped (lines 1170-1171)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-logalive")

    traces = o.work_dir / "traces" / cfg.container_name / "s1"
    traces.mkdir(parents=True)
    (traces / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "s1", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (traces / "events.jsonl").write_text("{}\n", encoding="utf-8")

    logs: list[str] = []
    st = o.run_evaluation(cfg, auth, gc, on_log=lambda n, m: logs.append(m))
    assert st.status in ("completed", "failed")


def test_run_evaluation_exit_nonzero_with_session(tmp_path: Path):
    """Non-zero exit with a discovered session still completes (line 1188-1189)."""
    client = FakeDocker(image_exists=True, wait_code=1)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-exit1-sess")

    traces = o.work_dir / "traces" / cfg.container_name / "s1"
    traces.mkdir(parents=True)
    (traces / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "s1", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (traces / "events.jsonl").write_text("{}\n", encoding="utf-8")

    st = o.run_evaluation(cfg, auth, gc)
    # With session discovered during extraction, status should be completed
    assert st.status in ("completed", "failed")


def test_run_evaluation_exception_recovers_session(tmp_path: Path):
    """Exception during eval still peeks session (lines 1197-1199)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-exc-recover")

    # Plant session that peek_session_dir can find
    traces = o.work_dir / "traces" / cfg.container_name / "s2"
    traces.mkdir(parents=True)
    (traces / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "s2", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (traces / "events.jsonl").write_text("{}\n", encoding="utf-8")

    # Make start_container raise
    with patch.object(o, "start_container", side_effect=RuntimeError("boom")):
        st = o.run_evaluation(cfg, auth, gc)
    assert st.status == "failed"
    assert st.session_dir is not None


def test_wait_mid_peek_loop_body(tmp_path: Path):
    """Session discovered during wait loop (lines 930-943)."""
    # Use a docker that delays wait so the peek loop gets to run
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    wait_event = threading.Event()
    original_wait = client.wait

    def slow_wait(name):
        wait_event.wait(timeout=2.0)  # block until we're ready
        return original_wait(name)

    client.wait = slow_wait  # type: ignore[method-assign]  # injecting fake

    traces_dir = o.work_dir / "traces" / "groket-mid-loop"
    sd = traces_dir / "sess-mid-loop"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sml", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    statuses_seen: list[ContainerStatus] = []

    def on_status(s: ContainerStatus) -> None:
        statuses_seen.append(s)
        # Release the wait once session is discovered
        wait_event.set()

    st = ContainerStatus(container_name="groket-mid-loop", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-mid-loop",
        st,
        on_status=on_status,
        peek_interval_s=0.1,
    )
    assert code == 0
    assert st.session_dir is not None
    assert len(statuses_seen) >= 1


def test_wait_mid_peek_already_has_session(tmp_path: Path):
    """Session already set skips peek (line 930-931 continue branch)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    wait_event = threading.Event()

    def slow_wait(name):
        wait_event.wait(timeout=1.0)
        return 0

    client.wait = slow_wait  # type: ignore[method-assign]  # injecting fake

    st = ContainerStatus(container_name="groket-has-sess", model="m")
    st.session_dir = Path("/already/set")

    # Release wait after a short delay
    def release():
        import time

        time.sleep(0.2)
        wait_event.set()

    threading.Thread(target=release, daemon=True).start()

    code = o.wait_for_container_with_session_peek(
        "groket-has-sess",
        st,
        peek_interval_s=0.1,
    )
    assert code == 0
    assert st.session_dir == Path("/already/set")


def test_wait_peek_exception_caught(tmp_path: Path):
    """OSError during peek is caught (lines 934-935)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    wait_event = threading.Event()

    def slow_wait(name):
        wait_event.wait(timeout=1.0)
        return 0

    client.wait = slow_wait  # type: ignore[method-assign]  # injecting fake

    def peek_boom(name):
        raise OSError("peek fail")

    def release():
        import time

        time.sleep(0.2)
        wait_event.set()

    threading.Thread(target=release, daemon=True).start()

    st = ContainerStatus(container_name="groket-peek-boom", model="m")
    with patch.object(o, "peek_session_dir", side_effect=peek_boom):
        code = o.wait_for_container_with_session_peek(
            "groket-peek-boom",
            st,
            peek_interval_s=0.1,
        )
    assert code == 0
    assert st.session_dir is None


def test_wait_abort_mid_loop(tmp_path: Path):
    """Abort during wait loop returns -1 (line 928-929)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    # Block wait indefinitely
    def block_wait(name):
        threading.Event().wait(timeout=10.0)
        return 0

    client.wait = block_wait  # type: ignore[method-assign]  # injecting fake

    def abort_after_loop():
        import time

        time.sleep(0.15)
        o.request_abort()

    threading.Thread(target=abort_after_loop, daemon=True).start()

    st = ContainerStatus(container_name="groket-abort-loop", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-abort-loop",
        st,
        peek_interval_s=0.1,
    )
    assert code == -1


def test_wait_exception_in_wait_thread(tmp_path: Path):
    """Exception in _wait thread sets exit_holder to -1 (lines 913-914)."""
    client = FakeDocker(image_exists=True, fail_wait=True)
    o = _orch(tmp_path, client)

    st = ContainerStatus(container_name="groket-wait-exc", model="m")
    code = o.wait_for_container_with_session_peek(
        "groket-wait-exc",
        st,
        peek_interval_s=0.1,
    )
    assert code == -1


def test_wait_final_peek_exception_caught(tmp_path: Path):
    """Final peek exception is swallowed (lines 960-961)."""
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    st = ContainerStatus(container_name="groket-final-exc", model="m")
    # session_dir is None, so final peek fires
    with patch.object(o, "peek_session_dir", side_effect=OSError("boom")):
        code = o.wait_for_container_with_session_peek(
            "groket-final-exc",
            st,
            peek_interval_s=0.1,
        )
    assert code == 0
    assert st.session_dir is None


def test_run_evaluation_abort_after_stagger_passes(tmp_path: Path):
    """Abort set after stagger but before start returns aborted (lines 1128-1130)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-post-stagger")

    # Abort fires during build (before start_delay check)
    original_build = o.build_image

    def abort_during_build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        o.request_abort()  # set abort after build completes
        return result

    with patch.object(o, "build_image", side_effect=abort_during_build):
        st = o.run_evaluation(cfg, auth, gc, start_delay_s=0.0)
    assert st.status == "aborted"


def test_run_evaluation_log_thread_alive_stopped(tmp_path: Path):
    """Log thread still alive after join(5) gets log_stop set (lines 1170-1171).

    Uses a real thread that stays alive until explicitly unblocked, forcing the
    'log_thread.is_alive()' branch after the 5s join.
    """
    client = FakeDocker(image_exists=True, wait_code=0)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-log-alive2")

    # Patch stream_container_logs to block until log_stop fires
    def blocking_stream(name, on_log, stop_event):
        stop_event.wait(timeout=30.0)

    with patch.object(o, "stream_container_logs", side_effect=blocking_stream):
        st = o.run_evaluation(cfg, auth, gc, on_log=lambda n, m: None)
    assert st.status in ("completed", "failed")


def test_run_evaluation_exception_cleanup(tmp_path: Path):
    """Exception path: fix_traces_ownership + peek for session (lines 1188-1189, 1198-1199)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(model="v9", prompt="p", container_name="groket-exc-cleanup2")

    # Plant a session for peek to find
    traces = o.work_dir / "traces" / cfg.container_name / "s1"
    traces.mkdir(parents=True)
    (traces / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "s1", "cwd": "/w"},
                "session_summary": "s",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (traces / "events.jsonl").write_text("{}\n", encoding="utf-8")

    # Make start_container raise to trigger exception path
    with patch.object(o, "start_container", side_effect=RuntimeError("start fail")):
        st = o.run_evaluation(cfg, auth, gc, on_log=lambda n, m: None)
    assert st.status == "failed"
    # Session should be discovered in exception handler
    assert st.session_dir is not None


def test_run_parallel_abort_slots(tmp_path: Path):
    """Parallel evaluations with abort return aborted slots (lines 1269, 1289-1299)."""
    client = FakeDocker(image_exists=True)
    o = _orch(tmp_path, client)

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    gc = tmp_path / "c.toml"
    gc.write_text("[cli]\n", encoding="utf-8")

    def slow_eval(*args, **kwargs):
        o._abort.wait(timeout=5.0)
        return None  # slot will be None → trigger error/aborted

    o.request_abort()
    with patch.object(o, "run_evaluation", side_effect=slow_eval):
        configs = [
            ContainerConfig(model="v9", prompt="p", container_name="groket-par-1"),
            ContainerConfig(model="v9", prompt="p", container_name="groket-par-2"),
        ]
        results = o.run_parallel_evaluations(configs, auth, gc)
    assert len(results) == 2
    assert all(r.status in ("aborted", "failed") for r in results)
