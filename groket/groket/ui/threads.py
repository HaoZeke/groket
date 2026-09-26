"""Marshal work onto the Textual app thread safely.

``App.call_from_thread`` **must** run from a worker thread. Calling it on the
app thread raises ``RuntimeError``. Worker callbacks and ``@work`` methods use
:func:`call_ui`; code that may run on either thread should use it too.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

from textual.app import App


def resolve_ui_app(owner: object) -> App | None:
    """Return ``owner.app`` when the widget is mounted; else ``None``."""
    with suppress(Exception):
        app = getattr(owner, "app", None)
        if isinstance(app, App):
            return app
    return None


def call_ui[R](
    app: App | None, callback: Callable[..., R], *args: object, **kwargs: object
) -> R | None:
    """Run *callback* on the app thread and return its result.

    From a worker: blocking ``App.call_from_thread`` (waits for the result).
    Already on the app thread: call *callback* directly (``call_from_thread``
    raises ``RuntimeError`` on the UI thread). When the app loop is gone,
    skip the callback (do not query a dead DOM). When *app* is missing,
    invoke *callback* inline if possible.
    """
    if app is None:
        with suppress(Exception):
            return callback(*args, **kwargs)
        return None
    try:
        return app.call_from_thread(callback, *args, **kwargs)
    except RuntimeError as exc:
        if "not running" in str(exc).lower():
            return None
        return callback(*args, **kwargs)
    except Exception:
        with suppress(Exception):
            return callback(*args, **kwargs)
        return None
