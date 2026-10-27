"""`/skills` 窗口：Tab 把调用信息填入输入栏（不发送），Enter 仍立即发出。"""
import asyncio

import pytest

import core.tui.screens.skills as skills_screen
from core.skill.skills import Skill
from core.tui.screens import MCPToolsScreen, SkillsScreen
from core.tui.ui_textual import ChatApp

_HINT = ("  ↑/↓ browse    Enter/click invoke    Tab invoke with message"
         "    Ctrl+E enable/disable    Esc close")


@pytest.fixture(autouse=True)
def _skills_enabled_and_state_ok(monkeypatch):
    """这些用例只测 Tab / Enter；固定技能启用且状态文件正常，避免本机状态干扰。"""
    monkeypatch.setattr(skills_screen, "is_skill_enabled", lambda name: True)
    monkeypatch.setattr(skills_screen, "skill_state_error", lambda: None)


def _skill() -> Skill:
    return Skill(name="review", description="Review code", content="",
                 location="/skills/review", skill_type="project")


def _push(app, results: list) -> None:
    app.push_screen(SkillsScreen([_skill()]), callback=results.append)


def test_tab_returns_with_message_and_enter_returns_invoke():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            results: list = []
            _push(app, results)
            await pilot.pause()
            screen = app.screen_stack[-1]
            assert screen.HINT == _HINT
            assert screen.query_one("#search-input").has_focus, "搜索栏应默认聚焦"
            await pilot.press("tab")  # 搜索栏聚焦时 Tab 也应作用于高亮技能
            await pilot.pause()
            assert results == [("review", True)]
            assert not isinstance(app.screen_stack[-1], SkillsScreen), "Tab 后弹窗应关闭"

            _push(app, results)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == [("review", True), ("review", False)], "Enter 仍应立即调用"
    asyncio.run(main())


def test_tab_fills_prompt_instead_of_sending():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt")
            prompt.text = "draft text"
            app._on_skill_picked(("review", True))
            await pilot.pause()
            assert prompt.text == "[Invoke skill 'review'] ", "应覆盖草稿并填入调用信息"
            assert prompt.cursor_location == prompt.document.end, "光标应位于填入文本末尾"
            assert prompt.has_focus, "应聚焦输入栏以便继续补充"
            assert not app._busy, "Tab 不应触发回合"
    asyncio.run(main())


def test_skill_pick_rejected_while_busy():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt")
            app._busy = True
            app._on_skill_picked(("review", True))
            await pilot.pause()
            assert prompt.text == "", "忙时不应填入"
            toasts = [n.message for n in app._notifications]
            assert any("Previous turn is still running" in message for message in toasts), toasts
    asyncio.run(main())


def test_empty_skill_name_is_ignored():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt")
            prompt.text = "draft"
            app._on_skill_picked(("", False))
            await pilot.pause()
            assert prompt.text == "draft", "空技能名不应覆盖输入栏"
            assert not app._busy, "空技能名不应触发回合"
    asyncio.run(main())


def test_tab_with_no_matching_skill_is_noop():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            results: list = []
            _push(app, results)
            await pilot.pause()
            screen = app.screen_stack[-1]
            screen.query_one("#search-input").value = "no-such-skill"
            await pilot.pause()
            assert screen.query_one("#skills-list").option_count == 0
            await pilot.press("tab")
            await pilot.pause()
            assert results == [], "无高亮行时 Tab 不应回传结果"
            assert isinstance(app.screen_stack[-1], SkillsScreen), "无高亮行时窗口应保持打开"
    asyncio.run(main())


def test_mcp_tools_picker_keeps_tab_focus_navigation():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            results: list = []
            screen = MCPToolsScreen("demo", [{"tool_name": "read_file", "tool_description": "Read"}])
            app.push_screen(screen, callback=results.append)
            await pilot.pause()
            assert screen.query_one("#search-input").has_focus, "搜索栏应默认聚焦"
            await pilot.press("tab")
            await pilot.pause()
            assert results == [], "MCP 工具窗 Tab 不应产生选择结果"
            assert isinstance(app.screen_stack[-1], MCPToolsScreen)
            assert not screen.query_one("#search-input").has_focus, "MCP 工具窗 Tab 应仍切换焦点"
    asyncio.run(main())
