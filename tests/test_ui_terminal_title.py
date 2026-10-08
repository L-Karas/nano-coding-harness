"""TUI 终端窗口标题：挂载时向终端写 OSC 2 标题 nano-harness，退出（App Unmount）时清空。

Textual 8.2.8 的 App.TITLE 只喂 Header 控件，不会改真实窗口标题，故由 ChatApp 生命周期钩子
经 _set_terminal_title 直接向 driver 写转义序列。"""
import asyncio

from textual.drivers.headless_driver import HeadlessDriver

import core.tui.ui_textual as ui_textual
from core.tui.ui_textual import ChatApp

_TITLE_SEQ = "\x1b]2;nano-harness\x07"
_CLEAR_SEQ = "\x1b]2;\x07"


class _RecordingDriver:
    """driver.write 边界替身：只记录写出的底层序列。"""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)


def test_set_terminal_title_writes_osc2_sequence():
    driver = _RecordingDriver()
    ui_textual._set_terminal_title(driver, "nano-harness")
    assert driver.writes == [_TITLE_SEQ]


def test_set_terminal_title_empty_string_clears():
    driver = _RecordingDriver()
    ui_textual._set_terminal_title(driver, "")
    assert driver.writes == [_CLEAR_SEQ]


def test_set_terminal_title_ignores_missing_driver():
    ui_textual._set_terminal_title(None, "nano-harness")  # 不抛异常


def test_app_sets_title_on_mount_and_clears_on_unmount(monkeypatch):
    writes: list[str] = []
    monkeypatch.setattr(HeadlessDriver, "write", lambda self, data: writes.append(data))

    async def run_once() -> None:
        app = ChatApp(handle_query=lambda _query: None)
        async with app.run_test():
            await asyncio.sleep(0)
            assert _TITLE_SEQ in writes  # 运行期间标题已生效

    asyncio.run(run_once())
    assert _CLEAR_SEQ in writes  # 退出时清空标题
    assert writes.index(_TITLE_SEQ) < writes.index(_CLEAR_SEQ)
