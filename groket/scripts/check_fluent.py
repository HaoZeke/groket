#!/usr/bin/env python3
"""Lint Fluent catalog + call-site construction (AGENTS.md §3.1 / §3.1a).

Hard failures (exit 1):
- f-string embedding t(...) under groket/
- re.compile(t(...))
- regex/binary message ids in FTL
- leading/trailing whitespace on single-line FTL values without placeables
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FTL = ROOT / "groket" / "locale" / "en" / "main.ftl"
PY_ROOT = ROOT / "groket"

_MSG = re.compile(r"^([a-zA-Z0-9_-]+)\s*=\s*(.*)$")
_REGEX_IDS = frozenset({"ui-x1b", "ui-x1b-x07-x1b", "ui-r-n", "ui-ufffd-2"})
_RE_COMPILE_T = re.compile(r"re\.compile\s*\(\s*t\s*\(")
# f"…{t( or f'…{t(
_F_STRING_T = re.compile(r"""f["'][^"']*\{[^{}]*\bt\s*\(""")
# multi-line FTL continuation lines start with spaces and are part of prior message
_MULTILINE_CONT = re.compile(r"^\s+\S")


def check_ftl() -> list[str]:
    hard: list[str] = []
    if not FTL.is_file():
        return [f"missing {FTL}"]
    lines = FTL.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _MSG.match(line)
        if not m:
            continue
        mid, val = m.group(1), m.group(2)
        if mid in _REGEX_IDS:
            hard.append(
                f"{FTL.relative_to(ROOT)}:{i}: regex/constant id {mid!r} must not live in FTL"
            )
        # Multi-line FTL: value empty on first line, body indented below
        if val.strip() == "":
            continue
        if "{$" in val or val.strip().startswith("{"):
            continue
        # Single-line value with edge spaces (Fluent strips — not a glue strategy)
        if val.startswith(" ") or (val.endswith(" ") and not val.endswith("\\ ")):
            hard.append(
                f"{FTL.relative_to(ROOT)}:{i}: {mid!r} has leading/trailing space "
                f"(Fluent strips edges; use full messages or join_ui)"
            )
    return hard


def check_py() -> list[str]:
    errs: list[str] = []
    for path in PY_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if _RE_COMPILE_T.search(line):
                errs.append(
                    f"{rel}:{i}: re.compile(t(...)) — keep regexes in Python, not Fluent"
                )
            if _F_STRING_T.search(line):
                errs.append(
                    f"{rel}:{i}: f-string embeds t() — use t('id', var=...) or join_ui(...)"
                )
    return errs


def main() -> int:
    hard = check_ftl() + check_py()
    for e in hard:
        print(f"ERROR: {e}", file=sys.stderr)
    if hard:
        print(f"check_fluent: {len(hard)} error(s)", file=sys.stderr)
        return 1
    print("check_fluent: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
