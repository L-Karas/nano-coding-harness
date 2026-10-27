"""`/skills` 窗口 Ctrl+E：切换启用状态；禁用项 Enter/Tab 只提示、不关闭窗口。"""
import asyncio

import pytest

import core.tui.screens.skills as skills_screen
from core.skill.skills import Skill
from core.tui.screens import SkillsScreen
from core.tui.ui_textual import ChatApp


@pytest.fixture
def state(monkeypatch):
    enabled = {"review": True}
    calls: list[tuple[str, bool]] = []

    def _is_enabled(name: str) -> bool:
        return enabled.get(name, True)

    def _set_enabled(name: str, value: bool) -> tuple[bool, str]:
        calls.append((name, value))
        enabled[name] = value
        action = "Enabled" if value else "Disabled"
        return True, f"{action} skill '{name}'"

    monkeypatch.setattr(skills_screen, "is_skill_enabled", _is_enabled)
    monkeypatch.setattr(skills_screen, "set_skill_enabled", _set_enabled)
    monkeypatch.setattr(skills_screen, "skill_state_error", lambda: None)
    return enabled, calls


def _skill() -> Skill:
    return Skill(name="review", description="Review code", content="",
                 location="/skills/review", skill_type="project")


def test_ctrl_e_toggles_and_disabled_blocks_invoke(state):
    enabled, calls = state

    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            results: list = []
            app.push_screen(SkillsScreen([_skill()]), callback=results.append)
            await pilot.pause()

            await pilot.press("ctrl+e")
            await pilot.pause()
            assert calls == [("review", False)]
            assert enabled["review"] is False
            assert isinstance(app.screen_stack[-1], SkillsScreen)

            await pilot.press("enter")
            await pilot.pause()
            assert results == [], "禁用技能 Enter 不应回传调用"
            assert isinstance(app.screen_stack[-1], SkillsScreen)

            await pilot.press("tab")
            await pilot.pause()
            assert results == [], "禁用技能 Tab 不应回传调用"
            assert isinstance(app.screen_stack[-1], SkillsScreen)

            await pilot.press("ctrl+e")
            await pilot.pause()
            assert enabled["review"] is True

            await pilot.press("enter")
            await pilot.pause()
            assert results == [("review", False)], "重新启用后 Enter 应恢复调用"
    asyncio.run(main())


def test_state_error_row_is_shown_but_skills_stay_enabled(monkeypatch):
    monkeypatch.setattr(skills_screen, "is_skill_enabled", lambda name: True)
    monkeypatch.setattr(skills_screen, "skill_state_error", lambda: "failed to read .skills.json")

    async def main():
        app = ChatApp(handle_query=lambda query: None)
        async with app.run_test() as pilot:
            results: list = []
            app.push_screen(SkillsScreen([_skill()]), callback=results.append)
            await pilot.pause()
            screen = app.screen_stack[-1]
            olist = screen.query_one("#skills-list")
            rows = [olist.get_option_at_index(i).prompt.plain
                    for i in range(olist.option_count)]
            assert rows[0].startswith("○ .skills.json"), rows
            assert rows[1].startswith("● review"), rows
            assert olist.highlighted == 1, "故障行不应抢占高亮"

            olist.highlighted = 0
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], SkillsScreen), "故障行 Enter 不应关窗"
            assert results == [], "故障行不可选中"
    asyncio.run(main())
