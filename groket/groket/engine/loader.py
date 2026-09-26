"""YAML rule loader — rules, composites, and ``@detector`` modules.

Loads:

- User detector modules from ``~/.groket/detectors/*.py`` and detectors in
  ``~/.groket/plugins/*.py``
- Empty package stubs ``assets/config/rules.yaml`` and ``composites.yaml``
- User rule YAML from ``~/.groket/rules/*.yaml`` (same ``id`` replaces stub entry)
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..assets_loader import asset_path
from ..models import JsonObject, Severity
from ..paths import user_analysis_plugins_dir, user_detectors_dir, user_rules_dir
from .detectors import DetectorFunc, get_detector

logger = logging.getLogger(__name__)


@dataclass
class RuleConfig:
    """A loaded rule definition from YAML."""

    rule_id: str
    description: str
    category: str
    severity: Severity
    enabled: bool
    detector_name: str
    params: JsonObject
    summary_template: str
    detail_template: str
    recommendation: str
    detector_func: DetectorFunc | None = None


@dataclass
class CompositeConfig:
    """A loaded composite rule definition from YAML."""

    composite_id: str
    name: str
    severity: Severity
    child_rules: list[str]
    relationship: str
    min_repeat: int = 3
    max_gap: int = 5
    root_cause: str = ""
    should_have: str = ""


@dataclass
class LoadedConfig:
    """The complete loaded configuration."""

    rules: dict[str, RuleConfig] = field(default_factory=dict)
    composites: list[CompositeConfig] = field(default_factory=list)


_CONFIG: LoadedConfig | None = None


def get_config() -> LoadedConfig:
    """Get the loaded config, loading from defaults if not yet loaded."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def reload_config() -> LoadedConfig:
    """Force reload of all config."""
    global _CONFIG
    _CONFIG = load_config()
    return _CONFIG


def load_config() -> LoadedConfig:
    """Load detectors and rules from user dirs plus empty package stubs.

    Detectors: ``~/.groket/detectors`` and ``~/.groket/plugins``.
    Rules: ``assets/config`` stubs, then ``~/.groket/rules`` (see
    ``examples/detection/`` for installable packs).
    """
    config = LoadedConfig()

    for plugin_dir in (user_detectors_dir(), user_analysis_plugins_dir()):
        if plugin_dir.is_dir():
            _load_detector_modules(plugin_dir)

    _load_rules_file(asset_path("config", "rules.yaml"), config)
    _load_composites_file(asset_path("config", "composites.yaml"), config)

    user_dir = user_rules_dir()
    if user_dir.is_dir():
        for yaml_file in sorted(user_dir.glob("*.yaml")):
            _load_override_file(yaml_file, config)
        for yaml_file in sorted(user_dir.glob("*.yml")):
            _load_override_file(yaml_file, config)

    for rule in config.rules.values():
        try:
            rule.detector_func = get_detector(rule.detector_name)
        except KeyError as e:
            logger.warning("Rule '%s': %s", rule.rule_id, e)

    return config


def _load_rules_file(path: Path, config: LoadedConfig) -> None:
    """Load rules from a YAML file."""
    if not path.exists():
        logger.warning("Rules file not found: %s", path)
        return

    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return

    rules_list = data if isinstance(data, list) else data.get("rules", [])

    for entry in rules_list:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        try:
            rule = _parse_rule(entry)
        except Exception as exc:
            logger.warning("Invalid rule in %s (id=%s): %s", path, entry.get("id"), exc)
            continue
        config.rules[rule.rule_id] = rule


def _load_composites_file(path: Path, config: LoadedConfig) -> None:
    """Load composites from a YAML file."""
    if not path.exists():
        logger.warning("Composites file not found: %s", path)
        return

    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return

    comps_list = data if isinstance(data, list) else data.get("composites", [])

    for entry in comps_list:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        try:
            comp = _parse_composite(entry)
        except Exception as exc:
            logger.warning("Invalid composite in %s (id=%s): %s", path, entry.get("id"), exc)
            continue
        config.composites.append(comp)


def _load_override_file(path: Path, config: LoadedConfig) -> None:
    """Load a user override file that merges into existing config."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to load override file %s: %s", path, e)
        return

    if data is None:
        return

    # Rules overrides
    rules_list = data if isinstance(data, list) else data.get("rules", [])
    for entry in rules_list:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        rule_id = entry["id"]
        if rule_id in config.rules:
            # Merge: override only specified fields
            existing = config.rules[rule_id]
            if "enabled" in entry:
                existing.enabled = entry["enabled"]
            if "severity" in entry:
                existing.severity = Severity(entry["severity"])
            if "params" in entry:
                existing.params.update(entry["params"])
            if "description" in entry:
                existing.description = entry["description"]
            if "category" in entry:
                existing.category = entry["category"]
            if "summary" in entry:
                existing.summary_template = entry["summary"]
            if "detail" in entry:
                existing.detail_template = entry["detail"]
            if "recommendation" in entry:
                existing.recommendation = entry["recommendation"]
            if "detector" in entry:
                existing.detector_name = entry["detector"]
        else:
            # New rule from user override
            try:
                rule = _parse_rule(entry)
            except Exception as exc:
                logger.warning("Invalid rule in %s (id=%s): %s", path, rule_id, exc)
                continue
            config.rules[rule.rule_id] = rule

    # Composite overrides
    comps_list = data.get("composites", []) if isinstance(data, dict) else []
    for entry in comps_list:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        try:
            comp = _parse_composite(entry)
        except Exception as exc:
            logger.warning("Invalid composite in %s (id=%s): %s", path, entry.get("id"), exc)
            continue
        # Replace existing composite with same ID, or add new
        config.composites = [c for c in config.composites if c.composite_id != comp.composite_id]
        config.composites.append(comp)


def _load_detector_modules(plugin_dir: Path) -> None:
    """Import ``*.py`` files that register detectors via ``@detector``.

    Puts *plugin_dir* on ``sys.path`` so multi-file packs can use sibling
    imports (e.g. ``from patterns import …``), matching a flat copy into
    ``~/.groket/detectors/``.
    """
    root = plugin_dir.resolve()
    path_s = str(root)
    inserted = path_s not in sys.path
    if inserted:
        sys.path.insert(0, path_s)
    try:
        for py_file in sorted(root.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            stem = py_file.stem
            # Prefer ordinary import so sibling modules resolve by stem.
            try:
                if stem in sys.modules:
                    importlib.reload(sys.modules[stem])
                else:
                    importlib.import_module(stem)
                logger.info("Loaded detector module: %s", py_file)
                continue
            except Exception:
                logger.debug("stem import failed for %s, trying file load", py_file, exc_info=True)
            module_name = f"groket_user_detector_{root.name}_{stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    logger.info("Loaded detector module: %s", py_file)
            except Exception as e:
                logger.warning("Failed to load detector module %s: %s", py_file, e)
    finally:
        if inserted:
            try:
                sys.path.remove(path_s)
            except ValueError:
                pass


def _parse_rule(entry: JsonObject) -> RuleConfig:
    """Parse a rule dict from YAML into a RuleConfig (Pydantic-validated)."""
    from .rule_schema import parse_rule_entry

    return parse_rule_entry(entry)


def _parse_composite(entry: JsonObject) -> CompositeConfig:
    """Parse a composite dict from YAML into a CompositeConfig (Pydantic-validated)."""
    from .rule_schema import parse_composite_entry

    return parse_composite_entry(entry)
