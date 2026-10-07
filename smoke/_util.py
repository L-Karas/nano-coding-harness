"""冒烟自检共用小工具：事件循环推进、程序化输入与输入条取件。"""
from __future__ import annotations

from core.tui.widgets import _CommandInput


async def settle(pilot, times: int = 10, delay: float = 0.02) -> None:
    """等 UI 稳定：连做几次短 pause 让事件循环推进（异步挂载/渲染完成）"""
    for _ in range(times):
        await pilot.pause(delay)


async def type_query(prompt: _CommandInput, pilot, text: str) -> None:
    """程序化输入并刷新补全候选（赋值不触达键入事件路径，须手动刷新）"""
    prompt.text = text
    prompt.cursor_location = (0, len(text))
    prompt._refresh_suggestions()
    await settle(pilot)


def prompt_of(app) -> _CommandInput:
    """当前输入条（#prompt）"""
    return app.query_one("#prompt", _CommandInput)
