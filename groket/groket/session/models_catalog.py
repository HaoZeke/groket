"""Grok model catalog helpers (host ``models_cache.json`` / config.toml)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml

from ..models import JsonObject
from ..paths import user_models_path
from ..utils import read_json_dict

MODELS = ["v9-pizzaparty", "v9-dietcoke", "grok-build"]
MODEL_SHORTS = {
    "v9": "v9",
    "grok-build": "build",
    "dietcoke": "dietcoke",
    "v9-dietcoke": "dietcoke",
    "pizzaparty": "pizzaparty",
    "v9-pizzaparty": "pizzaparty",
}

_USER_MODELS_PATH = user_models_path()
_GROK_MODELS_CACHE = Path.home() / ".grok" / "models_cache.json"
REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "xhigh"
_MODEL_ALIASES: dict[str, str] = {
    "pizzaparty": "v9-pizzaparty",
    "dietcoke": "v9-dietcoke",
    "btnb": "v9",
}


def split_model_effort(token: str) -> tuple[str, str]:
    """Split ``model`` or ``model:effort`` into ``(model_id, effort)``."""
    if not (raw := (token or "").strip()):
        return "", ""
    if ":" in raw:
        model, _, effort = raw.rpartition(":")
        if (model_s := model.strip()) and (effort_l := effort.strip().lower()) in REASONING_EFFORTS:
            return model_s, effort_l
    return raw, ""


def join_model_effort(model: str, effort: str = "") -> str:
    """Format a model token, appending ``:effort`` when *effort* is set."""
    mid, eff = (model or "").strip(), (effort or "").strip().lower()
    return f"{mid}:{eff}" if mid and eff in REASONING_EFFORTS else mid


def normalize_reasoning_effort(effort: str | None, *, default: str = "") -> str:
    """Return a known effort level, or *default* (may be empty)."""
    if (eff := (effort or "").strip().lower()) in REASONING_EFFORTS:
        return eff
    d = (default or "").strip().lower()
    return d if d in REASONING_EFFORTS else (default or "")


def _toml_models_key(key: str) -> str:
    import tomllib

    cfg = Path.home() / ".grok" / "config.toml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg, "rb") as f:
            return str((tomllib.load(f).get("models") or {}).get(key, "") or "").strip()
    except Exception:
        return ""


def default_reasoning_effort() -> str:
    val = _toml_models_key("default_reasoning_effort")
    return val.lower() if val.lower() in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def _read_models_cache() -> dict:
    return read_json_dict(_GROK_MODELS_CACHE) or {}


def _models_block(data: dict | None = None) -> dict:
    data = data if data is not None else _read_models_cache()
    block = data.get("models") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def _entry_info(entry: JsonObject | str) -> JsonObject:
    if not isinstance(entry, dict):
        return {}
    info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
    return info if isinstance(info, dict) else {}


def active_model_catalog() -> dict[str, dict]:
    """Active models from cache: canonical_id → metadata."""
    out: dict[str, dict] = {}
    for key, entry in _models_block().items():
        info = _entry_info(entry)
        if not (mid := str(info.get("model") or key).strip()):
            continue
        name = str(info.get("name") or "").strip()
        aliases: set[str] = {mid.lower(), str(key).strip().lower()}
        if name:
            aliases.add(name.lower())
            if "-" in mid:
                aliases.add(mid.split("-", 1)[-1].lower())
        supports = bool(info.get("supports_reasoning_effort"))
        catalog_effort = str(info.get("reasoning_effort") or "").strip().lower()
        if catalog_effort not in REASONING_EFFORTS:
            catalog_effort = ""
        rec = out.setdefault(
            mid,
            {
                "id": mid,
                "name": name or mid,
                "aliases": set(),
                "supports_reasoning_effort": supports,
                "default_effort": catalog_effort if supports else "",
            },
        )
        rec["aliases"] |= aliases
        if name and not rec.get("name"):
            rec["name"] = name
        if supports:
            rec["supports_reasoning_effort"] = True
            if catalog_effort and not rec.get("default_effort"):
                rec["default_effort"] = catalog_effort
    for rec in out.values():
        rec["aliases"] = sorted(rec["aliases"])
        if not rec.get("supports_reasoning_effort"):
            rec["default_effort"] = ""
    return out


def active_model_ids() -> list[str]:
    block = _models_block()
    if not block:
        return list(MODELS)
    ids, seen = [], set()
    for key, entry in block.items():
        mid = str(_entry_info(entry).get("model") or key).strip()
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids or list(MODELS)


def model_launch_options() -> list[tuple[str, str]]:
    cat = active_model_catalog()
    options: list[tuple[str, str]] = []
    for mid in active_model_ids():
        rec = cat.get(mid) or {}
        if rec.get("supports_reasoning_effort"):
            # Label == token (``model:effort``). Do not use spaces in the label:
            # Textual SelectionList only keeps the first word of the prompt as the
            # option identity, which would collapse all efforts for one model.
            # Effort is a colon suffix for our launch tokens / entrypoint, not a hyphen.
            options.extend(
                (join_model_effort(mid, eff), join_model_effort(mid, eff))
                for eff in REASONING_EFFORTS
            )
        else:
            options.append((mid, mid))
    return options


def _catalog_lookup(raw: str) -> str | None:
    if not (cat := active_model_catalog()) or not (low := (raw or "").strip().lower()):
        return None
    for mid in cat:
        if mid.lower() == low:
            return mid
    for mid, rec in cat.items():
        if low in {a.lower() for a in (rec.get("aliases") or [])}:
            return mid
        if str(rec.get("name") or "").strip().lower() == low:
            return mid
    return None


def normalize_model_selection_tokens(tokens: Sequence[str] | None) -> list[str]:
    cat, host_eff, out, seen = active_model_catalog(), default_reasoning_effort(), [], set()
    for raw in tokens or []:
        mid, eff = split_model_effort(raw)
        if not mid:
            continue
        if hit := _catalog_lookup(mid):
            mid = hit
        rec = cat.get(mid) or {}
        if rec.get("supports_reasoning_effort"):
            if not eff:
                eff = str(rec.get("default_effort") or host_eff or "").strip().lower()
            if eff not in REASONING_EFFORTS:
                eff = host_eff if host_eff in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT
            token = join_model_effort(mid, eff)
        else:
            token = mid
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def models_catalog_help_text() -> str:
    ids = active_model_ids()
    return (
        "no models_cache.json — run `grok models` once on the host"
        if not ids
        else "active: " + ", ".join(ids)
    )


def _read_user_models_yaml() -> list[str] | None:
    if not _USER_MODELS_PATH.exists():
        return None
    try:
        data = yaml.safe_load(_USER_MODELS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "models" in data:
            return [str(m).strip() for m in (data["models"] or []) if str(m).strip()]
        if isinstance(data, list):
            return [str(m).strip() for m in data if str(m).strip()]
    except Exception:
        pass
    return None


def load_models() -> list[str]:
    if not (live := active_model_ids()):
        return list(MODELS)
    if not (preferred := _read_user_models_yaml()):
        return live
    ordered, seen = [], set()
    for token in preferred:
        if (hit := _catalog_lookup(token)) and hit not in seen:
            ordered.append(hit)
            seen.add(hit)
    for mid in live:
        if mid not in seen:
            ordered.append(mid)
            seen.add(mid)
    return ordered or live


def default_model_id() -> str:
    ids = active_model_ids()
    id_set = {i.lower(): i for i in ids}
    if val := _toml_models_key("default"):
        if val in ids:
            return val
        if hit := _catalog_lookup(val):
            return hit
        if val.lower() in id_set:
            return id_set[val.lower()]
    return ids[0] if ids else "v9-pizzaparty"


def resolve_model_id(model: str, *, require_active: bool = False) -> str:
    mid, effort = split_model_effort(model)
    if not (raw := mid or (model or "").strip()):
        return raw
    if hit := _catalog_lookup(raw):
        return join_model_effort(hit, effort)
    low = raw.lower()
    if low in _MODEL_ALIASES:
        alias_target = _MODEL_ALIASES[low]
        if hit2 := _catalog_lookup(alias_target):
            return join_model_effort(hit2, effort)
        return "" if require_active else join_model_effort(alias_target, effort)
    if low == "v9":
        default = default_model_id()
        if default and (not require_active or _catalog_lookup(default)):
            return join_model_effort(default, effort)
    return "" if require_active else join_model_effort(raw, effort)


def resolve_model_ids(models: list[str]) -> list[str]:
    return [r for m in models if (r := resolve_model_id(m, require_active=False))]


def validate_models_for_launch(models: list[str]) -> tuple[list[str], list[str]]:
    active, skips, seen = [], [], set()
    catalog_hint = ", ".join(active_model_ids()) or "(empty — run: grok models)"
    for m in models:
        if not (raw := (m or "").strip()):
            continue
        if not (resolved := resolve_model_id(raw, require_active=True)):
            attempted = resolve_model_id(raw, require_active=False) or raw
            skips.append(
                f"{raw!r} → {attempted!r} is not in the active model list "
                f"(models_cache.json / `grok models`). Active: {catalog_hint}"
            )
            continue
        if resolved not in seen:
            seen.add(resolved)
            active.append(resolved)
    return active, skips


def model_suffix(model: str) -> str:
    """Short label for display / names; safe for Docker and Textual ids."""
    from ..utils import slug_text

    mid, effort = split_model_effort(model)
    if model in MODEL_SHORTS:
        base = MODEL_SHORTS[model]
    elif mid in MODEL_SHORTS:
        base = MODEL_SHORTS[mid]
    else:
        base = (mid or model)[:10]
    if effort:
        return slug_text(f"{base}-{effort}", max_len=14, fallback="model")
    return slug_text(base, max_len=10, fallback="model")
