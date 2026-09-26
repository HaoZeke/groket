"""Analyzer registry — config lists **analyzer classes** only.

``analysis.plugins`` entries must be ``"module.path:ClassName"``. The loader
imports that class (must implement :class:`~groket.analysis.base.Analyzer`),
instantiates it, and calls :func:`register_analyzer`. No module-level
``ANALYZER`` export, no ``register()`` side effect.

Built-ins register on first :func:`get_analyzer` / :func:`list_analyzers`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast, runtime_checkable

from .base import AnalysisResult, AnalyzeContext, Analyzer, AnalyzerInfo, NoopAnalyzer

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Analyzer] = {}
_DEFAULT_ID: str = "noop"
_NOOP = NoopAnalyzer()
# When set, :func:`register_analyzer` appends each registered id (for config loads).
_registration_sink: list[str] | None = None

# Always-on analyzers registered on first analyze / list (see ensure_builtins).
BUILTIN_ANALYZER_IDS: frozenset[str] = frozenset({"basic", "engine"})
_builtins_ready = False


@runtime_checkable
class AnalyzerClass(Protocol):
    """Callable class type that constructs an :class:`Analyzer` instance."""

    def __call__(self) -> Analyzer: ...


def ensure_builtins() -> None:
    """Register ``basic`` and ``engine`` when missing."""
    global _builtins_ready
    if _builtins_ready and "basic" in _REGISTRY and "engine" in _REGISTRY:
        return
    from .basic import BasicAnalyzer
    from .plugins.engine.analyzer import EngineDetectorAnalyzer

    if "basic" not in _REGISTRY:
        register_analyzer(BasicAnalyzer(), default=(_DEFAULT_ID == "noop"))
    if "engine" not in _REGISTRY:
        register_analyzer(EngineDetectorAnalyzer())
    _builtins_ready = True


def register_analyzer(analyzer: Analyzer, *, default: bool = False) -> None:
    info = analyzer.info
    _REGISTRY[info.id] = analyzer
    if _registration_sink is not None:
        _registration_sink.append(info.id)
    if default:
        global _DEFAULT_ID
        _DEFAULT_ID = info.id


def set_default_analyzer(analyzer_id: str) -> None:
    global _DEFAULT_ID
    if analyzer_id not in _REGISTRY and analyzer_id != "noop":
        raise KeyError(f"Unknown analyzer: {analyzer_id}")
    _DEFAULT_ID = analyzer_id


def get_analyzer(analyzer_id: str | None = None) -> Analyzer:
    ensure_builtins()
    aid = analyzer_id or _DEFAULT_ID
    if aid == "noop":
        return _NOOP
    return _REGISTRY.get(aid) or _NOOP


def list_analyzers() -> list[AnalyzerInfo]:
    ensure_builtins()
    infos = [a.info for a in _REGISTRY.values()]
    ids = {i.id for i in infos}
    if "noop" not in ids:
        infos.insert(0, _NOOP.info)
    return sorted(infos, key=lambda i: (i.optional, i.id))


def analyze_session_path(
    session_dir: Path | str,
    *,
    analyzer_id: str | None = None,
    context: AnalyzeContext | None = None,
) -> AnalysisResult:
    path = Path(session_dir)
    return get_analyzer(analyzer_id).analyze(path, context=context)


def _looks_like_analyzer_instance(obj: Analyzer | AnalyzerClass | type) -> bool:
    return (
        not isinstance(obj, type)
        and hasattr(obj, "info")
        and callable(getattr(obj, "analyze", None))
    )


def _looks_like_analyzer_class(obj: Analyzer | AnalyzerClass | type) -> bool:
    if not isinstance(obj, type):
        return False
    return callable(getattr(obj, "analyze", None))


def instantiate_analyzer(obj: Analyzer | AnalyzerClass | type) -> Analyzer:
    """Instantiate *obj* if it is an Analyzer class; pass through instances."""
    if _looks_like_analyzer_instance(obj):
        return cast(Analyzer, obj)
    if _looks_like_analyzer_class(obj):
        cls = cast(AnalyzerClass, obj)
        inst = cls()
        if _looks_like_analyzer_instance(inst):
            return inst
        raise TypeError(f"{obj!r}() did not produce an Analyzer")
    raise TypeError(f"{obj!r} is not an Analyzer class or instance")


def analyzer_from_module_attr(mod: ModuleType, class_name: str) -> Analyzer:
    """Load ``mod.class_name`` as the single Analyzer for a config entry."""
    if not class_name or not class_name.isidentifier():
        raise ValueError(
            f"invalid analyzer class name {class_name!r} (config must be 'module:ClassName')"
        )
    if not hasattr(mod, class_name):
        raise ValueError(f"module {mod.__name__!r} has no analyzer class {class_name!r}")
    attr: Analyzer | AnalyzerClass | type = getattr(mod, class_name)
    return instantiate_analyzer(attr)


def _plugin_search_dirs(config_dir: Path | None) -> list[str]:
    """Directories on ``sys.path`` for ``module:Class`` analysis plugins.

    Always includes ``~/.groket/plugins`` (user install). Also scans
    ``plugins/`` and ``examples/analysis/plugins/`` under the process cwd and
    ancestors of *config_dir* (repo checkout layouts).
    """
    from ..paths import user_analysis_plugins_dir

    search_dirs: list[str] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.is_dir():
            return
        seen.add(key)
        search_dirs.append(key)

    _add(user_analysis_plugins_dir())
    _add(Path.cwd())
    if config_dir is not None:
        for p in [config_dir, *config_dir.parents]:
            _add(Path(p))
    roots: list[Path] = [Path.cwd()]
    if config_dir is not None:
        roots.extend([config_dir, *config_dir.parents])
    for root in roots:
        _add(root / "plugins")
        _add(root / "examples" / "plugins")
        _add(root / "examples" / "analysis" / "plugins")
    return search_dirs


def _import_plugin_module(module_path: str, search_dirs: list[str]) -> ModuleType:
    """Import *module_path*, preferring the first matching ``.py`` on *search_dirs*.

    Uses :func:`importlib.util.spec_from_file_location` so a stale
    ``sys.modules`` entry (or a lower-priority ``examples/`` copy of the same
    module name) cannot shadow ``~/.groket/plugins``.
    """
    import importlib
    import importlib.util
    import sys

    # Bare module names only (``gte_feedback_grok``); dotted paths fall through.
    if module_path.isidentifier():
        for d in search_dirs:
            candidate = Path(d) / f"{module_path}.py"
            if not candidate.is_file():
                continue
            # Drop any previously imported shadow (examples vs user).
            sys.modules.pop(module_path, None)
            spec = importlib.util.spec_from_file_location(module_path, candidate)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_path] = mod
            spec.loader.exec_module(mod)
            logger.info("Loaded analysis plugin module from %s", candidate)
            return mod
    return importlib.import_module(module_path)


def load_config_plugins(
    specs: list[str],
    *,
    config_dir: Path | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Load plugins from ``"module:AnalyzerClass"`` config strings only.

    Returns ``(loaded, failed, registered_ids)``.
    """
    import sys

    global _registration_sink

    search_dirs = _plugin_search_dirs(config_dir)
    # High-priority first (``~/.groket/plugins`` before repo ``examples/``).
    # Insert in reverse so the first search dir ends up at sys.path[0].
    restore_paths: list[str] = []
    for d in reversed(search_dirs):
        if d not in sys.path:
            sys.path.insert(0, d)
            restore_paths.append(d)

    loaded: list[str] = []
    failed: list[str] = []
    registered_ids: list[str] = []
    prev_sink = _registration_sink
    _registration_sink = registered_ids
    try:
        for spec in specs:
            spec = spec.strip()
            if not spec:
                continue
            try:
                if ":" not in spec:
                    raise ValueError(
                        f"plugin spec {spec!r} must be 'module:ClassName' "
                        f"(point at the analyzer class, not the module alone)"
                    )
                module_path, class_name = spec.rsplit(":", 1)
                if not module_path or not class_name:
                    raise ValueError(f"invalid plugin spec {spec!r}")
                mod = _import_plugin_module(module_path, search_dirs)
                analyzer = analyzer_from_module_attr(mod, class_name)
                register_analyzer(analyzer)
                loaded.append(spec)
                logger.info("Loaded config plugin: %s", spec)
            except Exception:
                logger.warning("Failed to load config plugin: %s", spec, exc_info=True)
                failed.append(spec)
    finally:
        _registration_sink = prev_sink

    for d in restore_paths:
        try:
            sys.path.remove(d)
        except ValueError:
            pass

    return loaded, failed, registered_ids
