"""终端任务栏进度（OSC 9;4）：回合运行中动态条、等待权限/clarify 作答时黄色暂停、结束清除。

Windows Terminal 会把该序列同步到任务栏图标；不支持/headless 的 driver 静默忽略。"""
import asyncio
import threading
from concurrent.futures import Future

from textual.drivers.headless_driver import HeadlessDriver

import core.tui.render as _render
from core.tui.ui_textual import ChatApp

_WORKING = "\x1b]9;4;3\x07"
_WAITING = "\x1b]9;4;4;100\x07"
_CLEAR = "\x1b]9;4;0\x07"


class _RecordingDriver:
    """driver.write 边界替身：只记录写出的底层序列。"""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)


def _progress_seqs(writes: list[str]) -> list[str]:
    return [data for data in writes if data.startswith("\x1b]9;4;")]


def _record_writes(monkeypatch) -> list[str]:
    writes: list[str] = []
    monkeypatch.setattr(HeadlessDriver, "write", lambda self, data: writes.append(data))
    return writes


def _app_with_driver() -> tuple[ChatApp, _RecordingDriver]:
    app = ChatApp(handle_query=lambda _query: None)
    driver = _RecordingDriver()
    app._driver = driver
    return app, driver


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
        app = ChatApp(handle_query=lambda _query: None)
        async with app.run_test():
            app._busy = True  # 前景回合仍在跑
            app._begin_permission("Allow?", Future())
            assert _progress_seqs(writes) == [_WAITING]  # 等待用户：黄色暂停
            app._answer_permission("yes")
            assert _progress_seqs(writes) == [_WAITING, _WORKING]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _WORKING, _CLEAR]  # 退出兜底


def test_permission_answered_outside_turn_clears_progress(monkeypatch):
    """回合外的权限询问（子代理 / cron）：作答后不得残留动态条。"""
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        app = ChatApp(handle_query=lambda _query: None)
        async with app.run_test():
            app._begin_permission("Allow?", Future())
            assert _progress_seqs(writes) == [_WAITING]
            app._answer_permission("yes")
            assert _progress_seqs(writes) == [_WAITING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _CLEAR, _CLEAR]  # 退出兜底再清一次


def test_clarify_pauses_progress_and_answer_restores_working(monkeypatch):
    writes = _record_writes(monkeypatch)

    async def main() -> None:
        app = ChatApp(handle_query=lambda _query: None)
        async with app.run_test():
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
        app = ChatApp(handle_query=lambda _query: None)
        async with app.run_test():
            app._begin_clarify(["Which one?"], False, Future())
            assert _progress_seqs(writes) == [_WAITING]
            app._answer_clarify("Which one?")
            assert _progress_seqs(writes) == [_WAITING, _CLEAR]

    asyncio.run(main())
    assert _progress_seqs(writes) == [_WAITING, _CLEAR, _CLEAR]  # 退出兜底再清一次
