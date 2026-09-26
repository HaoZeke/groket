"""Modal: run host self-test and show results."""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .. import text as U
from ..bindings import FORM_SAVE
from ..i18n import join_ui, t
from ..quit_actions import QuitActions
from .activity_bar import ActivityBar

logger = logging.getLogger(__name__)


class SelfTestModal(QuitActions, ModalScreen[bool]):
    """Run Docker / Grok auth / path checks."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, work_dir: Path | None = None) -> None:
        super().__init__()
        self._work_dir = Path(work_dir).expanduser() if work_dir else None

    def compose(self) -> ComposeResult:
        with Container(id="self-test-modal"):
            yield Static(t("ui-self-test-external-dependencies"), id="self-test-title")
            yield Static(t("ui-running-checks"), id="self-test-body")
            with Horizontal(id="self-test-actions", classes="modal-footer"):
                yield Button(U.self_test_rerun(), id="self-test-rerun", variant="primary")
                yield Button(U.self_test_close(), id="self-test-close")

    def on_mount(self) -> None:
        self._run_checks()

    @work(thread=True, exclusive=True, group="self-test")
    def _run_checks(self) -> None:
        from ...diagnostics import run_self_test

        wd = self._work_dir
        if wd is None:
            wd = getattr(self.app, "work_dir", None)
        report = run_self_test(work_dir=wd)
        from ..threads import call_ui

        call_ui(self.app, self._apply_report, report)

    def _apply_report(self, report) -> None:
        body = Text()
        for c in report.checks:
            if c.ok:
                mark, style = (t("ui-ok-3"), "bold green")
            elif c.required:
                mark, style = (t("ui-fail"), "bold red")
            else:
                mark, style = (t("ui-warn-2"), "bold yellow")
            body.append(f"  [{mark}] ", style=style)
            body.append(f"{c.name}\n")
            if c.detail:
                body.append(f"         {c.detail}\n", style="dim")
        body.append("\n")
        if report.ok:
            body.append(t("ui-overall-pass-required-checks-ok"), style="bold green")
        else:
            body.append(
                join_ui(
                    t("ui-overall-fail"),
                    report.fail_count,
                    t("ui-required"),
                    report.warn_count,
                    t("ui-warnings"),
                ),
                style="bold red",
            )
        with suppress(Exception):
            self.query_one("#self-test-body", Static).update(body)
        try:
            summary = (
                t("ui-self-test-pass")
                if report.ok
                else join_ui(t("ui-self-test-fail"), report.fail_count)
            )
            if report.warn_count and report.ok:
                summary = t(
                    "ui-self-test-ok-warns",
                    n=report.warn_count,
                )
            setattr(self.app, "_self_test_summary", summary)
            for w in self.app.query(ActivityBar):
                with suppress(Exception):
                    w.refresh_activity()
        except Exception:
            logger.debug(t("ui-cache-self-test-summary-failed"), exc_info=True)

    @on(Button.Pressed, "#self-test-rerun")
    def _rerun(self) -> None:
        with suppress(Exception):
            self.query_one("#self-test-body", Static).update(t("ui-running-checks"))
        self._run_checks()

    @on(Button.Pressed, "#self-test-close")
    def _close(self) -> None:
        self.dismiss(True)

    def action_save(self) -> None:
        self._run_checks()

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, False)
