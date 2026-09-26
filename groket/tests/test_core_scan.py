"""Native updates.jsonl prefilter: Python twin and optional C ABI."""

from __future__ import annotations

import ctypes
from collections.abc import Iterator
from pathlib import Path

import pytest
from groket import core_scan
from groket.core_scan import (
    filter_updates,
    filter_updates_py,
    keep_updates_line,
    keep_updates_line_py,
    load_core_lib,
    reset_core_lib_cache,
)


@pytest.fixture(autouse=True)
def _reset_core_lib_cache() -> Iterator[None]:
    reset_core_lib_cache()
    yield
    reset_core_lib_cache()


FIXTURE = Path(__file__).parent / "fixtures" / "snapshots" / "minimal_session" / "updates.jsonl"

USER = b'{"params":{"update":{"sessionUpdate":"user_message_chunk","content":"hi"}}}'
STREAMING = (
    b'{"params":{"update":{"sessionUpdate":"tool_call_update","content":"' + (b"x" * 256) + b'"}}}'
)
COMPLETED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","status":"completed"}}}'
COMPLETED_SPACED = (
    b'{"params":{"update":{"sessionUpdate":"tool_call_update","status": "completed"}}}'
)
FAILED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","status":"failed"}}}'
FAILED_SPACED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","status": "failed"}}}'
IS_ERROR = b'{"params":{"update":{"sessionUpdate":"tool_call_update","isError":true}}}'
IS_ERROR_SPACED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","isError": true}}}'


class TestKeepUpdatesLinePy:
    def test_skip_non_terminal_tool_call_update(self) -> None:
        assert keep_updates_line_py(STREAMING) is False

    def test_keep_terminal_completed_failed_is_error(self) -> None:
        for line in (
            COMPLETED,
            COMPLETED_SPACED,
            FAILED,
            FAILED_SPACED,
            IS_ERROR,
            IS_ERROR_SPACED,
        ):
            assert keep_updates_line_py(line) is True

    def test_keep_user_message_chunk(self) -> None:
        assert keep_updates_line_py(USER) is True

    def test_keep_empty(self) -> None:
        assert keep_updates_line_py(b"") is True


class TestFilterUpdatesPy:
    def test_drops_fat_streaming_keeps_two_others(self) -> None:
        blob = USER + b"\n" + STREAMING + b"\n" + COMPLETED + b"\n"
        assert filter_updates_py(blob) == [USER, COMPLETED]

    def test_strips_cr_and_keeps_incomplete_last_line(self) -> None:
        blob = USER + b"\r\n" + STREAMING + b"\r\n" + COMPLETED
        assert filter_updates_py(blob) == [USER, COMPLETED]

    def test_incomplete_streaming_dropped(self) -> None:
        assert filter_updates_py(USER + b"\n" + STREAMING) == [USER]

    def test_empty_input(self) -> None:
        assert filter_updates_py(b"") == []


class TestNativeOptional:
    def test_missing_lib_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core_scan, "load_core_lib", lambda: None)
        assert keep_updates_line(USER) is None
        assert filter_updates(USER + b"\n") is None

    def test_c_abi_matches_python_twin_when_lib_loads(self) -> None:
        lib = load_core_lib()
        if lib is None:
            pytest.skip("libgroket_core not built")
        blob = FIXTURE.read_bytes() + STREAMING + b"\n" + USER + b"\n"
        native = filter_updates(blob)
        assert native == filter_updates_py(blob)
        for line in blob.split(b"\n"):
            if not line:
                continue
            assert keep_updates_line(line) is keep_updates_line_py(line)


class _FakeCore:
    """In-process stand-in for the cdylib (no compile)."""

    def __init__(
        self,
        *,
        keep_rc: int | None = None,
        filter_rc: int | None = None,
        fail_second: bool = False,
    ) -> None:
        self.keep_rc = keep_rc
        self.filter_rc = filter_rc
        self.fail_second = fail_second
        self._filter_calls = 0

    def groket_keep_updates_line(self, ptr: object, length: int) -> int:
        if self.keep_rc is not None:
            return self.keep_rc
        data = b"" if length == 0 else ctypes.string_at(ptr, length)
        return int(keep_updates_line_py(data))

    def groket_filter_updates(
        self,
        in_ptr: object,
        in_len: int,
        out_ptr: object,
        out_cap: int,
        out_len: object,
    ) -> int:
        if self.filter_rc is not None:
            return self.filter_rc
        self._filter_calls += 1
        data = b"" if in_len == 0 else ctypes.string_at(in_ptr, in_len)
        kept = filter_updates_py(data)
        blob = b"\n".join(kept) + (b"\n" if kept else b"")
        sz = ctypes.cast(out_len, ctypes.POINTER(ctypes.c_size_t))
        sz[0] = len(blob)
        if self.fail_second and self._filter_calls >= 2:
            return -1
        if len(blob) > out_cap:
            return -1
        if blob and out_ptr:
            ctypes.memmove(out_ptr, blob, len(blob))
        return 0


class TestNativeWrappersWithFakeLib:
    def test_keep_and_filter_match_twin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core_scan, "load_core_lib", lambda: _FakeCore())
        blob = USER + b"\n" + STREAMING + b"\n" + COMPLETED + b"\n"
        assert keep_updates_line(STREAMING) is False
        assert keep_updates_line(COMPLETED) is True
        assert keep_updates_line(b"") is True
        assert filter_updates(blob) == [USER, COMPLETED]
        assert filter_updates(b"") == []

    def test_filter_null_and_second_call_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core_scan, "load_core_lib", lambda: _FakeCore(filter_rc=-2))
        assert filter_updates(USER + b"\n") is None
        monkeypatch.setattr(core_scan, "load_core_lib", lambda: _FakeCore(fail_second=True))
        assert filter_updates(USER + b"\n") is None

    def test_keep_negative_rc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core_scan, "load_core_lib", lambda: _FakeCore(keep_rc=-2))
        assert keep_updates_line(USER) is None


class TestLibDiscovery:
    def test_filename_by_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core_scan.sys, "platform", "linux")
        assert core_scan.core_lib_filename() == "libgroket_core.so"
        monkeypatch.setattr(core_scan.sys, "platform", "darwin")
        assert core_scan.core_lib_filename() == "libgroket_core.dylib"
        monkeypatch.setattr(core_scan.sys, "platform", "win32")
        assert core_scan.core_lib_filename() == "groket_core.dll"

    def test_candidates_env_package_checkout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        custom = tmp_path / "custom.so"
        monkeypatch.setenv("GROKET_CORE_LIB", str(custom))
        monkeypatch.setattr(core_scan.sys, "platform", "linux")
        paths = core_scan.core_lib_candidates()
        assert paths[0] == custom
        names = [p.name for p in paths]
        assert "libgroket_core.so" in names
        release = ("native", "groket-core", "target", "release")
        assert any(p.parent.parts[-4:] == release for p in paths)

    def test_load_skips_unreadable_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        junk = tmp_path / "libgroket_core.so"
        junk.write_text("not a shared object", encoding="utf-8")
        monkeypatch.setattr(core_scan, "core_lib_candidates", lambda: [junk])
        assert load_core_lib() is None
        assert load_core_lib() is None

    def test_load_binds_when_cdll_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        so = tmp_path / "libgroket_core.so"
        so.write_bytes(b"\x00")

        class _Fn:
            def __init__(self) -> None:
                self.argtypes = None
                self.restype = None

        class _FakeDll:
            def __init__(self, _path: str) -> None:
                self.groket_keep_updates_line = _Fn()
                self.groket_filter_updates = _Fn()

        monkeypatch.setattr(core_scan.ctypes, "CDLL", _FakeDll)
        monkeypatch.setattr(core_scan, "core_lib_candidates", lambda: [so])
        lib = load_core_lib()
        assert lib is not None
        assert lib.groket_keep_updates_line.restype is ctypes.c_int32
        assert lib.groket_filter_updates.restype is ctypes.c_int32
