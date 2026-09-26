"""Shared Textual Pilot synchronisation and display assertion helpers.

Wait on observable app/widget state; drain the message loop with
``await pilot.pause()`` (no fixed delay). See AGENTS.md §4.5c.

Display checks use :func:`rich_plain` / :func:`assert_rich_contains` so tests
assert what the user would read, not merely that a renderable is non-None.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from io import StringIO
from typing import TypeVar

from rich.console import Console, RenderableType
from textual.pilot import Pilot
from textual.widgets import Static

T = TypeVar("T")

Predicate = Callable[[], bool]
AsyncPredicate = Callable[[], Awaitable[bool]]


def rich_plain(renderable: RenderableType | str | None) -> str:
    """Render a Rich/Textual renderable to plain text (what the user reads)."""
    if renderable is None:
        return ""
    if isinstance(renderable, str):
        return renderable
    buf = StringIO()
    Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=200,
        legacy_windows=False,
    ).print(renderable)
    return buf.getvalue()


def static_plain(widget: Static) -> str:
    """Plain text currently shown on a :class:`~textual.widgets.Static`."""
    content = widget.content
    plain = getattr(content, "plain", None)
    if isinstance(plain, str):
        return plain
    return rich_plain(content) if content is not None else str(content)


def assert_rich_contains(
    renderable: RenderableType | str | None,
    *needles: str,
    msg: str = "",
) -> str:
    """Assert every *needle* appears in the plain text of *renderable*.

    :returns: The plain text (for further checks).
    """
    text = rich_plain(renderable)
    missing = [n for n in needles if n not in text]
    if missing:
        preview = text if len(text) < 800 else text[:800] + "…"
        detail = msg or "renderable plain text"
        raise AssertionError(f"{detail}: missing {missing!r} in displayed text:\n{preview!r}")
    return text


def assert_static_contains(widget: Static, *needles: str, msg: str = "") -> str:
    """Assert every *needle* appears on a :class:`~textual.widgets.Static`."""
    text = static_plain(widget)
    missing = [n for n in needles if n not in text]
    if missing:
        detail = msg or f"#{getattr(widget, 'id', '?')}"
        raise AssertionError(f"{detail}: missing {missing!r} in displayed text:\n{text!r}")
    return text


async def wait_until(
    pilot: Pilot,
    pred: Predicate,
    *,
    attempts: int = 80,
    description: str = "condition",
) -> None:
    """Poll *pred* until true, pumping the Textual message loop between tries.

    :param pilot: Active :class:`~textual.pilot.PilotualPilot` from ``app.run_test()``.
    :param pred: Synchronous predicate; return True when the scenario is ready.
    :param attempts: Maximum loop iterations (safety bound, not a sleep budget).
    :param description: Included in the failure message when the bound is hit.
    :raises AssertionError: If *pred* is still false after *attempts* turns.
    """
    for _ in range(max(1, attempts)):
        if pred():
            return
        await pilot.pause()
    raise AssertionError(f"timed out waiting for {description} (attempts={attempts})")


async def wait_until_async(
    pilot: Pilot,
    pred: AsyncPredicate,
    *,
    attempts: int = 80,
    description: str = "condition",
) -> None:
    """Like :func:`wait_until` but *pred* is async (rare; prefer sync predicates)."""
    for _ in range(max(1, attempts)):
        if await pred():
            return
        await pilot.pause()
    raise AssertionError(f"timed out waiting for {description} (attempts={attempts})")
