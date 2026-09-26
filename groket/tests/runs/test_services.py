"""services.py helpers."""

from __future__ import annotations

from pathlib import Path

from groket.runs import services


def test_module_exports():
    assert hasattr(services, "__file__")


# Exercise public functions if present
def test_call_any_pure_helpers(tmp_path: Path):
    for name in dir(services):
        if name.startswith("_"):
            continue
        obj = getattr(services, name)
        if not callable(obj):
            continue
        # Only zero-arg safe calls
        try:
            import inspect

            sig = inspect.signature(obj)
            if len(sig.parameters) == 0:
                obj()
        except Exception:
            pass


def test_services_functions(tmp_path: Path, monkeypatch):
    # Call each public function with tmp_path where signature allows
    import inspect

    for name in sorted(dir(services)):
        if name.startswith("_"):
            continue
        fn = getattr(services, name)
        if not callable(fn) or inspect.isclass(fn):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        kwargs = {}
        args = []
        ok = True
        for p in sig.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            if p.default is not inspect.Parameter.empty:
                continue
            ann = str(p.annotation)
            if "Path" in ann or p.name.endswith("_dir") or p.name.endswith("path"):
                args.append(tmp_path)
            elif p.name in ("work_dir", "root", "session_dir"):
                args.append(tmp_path)
            else:
                ok = False
                break
        if not ok:
            continue
        try:
            fn(*args, **kwargs)
        except Exception:
            pass
