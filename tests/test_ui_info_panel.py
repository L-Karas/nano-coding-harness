"""右栏信息面板的折叠生命周期：三区（Todos / Background Tasks / Subagents）全空时默认折叠，
任一非空即自动展开；清空后若无手动展开则自动收回；Ctrl+←/→ 调宽，下限即折叠态。"""
import asyncio

import pytest
from textual.containers import Vertical
from textual.widgets import Static

import core.background_task as _bg
import core.sub_agent as _sa
from core.todo import todo as _todo
from core.todo.todo import Todo
from core.tui.info_panel import _InfoPanel
from core.tui.ui_textual import ChatApp


@pytest.fixture(autouse=True)
def _clean_panel_sources():
    """用例前后清空三区数据源：其它测试残留的活动会让 App 启动即自动展开，污染折叠基线。"""
    def clear() -> None:
        _todo.CURRENT_TODOS = []
        with _bg.BACKGROUND_LOCK:
            _bg.BACKGROUND_TASKS.clear()
        _sa.SUBAGENT_TASKS.clear()

    clear()
    yield
    clear()


def _is_collapsed(app: ChatApp) -> bool:
    return app.query_one("#right", Vertical).has_class("-collapsed")


def _refresh(app: ChatApp) -> None:
    """驱动一次面板同步（等价于 1s 轮询那一拍），避免测试等真实 interval。"""
    app.query_one("#info-panel", _InfoPanel)._refresh()


def test_panel_starts_collapsed():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            assert _is_collapsed(app)
            assert app.query_one("#info-tab-glyph", Static).content == "«"

    asyncio.run(main())


def test_auto_expands_when_todos_appear():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            _todo.CURRENT_TODOS = [Todo("write tests", "in_progress")]
            _refresh(app)
            assert not _is_collapsed(app)

    asyncio.run(main())


def test_auto_expands_when_background_task_appears():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            with _bg.BACKGROUND_LOCK:
                _bg.BACKGROUND_TASKS["bg-1"] = {"status": "running", "tool_call": "terminal(pytest)"}
            _refresh(app)
            assert not _is_collapsed(app)

    asyncio.run(main())


def test_auto_expands_when_subagent_appears():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            _sa.SUBAGENT_TASKS["sa-1"] = {"description": "explore", "phase": "thinking", "detail": ""}
            _refresh(app)
            assert not _is_collapsed(app)

    asyncio.run(main())


def test_auto_collapses_when_activity_ends():
    _todo.CURRENT_TODOS = [Todo("task", "in_progress")]

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            assert not _is_collapsed(app)  # 启动时已有任务：挂载即自动展开
            _todo.CURRENT_TODOS = []
            _refresh(app)
            assert _is_collapsed(app)

    asyncio.run(main())


def test_manual_expand_keeps_panel_open_when_empty():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test() as pilot:
            await pilot.click("#info-tab-glyph")  # 空态手动展开
            assert not _is_collapsed(app)
            _refresh(app)
            assert not _is_collapsed(app)  # 清空不再自动收回

    asyncio.run(main())


def test_manual_collapse_holds_until_next_activity():
    _todo.CURRENT_TODOS = [Todo("task", "in_progress")]

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test() as pilot:
            assert not _is_collapsed(app)
            await pilot.click("#info-tab-glyph")  # 运行中手动折叠
            assert _is_collapsed(app)
            _refresh(app)
            assert _is_collapsed(app)  # 数据仍非空也不抢回
            _todo.CURRENT_TODOS = []
            _refresh(app)
            assert _is_collapsed(app)
            _todo.CURRENT_TODOS = [Todo("next", "pending")]
            _refresh(app)
            assert not _is_collapsed(app)  # 下次「全空 → 非空」跳变再自动展开

    asyncio.run(main())


def test_ctrl_left_expands_from_collapsed_and_widens_to_cap():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            assert _is_collapsed(app)
            app.action_widen_info_panel()  # 折叠态 Ctrl+←：展开到默认 20%
            assert not _is_collapsed(app) and app._panel_pct == 20
            app.action_widen_info_panel()
            assert app._panel_pct == 30
            app.action_widen_info_panel()
            assert app._panel_pct == 40
            app.action_widen_info_panel()  # 封顶不超 40%
            assert app._panel_pct == 40

    asyncio.run(main())


def test_ctrl_right_narrows_to_collapse_and_back():
    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            app.action_widen_info_panel()  # 展开到 20%
            app.action_narrow_info_panel()  # 20% - 10% 低于下限：收成折叠态
            assert _is_collapsed(app)
            app.action_narrow_info_panel()  # 已是最低，空转
            assert _is_collapsed(app)
            app.action_widen_info_panel()  # 再次展开回 20%
            assert not _is_collapsed(app) and app._panel_pct == 20

    asyncio.run(main())


def test_auto_expand_restores_last_width():
    _todo.CURRENT_TODOS = [Todo("task", "in_progress")]

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test() as pilot:
            for _ in range(3):  # 20 → 30 → 40
                app.action_widen_info_panel()
            await pilot.click("#info-tab-glyph")  # 手动折叠
            assert _is_collapsed(app)
            _todo.CURRENT_TODOS = []
            _refresh(app)
            _todo.CURRENT_TODOS = [Todo("again", "pending")]
            _refresh(app)  # 自动展开应回到用户宽度
            assert not _is_collapsed(app) and app._panel_pct == 40

    asyncio.run(main())
