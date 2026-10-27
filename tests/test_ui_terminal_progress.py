"""终端任务栏进度（OSC 9;4）：回合 / 后台任务 / 子代理运行中动态条，等待权限 / clarify 作答时黄色暂停，
全部结束才清除。

Windows Terminal 会把该序列同步到任务栏图标；不支持 / headless 的 driver 静默忽略。"""
import asyncio
import threading
from concurrent.futures import Future

from textual.drivers.headless_driver import HeadlessDriver

import core.tui.render as _render
from core.runtime_state import RuntimeState, TodoEntry
from core.tools import ToolResult
from core.tui.ui_textual import ChatApp
from tests.support import RecordingDriver, chat_app

_WORKING = "\x1b]9;4;3\x07"
_WAITING = "\x1b]9;4;4;100\x07"
_CLEAR = "\x1b]9;4;0\x07"


def _progress_seqs(writes: list[str]) -> list[str]:
    return [data for data in writes if data.startswith("\x1b]9;4;")]


def _record_writes(monkeypatch) -> list[str]:
    writes: list[str] = []
    monkeypatch.setattr(HeadlessDriver, "write", lambda self, data: writes.append(data))
    return writes


def _app_with_driver(state: RuntimeState | None = None) -> tuple[ChatApp, RecordingDriver]:
    app = ChatApp(handle_query=lambda _query: None, runtime_state=state or RuntimeState())
    driver = RecordingDriver()
    app._driver = driver
    return app, driver


class _FakeRuntime:
    """AgentRuntime 替身：只暴露 UI 判活 / 中断用到的两个方法。"""

    def __init__(self) -> None:
        self.running = False

    def is_running(self) -> bool:
        return self.running

    def interrupt(self) -> bool:
        return False


def test_progress_working_writes_indeterminate_state():
    app, driver = _app_with_driver()
    app._progress_working()
    assert driver.writes == [_WORKING]


def test_progress_waiting_writes_paused_state():
    app, driver = _app_with_driver()
    app._progress_waiting()
    assert driver.writes == [_WAITING]


def test_progress_clear_writes_clear_state():
    app, driver = _app_with_driver()
    app._progress_clear()
    assert driver.writes == [_CLEAR]


def test_progress_methods_ignore_missing_driver():
    app = ChatApp(handle_query=lambda _query: None)  # 未 run：App._driver 为 None
    app._progress_working()
    app._progress_waiting()
    app._progress_clear()


def test_background_task_sets_running_until_it_finishes():
    """回合外后台任务：在跑时动态条，完成后清除。"""
    state = RuntimeState()
    app, driver = _app_with_driver(state)
    state.register_background("bg-1", "terminal(pytest)")
    app._progress_sync()
    assert driver.writes == [_WORKING]
    state.complete_background("bg-1", ToolResult(content="done"))
    app._progress_sync()
    assert driver.writes == [_WORKING, _CLEAR]


def test_subagent_sets_running_until_removed():
    """子代理条目只在运行期存在：存在即动态条，移除后清除。"""
    state = RuntimeState()
    app, driver = _app_with_driver(state)
    state.register_subagent("sa-1", "explore")
    app._progress_sync()
    assert driver.writes == [_WORKING]
    state.remove_subagent("sa-1")
    app._progress_sync()
    assert driver.writes == [_WORKING, _CLEAR]


def test_todo_in_progress_does_not_drive_taskbar():
    """Todos 不参与任务栏判活。"""
    state = RuntimeState()
    state.set_todos([TodoEntry("write tests", "in_progress")])
    app, driver = _app_with_driver(state)
    app._progress_sync()
    assert driver.writes == [_CLEAR]


def test_runtime_turn_sets_running_without_ui_busy():
    """auto_loop 自动回合不经过 UI 的 _busy：runtime 在跑也要动态条。"""
    runtime = _FakeRuntime()
    app = ChatApp(handle_query=lambda _query: None, runtime=runtime,
                  runtime_state=RuntimeState())
    driver = RecordingDriver()
    app._driver = driver
    runtime.running = True
    app._progress_sync()
    assert driver.writes == [_WORKING]
    runtime.running = False
    app._progress_sync()
    assert driver.writes == [_WORKING, _CLEAR]


def test_progress_sync_is_idempotent():
    """同一状态重复同步不重复写 OSC（1s tick 可任意调用）。"""
    app, driver = _app_with_driver()
    app._busy = True
    app._progress_sync()
    app._progress_sync()
    app._progress_sync()
    assert driver.writes == [_WORKING]


def _capture_interval_callbacks(monkeypatch) -> list:
    """捕获挂载期 set_interval(1.0, ...) 注册的回调（测试手动驱动 tick，不等真实计时器）。"""
    callbacks: list = []
    original = ChatApp.set_interval

    def spy(self, delay, callback, **kwargs):
        if delay == 1.0:
            callbacks.append(callback)
        return original(self, delay, callback, **kwargs)

    monkeypatch.setattr(ChatApp, "set_interval", spy)
    return callbacks


def _drive_ticks(callbacks: list) -> None:
    for callback in callbacks:
        callback()


def test_idle_tick_picks_up_background_task(monkeypatch):
    """回合外后台任务起止：无需显式触发，1s 兜底 tick 重估任务栏。"""
    writes = _record_writes(monkeypatch)
    ticks = _capture_interval_callbacks(monkeypatch)
    state = RuntimeState()

    async def main() -> None:
        async with chat_app(runtime_state=state) as (app, _):
            state.register_background("bg-1", "terminal(pytest)")
            _drive_ticks(ticks)
            assert _progress_seqs(writes) == [_WORKING]
            state.complete_background("bg-1", ToolResult(content="done"))
            _drive_ticks(ticks)
            assert _progress_seqs(writes) == [_WORKING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WORKING, _CLEAR, _CLEAR]  # 退出兜底


def test_idle_tick_picks_up_runtime_turn(monkeypatch):
    """auto_loop 自动回合（cron / 后台结果注入）不经 UI 的 _busy：1s tick 也能点亮任务栏。"""
    writes = _record_writes(monkeypatch)
    ticks = _capture_interval_callbacks(monkeypatch)
    runtime = _FakeRuntime()

    async def main() -> None:
        app = ChatApp(handle_query=lambda _query: None, runtime=runtime, runtime_state=RuntimeState())
        async with app.run_test():
            runtime.running = True
            _drive_ticks(ticks)
            assert _progress_seqs(writes) == [_WORKING]
            runtime.running = False
            _drive_ticks(ticks)
            assert _progress_seqs(writes) == [_WORKING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WORKING, _CLEAR, _CLEAR]  # 退出兜底


def test_turn_sets_indeterminate_then_clears(monkeypatch):
    writes = _record_writes(monkeypatch)
    release = threading.Event()

    async def main() -> None:
        app = ChatApp(handle_query=lambda _query: release.wait(5))
        _render._APP = app  # run_test 不经 run()：照 smoke 手动挂渲染桥接
        try:
            async with app.run_test():
                app._send_user_query("hello")
                await asyncio.sleep(0.05)
                assert _progress_seqs(writes) == [_WORKING]  # 回合运行中：动态条
                release.set()
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    if not app._busy:
                        break
                assert not app._busy
        finally:
            _render._APP = None

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WORKING, _CLEAR, _CLEAR]  # _set_idle + 退出兜底


def test_permission_pauses_progress_and_answer_restores_working(monkeypatch):
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        async with chat_app() as (app, _):
            app._busy = True  # 前景回合仍在跑
            app._begin_permission("Allow?", [], Future())
            assert _progress_seqs(writes) == [_WAITING]  # 等待用户：黄色暂停
            app._answer_permission("yes")
            assert _progress_seqs(writes) == [_WAITING, _WORKING]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _WORKING, _CLEAR]  # 退出兜底


def test_permission_answered_outside_turn_clears_progress(monkeypatch):
    """回合外的权限询问（子代理 / cron）：作答后不得残留动态条。"""
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        async with chat_app() as (app, _):
            app._begin_permission("Allow?", [], Future())
            assert _progress_seqs(writes) == [_WAITING]
            app._answer_permission("yes")
            assert _progress_seqs(writes) == [_WAITING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _CLEAR, _CLEAR]  # 退出兜底再清一次


def test_clarify_pauses_progress_and_answer_restores_working(monkeypatch):
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        async with chat_app() as (app, _):
            app._busy = True  # 前景回合仍在跑
            app._begin_clarify(["Which one?"], False, Future())
            assert _progress_seqs(writes) == [_WAITING]  # 等待用户：黄色暂停
            app._answer_clarify("Which one?")
            assert _progress_seqs(writes) == [_WAITING, _WORKING]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _WORKING, _CLEAR]  # 退出兜底


def test_clarify_answered_outside_turn_clears_progress(monkeypatch):
    """回合外的 clarify（子代理）：作答后不得残留动态条。"""
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        async with chat_app() as (app, _):
            app._begin_clarify(["Which one?"], False, Future())
            assert _progress_seqs(writes) == [_WAITING]
            app._answer_clarify("Which one?")
            assert _progress_seqs(writes) == [_WAITING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _CLEAR, _CLEAR]  # 退出兜底再清一次


def test_turn_end_keeps_running_while_background_active(monkeypatch):
    """前台回合结束时后台任务仍在跑：保持动态条，等结束的 tick 再清除。"""
    writes = _record_writes(monkeypatch)
    state = RuntimeState()

    async def main() -> None:
        async with chat_app(runtime_state=state) as (app, _):
            state.register_background("bg-1", "terminal(pytest)")
            app._busy = True
            app._progress_sync()
            assert _progress_seqs(writes) == [_WORKING]
            app._set_idle()
            assert _progress_seqs(writes) == [_WORKING]  # 不闪空闲
            state.complete_background("bg-1", ToolResult(content="done"))
            app._progress_sync()
            assert _progress_seqs(writes) == [_WORKING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WORKING, _CLEAR, _CLEAR]  # 退出兜底


def test_turn_end_clears_pending_waiting_progress(monkeypatch):
    """回合结束兜底清理挂起询问：不得把黄色条留在任务栏上。"""
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        app = ChatApp(handle_query=lambda _query: None, runtime_state=RuntimeState())
        async with app.run_test():
            app._busy = True
            app._begin_permission("Allow?", [], Future())
            assert _progress_seqs(writes) == [_WAITING]
            app._set_idle()  # 回合结束：_reset_interactions 先清挂起，再重估进度
            assert _progress_seqs(writes) == [_WAITING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _CLEAR, _CLEAR]  # 退出兜底


def test_permission_answer_resumes_running_while_background_active(monkeypatch):
    """等待作答优先黄色；后台任务仍在跑时作答后回到动态条而不是清除。"""
    writes = _record_writes(monkeypatch)
    state = RuntimeState()

    async def main() -> None:
        async with chat_app(runtime_state=state) as (app, _):
            state.register_background("bg-1", "terminal(pytest)")
            app._begin_permission("Allow?", [], Future())
            assert _progress_seqs(writes) == [_WAITING]
            app._progress_sync()  # tick 到点：等待优先，不覆盖黄色
            assert _progress_seqs(writes) == [_WAITING]
            app._answer_permission("yes")
            assert _progress_seqs(writes) == [_WAITING, _WORKING]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _WORKING, _CLEAR]  # 退出兜底
