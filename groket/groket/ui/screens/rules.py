"""Rule manager screen — view, enable/disable, and inspect detection rules."""

from __future__ import annotations

import logging

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Static

from ...engine import get_all_rules, set_rule_enabled
from .. import text as U
from ..bindings import RULES, ChromeActions, focus_primary_list
from ..data_table import style_data_table
from ..i18n import t

logger = logging.getLogger(__name__)


class RulesScreen(ChromeActions):
    """Screen for managing detection rules."""

    BINDINGS = list(RULES)

    def compose(self) -> ComposeResult:
        from ..brand_mark import AppChrome, AppFooter

        yield AppChrome()
        with Vertical():
            yield Static(f"[bold]{U.rules_title()}[/bold]")
            yield DataTable(id="rule-table")
            yield VerticalScroll(Static(id="rule-detail"), id="rule-detail-scroll")
            with Horizontal():
                yield Button(U.enable_all_btn(), variant="primary", id="enable-all-btn")
                yield Button(U.disable_all_btn(), variant="error", id="disable-all-btn")
        yield AppFooter()

    def on_mount(self) -> None:
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#rule-table", DataTable)
        table.clear(columns=True)
        style_data_table(table)
        table.add_columns(U.col_status(), U.col_rule_id(), U.col_category(), U.col_description())
        rules = get_all_rules()
        for rule_id, info in sorted(rules.items()):
            status = "[green]ON[/]" if info.enabled else "[red]OFF[/]"
            table.add_row(status, info.rule_id, info.category, info.description[:60], key=rule_id)
        focus_primary_list(table)

    @on(DataTable.RowSelected, "#rule-table")
    def _on_rule_selected(self, event: DataTable.RowSelected) -> None:
        rule_id = str(event.row_key.value)
        rules = get_all_rules()
        if rule_id in rules:
            info = rules[rule_id]
            from ..i18n import t as _t

            detail = f"[bold]{info.rule_id}[/bold]\n\n" + U.rule_detail(
                info.category, info.enabled, info.detector_name, info.description
            )
            if info.params:
                detail += "\n" + _t("parameters-label") + "\n"
                for k, v in info.params.items():
                    val_str = str(v)[:80]
                    detail += f"  {k}: {val_str}\n"
            if info.recommendation:
                detail += "\n" + _t("recommendation-label") + "\n" + f"{info.recommendation}\n"
            self.query_one("#rule-detail", Static).update(detail)

    def action_toggle_rule(self) -> None:
        table = self.query_one("#rule-table", DataTable)
        if table.cursor_row is not None:
            cursor_key = str(
                table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
            )
            rules = get_all_rules()
            if cursor_key in rules:
                new_state = not rules[cursor_key].enabled
                set_rule_enabled(cursor_key, new_state)
                self._populate_table()
                from ..i18n import t as _t

                state_str = _t("state-enabled") if new_state else _t("state-disabled")
                self.notify(U.rule_toggled(cursor_key, state_str))

    @on(Button.Pressed, "#enable-all-btn")
    def _on_enable_all_btn(self) -> None:
        self.action_enable_all()

    def action_enable_all(self) -> None:
        for rule_id in get_all_rules():
            set_rule_enabled(rule_id, True)
        self._populate_table()
        self.notify(U.all_rules_enabled())

    @on(Button.Pressed, "#disable-all-btn")
    def _on_disable_all_btn(self) -> None:
        self.action_disable_all()

    def action_disable_all(self) -> None:
        for rule_id in get_all_rules():
            set_rule_enabled(rule_id, False)
        self._populate_table()
        self.notify(U.all_rules_disabled())

    def action_refresh_context(self) -> None:
        """Reload rules table from disk/config."""
        try:
            self.on_mount()
        except Exception:
            logger.debug(t("ui-failed-to-refresh-rules-table"), exc_info=True)
        self.notify(U.rules_list_refreshed(), severity="information", timeout=3)
