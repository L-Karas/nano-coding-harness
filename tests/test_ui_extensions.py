"""`/extensions` 弹窗：来源 tag、hook / 未启用原因、搜索、Enter 无动作、Esc 关闭、空列表提示。"""
import asyncio

import pytest
from textual.widgets import Input, OptionList

import core.extension.registry as registry
import core.tui.render as _render
from core.extension import list_extensions
from core.tui.screens import ExtensionsScreen
from core.tui.ui_textual import ChatApp
from core.tui.utils import SLASH_COMMANDS


@pytest.fixture(autouse=True)
def _extensions():
    original = list_extensions()
    registry.reset()
    registry.record("observers", "builtin", enabled=True, events=["before_tool"])
    registry.record("permission", "builtin", enabled=True, events=["before_tool"])
    registry.record("alpha", "user", enabled=False, detail="invalid extension.json")
    registry.record("zeta", "user", enabled=True, events=["before_llm", "after_tool"])
    yield
    registry.reset()
    for entry in original:
        registry.record(entry.name, entry.origin, enabled=entry.enabled,
                        detail=entry.detail, events=entry.events)


def _rows(app):
    screen = app.screen_stack[-1]
    lst = screen.query_one("#extensions-list", OptionList)
    return screen, lst, [lst.get_option_at_index(i).prompt.plain for i in range(lst.option_count)]


def test_extensions_command_is_completable_and_opens_read_only_screen():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            assert "/extensions" in SLASH_COMMANDS
            prompt = app.query_one("#prompt")
            prompt.focus()
            prompt.text = "/extensions"
            await pilot.press("enter")
            await pilot.pause()
            assert app._exception is None
            screen, lst, rows = _rows(app)
            assert isinstance(screen, ExtensionsScreen)
            assert rows == [
                "● observers [builtin]\nhooks: before_tool",
                "● permission [builtin]\nhooks: before_tool",
                "● alpha [user]\ninvalid extension.json",
                "● zeta [user]\nhooks: before_llm, after_tool",
            ]
            assert screen.TITLE == "Extensions"
            assert screen.HINT == "  ↑/↓ browse    Esc close"
            search = screen.query_one("#search-input", Input)
            assert search.placeholder == "Search extensions…"
            assert search.has_focus
    asyncio.run(main())


def test_search_filters_by_name_and_enter_does_not_close():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            app._open_extensions()
            await pilot.pause()
            screen, lst, _rows_before = _rows(app)
            search = screen.query_one("#search-input", Input)
            search.value = "ZETA"
            await pilot.pause()
            assert [lst.get_option_at_index(i).prompt.plain for i in range(lst.option_count)] == [
                "● zeta [user]\nhooks: before_llm, after_tool",
            ]
            search.value = ""
            await pilot.pause()
            assert lst.option_count == 4
            await pilot.click("#extensions-list", offset=(2, 0))
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ExtensionsScreen), "单击行不应关窗"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ExtensionsScreen), "Enter 不应关窗"
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ExtensionsScreen)
    asyncio.run(main())


def test_enabled_extension_without_events_shows_no_hooks():
    async def main():
        registry.record("silent", "user", enabled=True)
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            app._open_extensions()
            await pilot.pause()
            _screen, _lst, rows = _rows(app)
            assert "● silent [user]\nno hooks" in rows
    asyncio.run(main())


def test_empty_registry_notifies_instead_of_opening():
    async def main():
        registry.reset()
        app = ChatApp(handle_query=lambda query: None)
        _render._APP = app
        try:
            async with app.run_test() as pilot:
                app._open_extensions()
                await pilot.pause()
                assert not isinstance(app.screen_stack[-1], ExtensionsScreen)
                bodies = [str(w.render()) for w in app.query(".card-body")]
                assert any("No extensions found" in body for body in bodies), bodies
        finally:
            _render._APP = None
    asyncio.run(main())
