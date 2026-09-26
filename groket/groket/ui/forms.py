"""Shared form helpers for structured Runner / modal inputs (Textual Select, etc.)."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from textual.widgets import SelectionList

from ..docker.base_profiles import DEFAULT_DOCKER_IMAGE
from ..runs.batch import active_model_ids, load_models
from ..runs.personas import PersonaStore
from .i18n import join_ui, t

DOCKER_IMAGE_OPTIONS: list[tuple[str, str]] = [
    (t("ui-fully-loaded-full-tools-share-loop"), "fully-loaded"),
    (t("ui-minimal-baseline-share-loop-setup-sh-for-rest"), "minimal"),
    (t("ui-ubuntu-24-04-fully-loaded"), "ubuntu:24.04@fully-loaded"),
    (t("ui-debian-bookworm-fully-loaded"), "debian:bookworm@fully-loaded"),
    (t("ui-ubuntu-24-04-raw-os-still-share-loop-via-entrypo"), "ubuntu:24.04"),
]
BATCH_PARALLEL_OPTIONS: list[tuple[str, str]] = [
    (join_ui(n, t("ui-config-s-in-flight")), str(n))
    for n in (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 32)
]
PERSONA_NONE = "__none__"


def docker_select_options() -> list[tuple[str, str]]:
    return list(DOCKER_IMAGE_OPTIONS)


def normalize_docker_choice(value: str | None) -> str:
    v = (value or "").strip() or DEFAULT_DOCKER_IMAGE
    known = {opt[1] for opt in DOCKER_IMAGE_OPTIONS}
    if v in known:
        return v
    low = v.lower()
    if low in ("full", "groket:fully-loaded", "default"):
        return "fully-loaded"
    if low in ("min", "bare"):
        return "minimal"
    return DEFAULT_DOCKER_IMAGE


def docker_select_value_or_default(stored: str | None) -> str:
    return normalize_docker_choice(stored)


def load_active_model_ids() -> list[str]:
    """Canonical model ids (no effort suffix) from ``grok models`` cache / config."""
    with suppress(Exception):
        ids = list(active_model_ids() or [])
        if ids:
            return ids
    try:
        return list(load_models() or [])
    except Exception:
        return []


def load_model_launch_options() -> list[tuple[str, str]]:
    """Labels and launch tokens (``model`` or ``model:effort``) for the runner list."""
    with suppress(Exception):
        from ..session.models_catalog import model_launch_options

        opts = model_launch_options()
        if opts:
            return list(opts)
    # Fallback: bare ids when catalog metadata is unavailable.
    return [(mid, mid) for mid in load_active_model_ids()]


def default_model_selection() -> list[str]:
    """Default launch token(s) for a blank runner (with effort when supported)."""
    try:
        from ..runs.batch import default_model_id
        from ..session.models_catalog import normalize_model_selection_tokens

        mid = (default_model_id() or "").strip()
        if mid:
            return normalize_model_selection_tokens([mid])
    except Exception:
        pass
    opts = load_model_launch_options()
    if not opts:
        return []
    # Prefer a default-effort token for the first catalog model when present.
    try:
        from ..session.models_catalog import normalize_model_selection_tokens

        first_mid = opts[0][1].split(":", 1)[0]
        return normalize_model_selection_tokens([first_mid])
    except Exception:
        return [opts[0][1]]


def model_selection_items(
    selected: Sequence[str] | None = None,
    *,
    catalog: Sequence[str] | None = None,
    default_select_all: bool = False,
) -> list[tuple[str, str, bool]]:
    """Build SelectionList triples: (label, token, initially_selected).

    Tokens are ``model`` or ``model:effort`` for models that support reasoning
    effort (see :func:`~groket.session.models_catalog.model_launch_options`).
    """
    from ..session.models_catalog import (
        normalize_model_selection_tokens,
        split_model_effort,
    )

    options = load_model_launch_options()
    if catalog is not None:
        allowed = {m.strip() for m in catalog if (m or "").strip()}
        if allowed:
            filtered: list[tuple[str, str]] = []
            for lab, tok in options:
                mid, _eff = split_model_effort(tok)
                if tok in allowed or mid in allowed:
                    filtered.append((lab, tok))
            # Include bare catalog entries missing from options.
            have = {split_model_effort(t)[0] for _, t in filtered} | {t for _, t in filtered}
            for mid in catalog:
                m = (mid or "").strip()
                if m and m not in have:
                    filtered.append((m, m))
                    have.add(m)
            options = filtered

    tokens = [tok for _, tok in options]
    token_set = set(tokens)
    sel_raw = [m.strip() for m in (selected or []) if (m or "").strip()]
    try:
        sel = set(normalize_model_selection_tokens(sel_raw)) if sel_raw else set()
    except Exception:
        sel = set(sel_raw)
    if not sel and tokens:
        if default_select_all:
            sel = set(tokens)
        else:
            sel = set(default_model_selection())
            if sel and not (sel & token_set):
                # Fall back to first option when defaults don't match tokens.
                sel = {tokens[0]}
    extra = [m for m in sorted(sel) if m not in token_set]
    items: list[tuple[str, str, bool]] = []
    for lab, tok in options:
        items.append((lab, tok, tok in sel))
    for mid in extra:
        items.append((f"{mid} [not in catalog]", mid, True))
    return items


def persona_select_options(work_dir: Path | None) -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = [(t("ui-none-run-defaults-only"), PERSONA_NONE)]
    if work_dir is None:
        return opts
    with suppress(Exception):
        store = PersonaStore(work_dir)
        store.ensure_defaults()
        for p in store.list():
            bits: list[str] = []
            if p.github_write:
                bits.append("gh-write")
            if (p.github_token or "").strip():
                bits.append("token")
            elif (p.github_token_env or "").strip():
                bits.append("token-env")
            suffix = f" · {'+'.join(bits)}" if bits else ""
            label = f"{p.name or p.persona_id}{suffix}"
            opts.append((label, p.persona_id))
    return opts


def normalize_persona_id(value: str | None | object) -> str:
    """UI select value → stored/launch persona_id (empty string means none)."""
    if value is None:
        return ""
    if type(value).__name__ in ("NoSelection", "_NoSelection"):
        return ""
    s = str(value).strip()
    if not s or s in (PERSONA_NONE, "None", "False", "True"):
        return ""
    return s


def persona_select_value(stored_persona_id: str | None) -> str:
    """Stored persona_id → Select.value (always a valid option id)."""
    s = (stored_persona_id or "").strip()
    return s if s else PERSONA_NONE


def select_null() -> object:
    """Empty selection sentinel for Textual ``Select`` (version-safe).

    Textual 8+ uses :attr:`Select.NULL` (a ``NoSelection`` instance). Older
    code and some docs still refer to ``Select.BLANK``, which is the bool
    ``False`` and is **not** a legal option value — constructing
    ``Select(..., value=Select.BLANK)`` raises ``InvalidSelectValueError``.
    """
    from textual.widgets import Select

    null = getattr(Select, "NULL", None)
    if null is not None:
        return null
    # Pre-NULL Textual: BLANK was the unselected sentinel (often ``False``).
    return getattr(Select, "BLANK", None)


def select_is_blank(value: object) -> bool:
    """True when a Select has no selection (NULL / legacy BLANK / None / bool)."""
    if value is None:
        return True
    # Textual 8: Select.BLANK is the bool False and is not a valid option.
    if isinstance(value, bool):
        return True
    if type(value).__name__ in ("NoSelection", "_NoSelection"):
        return True
    from textual.widgets import Select

    null = getattr(Select, "NULL", None)
    if null is not None and value is null:
        return True
    blank = getattr(Select, "BLANK", None)
    if blank is not None and value is blank:
        return True
    return False


def select_value_str(widget_value: str | None | object, *, default: str = "") -> str:
    """Coerce Select.value (may be sentinel) to str."""
    if select_is_blank(widget_value):
        return default
    s = str(widget_value).strip()
    if s in ("", "None", "False", "True"):
        return default
    return s


def selection_list_selected_ids(selection_list: SelectionList[str]) -> list[str]:
    """Ordered selected model/persona ids from a SelectionList."""
    try:
        selected = list(selection_list.selected)
    except Exception:
        return []
    out: list[str] = []
    for item in selected:
        sid = str(item).strip()
        if sid and sid not in out:
            out.append(sid)
    return out


def batch_parallel_options() -> list[tuple[str, str]]:
    return list(BATCH_PARALLEL_OPTIONS)


def normalize_batch_parallel(value: str | int | None, default: int = 2) -> int:
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(32, n))
