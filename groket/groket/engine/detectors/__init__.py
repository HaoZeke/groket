"""Detector registry (no built-in detectors).

Implement detectors in ``~/.groket/detectors/*.py`` (or ``~/.groket/plugins``)
with::

    from groket.engine.detectors import detector
    from groket.engine.models import Match

    @detector("my_detector")
    def my_detector(tool_calls, messages, params):
        ...
        return [Match(...)]

Examples: ``examples/detection/``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from ...models import (
    ChatHistory,
    ChatMessage,
    JsonValue,
    ParamBag,
    RuleParams,
    ToolCall,
)
from ..models import Match

__all__ = [
    "ChatHistory",
    "ChatMessage",
    "DetectorFunc",
    "ParamBag",
    "RuleParams",
    "detector",
    "get_all_detectors",
    "get_detector",
    "clear_detectors",
]

DetectorFunc = Callable[
    [list[ToolCall], Sequence[ChatMessage], RuleParams],
    list[Match],
]

_DETECTORS: dict[str, DetectorFunc] = {}


def detector(name: str) -> Callable[[DetectorFunc], DetectorFunc]:
    """Register *func* as detector *name* (YAML ``detector:`` field)."""

    def wrapper(func: DetectorFunc) -> DetectorFunc:
        def adapted(
            tool_calls: list[ToolCall],
            messages: Sequence[ChatMessage],
            params: RuleParams | Mapping[str, JsonValue],
        ) -> list[Match]:
            return func(tool_calls, messages, ParamBag.ensure(params))

        _DETECTORS[name] = adapted
        return func

    return wrapper


def get_detector(name: str) -> DetectorFunc:
    if name not in _DETECTORS:
        available = ", ".join(sorted(_DETECTORS.keys())) or "(none — install user detectors)"
        raise KeyError(f"Unknown detector '{name}'. Available: {available}")
    return _DETECTORS[name]


def get_all_detectors() -> dict[str, DetectorFunc]:
    return dict(_DETECTORS)


def clear_detectors() -> None:
    """Clear registry (tests)."""
    _DETECTORS.clear()
