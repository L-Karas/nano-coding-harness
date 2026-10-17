"""设置窗鼠标通路：预填不落盘；点开关 / 点下拉选项 / 点输入框编辑后 Enter 都落盘。"""
import asyncio
from types import SimpleNamespace

from textual.widgets import Input, OptionList, Select, Switch

import core.tui.screens.settings as settings_mod
from core.config import AgentConfig
from core.tui.screens import SettingsScreen
from core.tui.ui_textual import ChatApp


class _FakeConfigManager:
    """顶替 core.config.AgentConfigManager：读默认配置，落盘只记录不写文件。"""

    def __init__(self, config):
        self.config = config

    @classmethod
    def load_config(cls):
        return SimpleNamespace(config=AgentConfig())

    def save_config(self):
        type(self).saved.append(self.config.model_dump())

    saved: list[dict] = []


def test_settings_mouse_click_paths_persist(monkeypatch):
    _FakeConfigManager.saved = []
    monkeypatch.setattr(settings_mod, "AgentConfigManager", _FakeConfigManager)
    monkeypatch.setattr("core.client.get_model_list",
                        lambda custom_model=False: [("deepseek-chat", "deepseek")], raising=False)

    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test(size=(120, 45)) as pilot:
            app.push_screen(SettingsScreen())
            await pilot.pause()
            form = app.screen_stack[-1]
            saved = _FakeConfigManager.saved
            assert saved == [], "开窗预填不应落盘"

            # 点开关：切换并落盘（窗口高度同 /sessions：先滚到可见区再点）
            switch = form.query_one("#set-auto_compact", Switch)
            assert switch.value is True
            switch.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#set-auto_compact")
            await pilot.pause()
            assert switch.value is False, "单击开关应切换"
            assert saved and saved[-1]["auto_compact"] is False, "单击开关应落盘"

            # 点锁定输入框：进入编辑态（等同第一次 Enter），输入后 Enter 落盘
            tokens = form.query_one("#set-default_max_tokens", Input)
            tokens.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#set-default_max_tokens")
            await pilot.pause()
            assert tokens.has_class("-editing"), "单击锁定输入框应进入编辑态"
            await pilot.press("1")  # 进入编辑态即全选：直接输入替换
            await pilot.pause()
            assert tokens.value == "1", tokens.value
            await pilot.press("enter")
            await pilot.pause()
            assert saved[-1]["default_max_tokens"] == 1, "编辑后 Enter 应落盘"

            # 点下拉选项：选中并落盘（行偏移 = 边框 gutter.top + 选项行号）
            thinking = form.query_one("#set-default_thinking_level", Select)
            thinking.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#set-default_thinking_level")
            await pilot.pause()
            assert thinking.expanded, "单击下拉应展开浮层"
            overlay = thinking.query_one(OptionList)
            await pilot.click(overlay, offset=(2, overlay.gutter.top + overlay._index_to_line[0]))
            await pilot.pause()
            assert thinking.value == "minimal", thinking.value
            assert not thinking.expanded
            assert saved[-1]["default_thinking_level"] == thinking.value, \
                "鼠标点下拉选项应落盘"

            # Tab 保留切字段：从模型下拉切到下一个字段
            first = form.query_one("#set-default_model", Select)
            first.focus()
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert form.focused.id == "set-default_sub_model", \
                f"Tab 应切到下一字段: {form.focused.id}"

            # 焦点边界：滚动主体不参与焦点链（Tab / Shift+Tab 只在字段间回环；点参数名不移焦点）
            last = form.query_one("#set-enable_defer_skills", Switch)
            last.focus()
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert form.focused is first, f"末字段 Tab 应回环到首字段: {form.focused.id}"
            first.focus()
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            assert form.focused is last, f"首字段 Shift+Tab 应回环到末字段: {form.focused.id}"
            sub = form.query_one("#set-default_sub_model", Select)
            sub.focus()
            await pilot.pause()
            await pilot.click(".field-label")
            await pilot.pause()
            assert form.focused is sub, f"点击参数名不应移走焦点: {form.focused.id}"
    asyncio.run(main())
