"""Shared session-export notify path for sessions home and browser."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.screen import Screen

from .i18n import t
from .threads import call_ui, resolve_ui_app


def export_session_with_notify(
    owner: object,
    session_dir: Path,
    *,
    profile: str | None = None,
) -> None:
    """Build a session export and notify the operator.

    Call from a worker thread (``@work``). Uses :func:`call_ui` for notifications.

    :param owner: Screen or app that owns ``notify`` (and ``app`` when a screen).
    :param session_dir: Grok session directory to export.
    :param profile: Export profile id; default profile when omitted.
    """
    from ..session.export_bundle import export_session_bundle

    app = resolve_ui_app(owner)
    notify = getattr(owner, "notify", None)
    if not callable(notify):
        export_session_bundle(session_dir, profile=profile)
        return

    call_ui(app, notify, t("export-bundle-working"), severity="information")
    try:
        result = export_session_bundle(session_dir, profile=profile)
    except Exception as exc:
        call_ui(
            app,
            notify,
            t("export-bundle-failed", exc=str(exc)),
            severity="error",
            timeout=12,
        )
        return
    call_ui(
        app,
        notify,
        t(
            "export-bundle-saved",
            path=str(result.path),
            profile=result.profile_id,
        ),
        severity="information",
        timeout=12,
    )


def _run_export_worker(
    owner: Screen | App,
    session_dir: Path,
    *,
    profile: str | None,
) -> None:
    """Export on a background worker (same pattern as ``E``)."""
    app = owner if isinstance(owner, App) else resolve_ui_app(owner)

    def _run() -> None:
        export_session_with_notify(owner, session_dir, profile=profile)

    if app is not None:
        worker = getattr(app, "run_worker", None)
        if callable(worker):
            worker(_run, thread=True, exclusive=False, name="export-session")
            return
    _run()


def start_export_with_profile_picker(
    owner: Screen | App,
    session_dir: Path,
    *,
    remember_as_default: bool = False,
) -> None:
    """Push the profile picker; on confirm, export *session_dir* on a worker.

    :param remember_as_default: When True, write the chosen id to
        ``export.default_profile`` so later ``E`` skips the picker.
    """
    from .export_profile_modal import ExportProfileModal

    app = owner if isinstance(owner, App) else resolve_ui_app(owner)
    if app is None:
        return

    def _done(profile_id: str | None) -> None:
        if not profile_id:
            return
        if remember_as_default:
            from ..session.export_spec import set_default_export_profile_id

            try:
                set_default_export_profile_id(profile_id)
            except OSError:
                pass
        _run_export_worker(owner, session_dir, profile=profile_id)

    app.push_screen(ExportProfileModal(), _done)


def start_export_smart(owner: Screen | App, session_dir: Path) -> None:
    """Export with configured profile, or ask once when none is configured.

    If ``export.default_profile`` is set in config.json, export immediately.
    Otherwise open the profile picker and save the choice as the default so
    the next ``E`` is silent.
    """
    from ..session.export_spec import configured_export_profile_id

    configured = configured_export_profile_id()
    if configured:
        _run_export_worker(owner, session_dir, profile=configured)
        return
    start_export_with_profile_picker(owner, session_dir, remember_as_default=True)
