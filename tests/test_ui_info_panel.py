"""右栏信息面板的折叠生命周期：三区（Todos / Background Tasks / Subagents）全空时默认折叠，
任一非空即自动展开；清空后若无手动展开则自动收回；Ctrl+←/→ 调宽，下限即折叠态。"""
import asyncio

from textual.containers import Vertical
from textual.widgets import Static

from core.runtime_state import RuntimeState, TodoEntry
from core.tools import ToolResult
from core.tui.info_panel import _InfoPanel
from core.tui.ui_textual import ChatApp


def _app(state: RuntimeState) -> ChatApp:
    return ChatApp(handle_query=lambda _q: None, runtime_state=state)


def _is_collapsed(app: ChatApp) -> bool:
    return app.query_one("#right", Vertical).has_class("-collapsed")


def _refresh(app: ChatApp) -> None:
    """驱动一次面板同步（等价于 1s 轮询那一拍），避免测试等真实 interval。"""
    app.query_one("#info-panel", _InfoPanel)._refresh()


def test_panel_starts_collapsed():
    async def main() -> None:
        app = _app(RuntimeState())
        async with app.run_test():
            assert _is_collapsed(app)
            assert app.query_one("#info-tab-glyph", Static).content == "«"

    asyncio.run(main())


def test_auto_expands_when_todos_appear():
    state = RuntimeState()

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            state.set_todos([TodoEntry("write tests", "in_progress")])
            _refresh(app)
            assert not _is_collapsed(app)

    asyncio.run(main())


def test_auto_expands_when_background_task_appears():
    state = RuntimeState()

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            state.register_background("bg-1", "terminal(pytest)")
            _refresh(app)
            assert not _is_collapsed(app)

    asyncio.run(main())


def test_auto_expands_when_subagent_appears():
    state = RuntimeState()

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            state.register_subagent("sa-1", "explore")
            _refresh(app)
            assert not _is_collapsed(app)

    asyncio.run(main())


def test_auto_collapses_when_activity_ends():
    state = RuntimeState()
    state.set_todos([TodoEntry("task", "in_progress")])

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            assert not _is_collapsed(app)  # 启动时已有任务：挂载即自动展开
            state.set_todos([])
            _refresh(app)
            assert _is_collapsed(app)

    asyncio.run(main())


def test_manual_expand_keeps_panel_open_when_empty():
    state = RuntimeState()

    async def main() -> None:
        app = _app(state)
        async with app.run_test() as pilot:
            await pilot.click("#info-tab-glyph")  # 空态手动展开
            assert not _is_collapsed(app)
            _refresh(app)
            assert not _is_collapsed(app)  # 清空不再自动收回

    asyncio.run(main())


def test_manual_collapse_holds_until_next_activity():
    state = RuntimeState()
    state.set_todos([TodoEntry("task", "in_progress")])

    async def main() -> None:
        app = _app(state)
        async with app.run_test() as pilot:
            assert not _is_collapsed(app)
            await pilot.click("#info-tab-glyph")  # 运行中手动折叠
            assert _is_collapsed(app)
            _refresh(app)
            assert _is_collapsed(app)  # 数据仍非空也不抢回
            state.set_todos([])
            _refresh(app)
            assert _is_collapsed(app)
            state.set_todos([TodoEntry("next", "pending")])
            _refresh(app)
            assert not _is_collapsed(app)  # 下次「全空 → 非空」跳变再自动展开

    asyncio.run(main())


def test_ctrl_left_expands_from_collapsed_and_widens_to_cap():
    async def main() -> None:
        app = _app(RuntimeState())
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
        app = _app(RuntimeState())
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
    state = RuntimeState()
    state.set_todos([TodoEntry("task", "in_progress")])

    async def main() -> None:
        app = _app(state)
        async with app.run_test() as pilot:
            for _ in range(3):  # 20 → 30 → 40
                app.action_widen_info_panel()
            await pilot.click("#info-tab-glyph")  # 手动折叠
            assert _is_collapsed(app)
            state.set_todos([])
            _refresh(app)
            state.set_todos([TodoEntry("again", "pending")])
            _refresh(app)  # 自动展开应回到用户宽度
            assert not _is_collapsed(app) and app._panel_pct == 40

    asyncio.run(main())


def test_running_cancellable_row_stop_click_cancels():
    """运行中条目行尾 ■ = 指名停止；点它不触发展开。"""
    state = RuntimeState()
    calls = []
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    state.attach_cancel(bg_id, lambda: calls.append(bg_id))

    async def main() -> None:
        app = _app(state)
        async with app.run_test() as pilot:
            _refresh(app)
            row_stops = app.query_one("#bg-list").query(".-stop")
            assert len(row_stops) == 1
            await pilot.click(row_stops[0])
            assert calls == [bg_id]
            assert state.snapshot().background[0].cancellable is False
            label = app.query_one("#bg-list").query(".-info-text")[0]
            assert str(label.content)[:1] == "▸", "点 ■ 不应顺带展开"

    asyncio.run(main())


def test_row_text_click_still_toggles_expand():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    state.attach_cancel(bg_id, lambda: None)

    async def main() -> None:
        app = _app(state)
        async with app.run_test() as pilot:
            _refresh(app)
            label = app.query_one("#bg-list").query(".-info-text")[0]
            assert str(label.content)[:1] == "▸"
            await pilot.click(label)
            label = app.query_one("#bg-list").query(".-info-text")[0]
            assert str(label.content)[:1] == "▾"

    asyncio.run(main())


def test_completed_row_has_no_stop_control():
    state = RuntimeState()
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    state.attach_cancel(bg_id, lambda: None)
    state.complete_background(bg_id, ToolResult(content="done"))

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            _refresh(app)
            assert len(app.query_one("#bg-list").query(".-stop")) == 0

    asyncio.run(main())


def test_running_non_cancellable_row_has_no_stop_control():
    """worker 线程路径（纯同步工具）没有可杀句柄：运行中条目也不给 ■。"""
    state = RuntimeState()
    state.register_background("bg-1", "terminal(pytest)")  # 未 attach_cancel

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            _refresh(app)
            assert state.snapshot().background[0].status == "running"
            assert len(app.query_one("#bg-list").query(".-stop")) == 0

    asyncio.run(main())


def test_subagent_row_expands_to_goal_not_description():
    """折叠只显示阶段；展开补上 goal，description 不在右栏展示。"""
    state = RuntimeState()

    async def main() -> None:
        app = _app(state)
        async with app.run_test() as pilot:
            state.register_subagent("sa-1", "explore the whole repo", "find the entry point")
            _refresh(app)
            await pilot.pause()  # 等 Textual 处理行 mount
            label = app.query_one("#subagents-list").query(".-info-text")[0]
            collapsed = str(label.content)
            assert "[thinking]" in collapsed
            assert "find the entry point" not in collapsed
            assert "explore the whole repo" not in collapsed

            await pilot.click(label)
            await pilot.pause()
            expanded = str(app.query_one("#subagents-list").query(".-info-text")[0].content)
            assert "find the entry point" in expanded
            assert "explore the whole repo" not in expanded

    asyncio.run(main())


def test_subagent_background_record_not_listed_in_bg_section():
    """子代理的 bg 记录只作结果回流：Background Tasks 不列它，只在 Subagents 分区露面。"""
    state = RuntimeState()
    state.register_background("bg-1", "terminal(pytest)")
    state.register_background("bg-2", "spawn_subagent(description=explore)", is_subagent=True)

    async def main() -> None:
        app = _app(state)
        async with app.run_test():
            _refresh(app)
            ids = [row.work_id for row in app.query_one("#bg-list").query(".info-row")]
            assert ids == ["bg-1"]

    asyncio.run(main())
