"""examples/ is a supported contract — must pass scripts/check_examples.py."""

from __future__ import annotations

import runpy
from pathlib import Path


def test_examples_check_script_exits_zero() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "check_examples.py"
    assert script.is_file()
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        assert exc.code in (0, None), f"check_examples failed with {exc.code}"
