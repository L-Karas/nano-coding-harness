"""`/extensions` 弹窗：来源 tag、可选描述、hook / 未启用原因、搜索、Enter 无动作、Esc 关闭、空列表提示。"""
import asyncio

import pytest
from textual.widgets import Input, OptionList, Static

import core.extension.registry as registry
from core.extension import list_extensions
from core.tui.screens import ExtensionsScreen
from core.tui.utils import SLASH_COMMANDS
from tests.support import chat_app, option_texts


@pytest.fixture(autouse=True)
def _extensions():
    original = list_extensions()
    registry.reset()
    registry.record("observers", "builtin", enabled=True, events=["before_tool"])
    registry.record("permission", "builtin", enabled=True, events=["before_tool"])
    registry.record("alpha", "user", enabled=False, detail="invalid extension.json")
    registry.record("zeta", "user", enabled=True, events=["before_llm", "after_tool"],
                    description="Zeta extension")
    yield
    registry.reset()
    for entry in original:
        registry.record(entry.name, entry.origin, enabled=entry.enabled,
                        detail=entry.detail, events=entry.events,
                        description=entry.description)


def _rows(app):
    screen = app.screen_stack[-1]
    lst = screen.query_one("#extensions-list", OptionList)
    return screen, lst, option_texts(lst)


def test_extensions_command_is_completable_and_opens_read_only_screen():
    async def main():
        async with chat_app() as (app, pilot):
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
                "○ alpha [user]\ninvalid extension.json",
                "● zeta [user]\nZeta extension\nhooks: before_llm, after_tool",
            ]
            assert screen.TITLE == "Extensions"
            assert screen.HINT == "  ↑/↓ browse    Esc close"
            search = screen.query_one("#search-input", Input)
            assert search.placeholder == "Search extensions…"
            assert search.has_focus
    asyncio.run(main())


def test_search_filters_by_name_and_enter_does_not_close():
    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            screen, lst, _rows_before = _rows(app)
            search = screen.query_one("#search-input", Input)
            search.value = "ZETA"
            await pilot.pause()
            assert option_texts(lst) == [
                "● zeta [user]\nZeta extension\nhooks: before_llm, after_tool",
            ]
            search.value = "Zeta extension"
            await pilot.pause()
            assert option_texts(lst) == [], "搜索只匹配名称，不匹配描述"
            search.value = ""
            await pilot.pause()
            assert len(option_texts(lst)) == 4
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


def test_list_is_transparent_and_hint_is_pinned_to_window_bottom():
    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            screen = app.screen_stack[-1]
            lst = screen.query_one("#extensions-list", OptionList)
            hint = screen.query_one(".picker-hint", Static)
            picker = screen.query_one(".picker")
            assert lst.styles.background.is_transparent, lst.styles.background
            assert lst.region.bottom <= hint.region.y, (lst.region, hint.region)
            assert hint.region.bottom == picker.content_region.bottom, \
                f"提示行应钉在窗口最下方: {hint.region} / {picker.content_region}"
    asyncio.run(main())


def test_disabled_extension_uses_failed_mcp_server_style():
    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, lst, _ = _rows(app)
            row = lst.get_option_at_index(2).prompt  # alpha（禁用）
            assert row.plain.startswith("○ alpha")
            head = next(span for span in row.spans
                        if row.plain[span.start:span.end] == "○ alpha")
            assert str(head.style) == "#f87171", "未启用行应同 /mcp failed server 的红色"
    asyncio.run(main())


def test_disabled_extension_shows_description_before_reason():
    async def main():
        registry.record("off", "user", enabled=False, detail="disabled",
                        description="Disabled but described")
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, _lst, rows = _rows(app)
            assert "○ off [user]\nDisabled but described\ndisabled" in rows
    asyncio.run(main())


def test_enabled_extension_without_events_shows_no_hooks():
    async def main():
        registry.record("silent", "user", enabled=True)
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, _lst, rows = _rows(app)
            assert "● silent [user]\nno hooks" in rows
    asyncio.run(main())


def test_empty_registry_notifies_instead_of_opening():
    async def main():
        registry.reset()
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ExtensionsScreen)
            note = next(n for n in app._notifications if n.message == "No extensions found")
            assert note.markup is False, "toast 正文按纯文本渲染，不做 Rich 标记解析"
    asyncio.run(main())
