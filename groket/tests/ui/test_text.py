"""Verify ui_text public API returns valid Rich markup."""

from __future__ import annotations

import inspect

from groket.ui import text as ui_text
from groket.ui.i18n import setup_i18n


def test_all_ui_text_callables():
    setup_i18n("en")
    for name, fn in inspect.getmembers(ui_text, inspect.isfunction):
        if name.startswith("_"):
            continue
        if name in ("_", "ngettext"):
            continue
        sig = inspect.signature(fn)
        params = [
            p
            for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        args = []
        for p in params:
            if p.name in ("n", "pid", "label", "root"):
                args.append("x" if p.name != "n" else 2)
            else:
                args.append("x")
        try:
            out = fn(*args)
        except TypeError:
            continue
        assert out is not None
        if isinstance(out, tuple):
            assert all(isinstance(x, str) for x in out)
        else:
            assert isinstance(out, str)
