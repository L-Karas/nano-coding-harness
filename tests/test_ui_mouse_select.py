"""窗口鼠标与浏览导航：列表窗提示行写明 click（与 Enter 等价的窗口并列写 Enter/click，
注销窗点击仅选中）；表单窗 Tab / ↑/↓ 都可切字段；单击行 ≡ Enter。"""
import asyncio

from textual.widgets import Input, Static

from core.tui.screens import (
    ForkScreen,
    LoginScreen,
    MCPServersScreen,
    MCPToolsScreen,
    ModelPickerScreen,
    ProviderScreen,
    RegisterProviderScreen,
    SessionPickerScreen,
    SkillsScreen,
    UnregisterModelScreen,
    UnregisterProviderScreen,
)
from tests.support import chat_app

# 单击行立即执行与 Enter 相同的动作
_CLICK_ACTION_SCREENS = (ForkScreen, LoginScreen, MCPServersScreen, MCPToolsScreen, ModelPickerScreen,
                         ProviderScreen, SessionPickerScreen, SkillsScreen)
# 点击仅移动选中（动作键是 Delete，Enter 无动作）
_CLICK_SELECT_SCREENS = (UnregisterModelScreen, UnregisterProviderScreen)


def test_list_picker_hints_mention_click():
    for screen_cls in _CLICK_ACTION_SCREENS:
        assert "Enter/click" in screen_cls.HINT, \
            f"{screen_cls.__name__} 点击与 Enter 等价，提示行应并列写 Enter/click"
    for screen_cls in _CLICK_SELECT_SCREENS:
        assert "↑/↓ or click" in screen_cls.HINT, \
            f"{screen_cls.__name__} 点击仅选中，提示行应写 ↑/↓ or click"


def test_login_click_row_equals_enter():
    async def main():
        async with chat_app() as (app, pilot):
            app.push_screen(LoginScreen())
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], LoginScreen)
            await pilot.click("#login-list", offset=(2, 0))  # 首行 = 注册自定义提供方
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], RegisterProviderScreen), \
                "单击行应等同 Enter 选中（进入子窗）"
    asyncio.run(main())


def test_form_tab_and_arrows_switch_fields():
    async def main():
        async with chat_app() as (app, pilot):
            app.push_screen(RegisterProviderScreen())
            await pilot.pause()
            form = app.screen_stack[-1]
            hint = str(form.query_one(".picker-hint", Static).render())
            assert "Tab next field" in hint, f"表单提示行应保留 Tab 切换: {hint!r}"
            name = form.query_one("#reg-provider-name", Input)
            assert form.focused is name
            await pilot.press("tab")
            await pilot.pause()
            assert form.focused.id == "reg-provider-url", "Tab 应切到下一字段"
            await pilot.press("shift+tab")
            await pilot.pause()
            assert form.focused is name, "Shift+Tab 应切回上一字段"
            await pilot.press("down")
            await pilot.pause()
            assert form.focused.id == "reg-provider-url", "↓ 应切到下一字段"
            await pilot.press("up")
            await pilot.pause()
            assert form.focused is name, "↑ 应切回上一字段"
    asyncio.run(main())
