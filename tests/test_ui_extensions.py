"""`/extensions` 弹窗：来源 tag 前的状态标签、可选描述与 hook 颜色、搜索、Ctrl+E 启停、Enter 无动作、Esc 关闭、空列表提示。"""
import asyncio

import pytest
from textual.widgets import Input, OptionList, Static

import core.extension.registry as registry
import core.tui.screens.extensions as extensions_screen
from core.extension import ToggleResult, list_extensions
from core.tui.screens import ExtensionsScreen
from core.tui.theme import HOOK_TEXT, SECONDARY_TEXT
from core.tui.utils import SLASH_COMMANDS
from tests.support import chat_app, option_texts


@pytest.fixture(autouse=True)
def _extensions():
    original = list_extensions()
    registry.reset()
    registry.record("observers", "builtin", enabled=True, events=["before_tool"])
    registry.record("permission", "builtin", enabled=True, events=["before_tool"])
    registry.record("alpha", "user", enabled=False, detail="invalid extension.json", error=True)
    registry.record("zeta", "user", enabled=True, events=["before_llm", "after_tool"],
                    description="Zeta extension")
    yield
    registry.reset()
    for entry in original:
        registry.record(entry.name, entry.origin, enabled=entry.enabled,
                        detail=entry.detail, events=entry.events,
                        description=entry.description, error=entry.error)


def _rows(app):
    screen = app.screen_stack[-1]
    lst = screen.query_one("#extensions-list", OptionList)
    return screen, lst, option_texts(lst)


def test_extensions_command_is_completable_and_opens_screen():
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
                "○ alpha (error) [user]\nno hooks",
                "● zeta [user]\nZeta extension\nhooks: before_llm, after_tool",
            ]
            assert screen.TITLE == "Extensions"
            assert screen.HINT == "  ↑/↓ browse    Ctrl+E enable/disable    Esc close"
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


def test_extension_status_rows_use_status_specific_style():
    async def main():
        registry.record("off", "user", enabled=False)
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, lst, _ = _rows(app)
            enabled_row = lst.get_option_at_index(0).prompt  # observers
            assert enabled_row.plain == "● observers [builtin]\nhooks: before_tool", \
                "enabled 不应显示状态标签"
            error_row = lst.get_option_at_index(2).prompt  # alpha（注册失败）
            assert error_row.plain == "○ alpha (error) [user]\nno hooks", \
                "error 状态只显示标签，不展示失败原因"
            error_head = next(span for span in error_row.spans
                              if error_row.plain[span.start:span.end]
                              == "○ alpha")
            assert str(error_head.style) == "#f87171", "error 行应同 /mcp failed server 的红色"
            disabled_row = lst.get_option_at_index(3).prompt  # off（用户禁用）
            assert disabled_row.plain.startswith("○ off (disabled) [user]")
            disabled_head = next(span for span in disabled_row.spans
                                 if disabled_row.plain[span.start:span.end]
                                 == "○ off")
            assert SECONDARY_TEXT in str(disabled_head.style), \
                "disabled 行应同 /mcp disabled server 的灰色"
    asyncio.run(main())


def test_hook_text_uses_a_distinct_color():
    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            screen, lst, _ = _rows(app)
            screen.query_one("#search-input", Input).value = "zeta"
            await pilot.pause()
            row = lst.get_option_at_index(0).prompt
            description = next(span for span in row.spans
                               if row.plain[span.start:span.end].strip()
                               == "Zeta extension")
            hooks = next(span for span in row.spans
                         if row.plain[span.start:span.end].strip()
                         == "hooks: before_llm, after_tool")
            assert str(description.style) == SECONDARY_TEXT
            assert str(hooks.style) == HOOK_TEXT
            assert str(description.style) != str(hooks.style)
    asyncio.run(main())


def test_disabled_extension_shows_description_then_hooks():
    async def main():
        registry.record("off", "user", enabled=False,
                        description="Disabled but described")
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, _lst, rows = _rows(app)
            assert "○ off (disabled) [user]\nDisabled but described\nno hooks" in rows
    asyncio.run(main())


def test_disabled_extension_shows_recorded_hooks():
    async def main():
        registry.record("offhook", "user", enabled=False,
                        events=["before_tool", "after_tool"],
                        description="Off with hooks")
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            screen, lst, _rows_before = _rows(app)
            screen.query_one("#search-input", Input).value = "offhook"
            await pilot.pause()
            assert option_texts(lst) == [
                "○ offhook (disabled) [user]\nOff with hooks\n"
                "hooks: before_tool, after_tool",
            ]
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


def test_ctrl_e_toggles_selected_user_extension(monkeypatch):
    calls: list[str] = []

    def _toggle(name: str):
        calls.append(name)
        entry = next(e for e in list_extensions()
                     if e.origin == "user" and e.name == name)
        registry.record(name, "user", enabled=not entry.enabled,
                        events=entry.events, description=entry.description)
        return ToggleResult(True, f"Disabled extension '{name}'")

    monkeypatch.setattr(extensions_screen, "toggle_extension", _toggle)

    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, lst, _rows_before = _rows(app)
            lst.highlighted = 3  # zeta
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert calls == ["zeta"]
            assert option_texts(lst) == [
                "● observers [builtin]\nhooks: before_tool",
                "● permission [builtin]\nhooks: before_tool",
                "○ alpha (error) [user]\nno hooks",
                "○ zeta (disabled) [user]\nZeta extension\n"
                "hooks: before_llm, after_tool",
            ]
            assert any(n.message == "Disabled extension 'zeta'" for n in app._notifications)
    asyncio.run(main())


def test_ctrl_e_on_builtin_only_notifies(monkeypatch):
    calls: list[str] = []

    def _toggle(name: str):
        calls.append(name)
        return ToggleResult(True, "should not run")

    monkeypatch.setattr(extensions_screen, "toggle_extension", _toggle)

    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, lst, _rows_before = _rows(app)
            lst.highlighted = 0  # observers
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert calls == []
            note = next(n for n in app._notifications
                        if n.message == "Builtin extensions cannot be toggled.")
            assert note.title == "⛔ Extensions"
    asyncio.run(main())


def test_ctrl_e_failure_shows_error_toast(monkeypatch):
    monkeypatch.setattr(
        extensions_screen, "toggle_extension",
        lambda name: ToggleResult(False, "Failed to save extension state: disk full"))

    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            _screen, lst, _rows_before = _rows(app)
            lst.highlighted = 3  # zeta
            await pilot.press("ctrl+e")
            await pilot.pause()
            note = next(n for n in app._notifications
                        if n.message == "Failed to save extension state: disk full")
            assert note.title == "⚠️ Extensions"
    asyncio.run(main())


def test_ctrl_e_enable_load_failed_shows_warning(monkeypatch):
    registry.record("broken", "user", enabled=False, detail="disabled · load failed", error=True)
    monkeypatch.setattr(
        extensions_screen, "toggle_extension",
        lambda name: ToggleResult(
            True,
            "Set extension 'broken' to enabled, but it still fails to load: load failed",
            warning=True,
        ))

    async def main():
        async with chat_app() as (app, pilot):
            app._open_extensions()
            await pilot.pause()
            screen, lst, _rows_before = _rows(app)
            screen.query_one("#search-input", Input).value = "broken"
            await pilot.pause()
            lst.highlighted = 0
            await pilot.press("ctrl+e")
            await pilot.pause()
            note = next(n for n in app._notifications if "still fails to load" in n.message)
            assert note.title == "⚠️ Extensions"
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
