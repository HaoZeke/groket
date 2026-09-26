#!/usr/bin/env python3
"""Fail on disallowed Any / object *value bags* in groket/ (AGENTS.md §4.1).

- Explicit ``Any`` is banned (also mypy ``disallow_any_explicit``).
- ``object`` is banned only as a *container* value type (``dict[…, object]``,
  ``list[object]``, ``Mapping[str, object]``), not as a single unknown input
  at a coerce boundary (``json_value_from_unknown(value: object)``, Pydantic
  ``v: object``, dunders).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = ROOT / "groket"

_ANY_IN_CODE = re.compile(r"\bAny\b")
_OBJECT_BAG = re.compile(
    r"(dict\[[^\]]*object|list\[object\]|Mapping\[[^\]]*object|Sequence\[[^\]]*object"
    r"|tuple\[[^\]]*object|set\[object\])"
)


def main() -> int:
    errs: list[str] = []
    for path in sorted(PY_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#")[0]
            # Docstrings / comments only — skip if whole line is in quotes narrative
            if _ANY_IN_CODE.search(code):
                # Allow typing_extensions / re-exports? No.
                # Skip if only inside a string literal on the line
                if re.search(r"""['\"].*\bAny\b.*['\"]""", code) and not re.search(
                    r"\bAny\b(?![^'\"]*['\"])", code.replace("'", " ").replace('"', " ")
                ):
                    pass
                # Simpler: allow if line is only a docstring fragment (starts with spaces and quote)
                stripped = code.strip()
                if stripped.startswith(('"""', "'''", '"', "'")) and "Any" in stripped:
                    # likely prose in docstring
                    if ":" not in stripped.split("Any")[0][-5:]:
                        continue
                if re.search(r"\bAny\b", code):
                    # exclude `from __future__` etc
                    if "typing" in code and "import" in code:
                        errs.append(f"{rel}:{i}: explicit Any import/use")
                    elif re.search(r":\s*Any\b|->\s*Any\b|\[\s*Any\s*\]|,\s*Any\b|\bAny\s*[,\]\)]", code):
                        errs.append(f"{rel}:{i}: explicit Any annotation")
                    elif re.search(r"\bAny\b", code) and not stripped.startswith(('"""', "'''")):
                        # prose in code comment already stripped
                        if not stripped.startswith("*") and "descendant" not in code.lower():
                            if re.search(r"\bAny\b", code):
                                # last resort: only if looks like type use
                                if any(x in code for x in (": Any", "-> Any", "[Any]", "Any |", "| Any", "Optional[Any]", "Callable[..., Any]")):
                                    errs.append(f"{rel}:{i}: explicit Any")
            if _OBJECT_BAG.search(code):
                if "Textual" in line or "Pydantic" in line:
                    continue
                # tuple[str, object] row payloads etc. — still a bag; allow only with comment
                if "catalog/registry" in line or "row payload" in line:
                    continue
                if "JSON boundary" in line:
                    continue
                errs.append(f"{rel}:{i}: object used as container/value bag")
    for e in errs:
        print(f"ERROR: {e}", file=sys.stderr)
    if errs:
        return 1
    print("check_typing_policy: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
