"""Flag modal widget tests."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.models import Flag, FlagVerdict
from groket.ui.widgets.flag_panel import FlagModal
from textual.app import App, ComposeResult
from textual.widgets import Select, Static, TextArea

from .pilot_helpers import wait_until


class _FlagApp(App):
    def compose(self) -> ComposeResult:
        yield Static("main")


@pytest.mark.asyncio
async def test_flag_modal_create_new() -> None:
    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(
            index=5,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "x"},
        )
        modal = FlagModal(ev)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#verdict-select"))),
            description="verdict-select mounted",
        )
        verdict = app.screen.query_one("#verdict-select", Select)
        assert verdict.value == FlagVerdict.BAD.value
        desc = app.screen.query_one("#flag-description", TextArea)
        desc.text = "Unnecessary grep"
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_edit_existing() -> None:
    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=3, event_type="tool_call", tool_name="read_file")
        existing = Flag(
            event_index=3,
            verdict=FlagVerdict.GOOD,
            description="Good usage",
        )
        modal = FlagModal(ev, existing_flag=existing)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal with existing",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#save-flag"))),
            description="save-flag mounted",
        )
        assert app.screen.existing_flag is existing
        delete_btn = app.screen.query("#delete-flag")
        assert len(list(delete_btn)) == 1


@pytest.mark.asyncio
async def test_flag_modal_cancel() -> None:
    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="user_message_chunk", content="hi")
        modal = FlagModal(ev)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_save_action() -> None:
    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="tool_call", tool_name="grep")
        modal = FlagModal(ev)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#verdict-select"))),
            description="verdict mounted",
        )
        app.screen.action_save()
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_button_cancel() -> None:
    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="user_message_chunk", content="x")
        modal = FlagModal(ev)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#cancel-flag"))),
            description="cancel mounted",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_button_delete() -> None:
    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="tool_call", tool_name="grep")
        existing = Flag(event_index=0, verdict=FlagVerdict.BAD, description="bad")
        modal = FlagModal(ev, existing_flag=existing)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#save-flag"))),
            description="buttons mounted",
        )
        assert app.screen.query("#delete-flag")


def test_flag_submitted_message() -> None:
    """FlagSubmitted message carries the saved flag."""
    flag = Flag(event_index=1, verdict=FlagVerdict.BAD, description="test")
    msg = FlagModal.FlagSubmitted(flag)
    assert msg.flag is flag


def test_flag_deleted_message() -> None:
    """FlagDeleted message carries the event index."""
    msg = FlagModal.FlagDeleted(7)
    assert msg.event_index == 7


@pytest.mark.asyncio
async def test_flag_modal_button_pressed_save() -> None:
    """on_button_pressed dispatches the save action."""
    from textual.widgets import Button

    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="tool_call", tool_name="grep")
        existing = Flag(event_index=0, verdict=FlagVerdict.BAD, description="bad")
        modal = FlagModal(ev, existing_flag=existing)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#save-flag"))),
            description="buttons mounted",
        )
        save_btn = app.screen.query_one("#save-flag", Button)
        app.screen.on_button_pressed(Button.Pressed(save_btn))
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_button_pressed_delete() -> None:
    """on_button_pressed dispatches the delete action."""
    from textual.widgets import Button

    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="tool_call", tool_name="grep")
        existing = Flag(event_index=0, verdict=FlagVerdict.BAD, description="bad")
        modal = FlagModal(ev, existing_flag=existing)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#delete-flag"))),
            description="delete button mounted",
        )
        del_btn = app.screen.query_one("#delete-flag", Button)
        app.screen.on_button_pressed(Button.Pressed(del_btn))
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_button_pressed_cancel() -> None:
    """on_button_pressed dispatches the cancel action."""
    from textual.widgets import Button

    app = _FlagApp()
    async with app.run_test(size=(100, 40)) as pilot:
        ev = make_trace_event(index=0, event_type="user_message_chunk", content="hi")
        modal = FlagModal(ev)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, FlagModal),
            description="FlagModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#cancel-flag"))),
            description="cancel mounted",
        )
        cancel_btn = app.screen.query_one("#cancel-flag", Button)
        app.screen.on_button_pressed(Button.Pressed(cancel_btn))
        await pilot.pause()
