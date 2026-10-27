"""冒烟自检共用小工具：事件循环推进、程序化输入、选项读取与回合等待。"""
from __future__ import annotations

from textual.widgets import OptionList

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


def screen_of(app):
    """当前栈顶弹窗（多个冒烟段连续压栈时取最新一个）。"""
    return app.screen_stack[-1]


def option_texts(widget: OptionList) -> list[str]:
    """OptionList 当前各行的纯文本。"""
    return [widget.get_option_at_index(i).prompt.plain for i in range(widget.option_count)]


def option_ids(widget: OptionList) -> list[str]:
    """OptionList 当前各行的 option id。"""
    return [widget.get_option_at_index(i).id for i in range(widget.option_count)]


def screen_option_texts(app, selector: str) -> list[str]:
    """栈顶弹窗内某个 OptionList 的各行纯文本。"""
    return option_texts(screen_of(app).query_one(selector, OptionList))


async def open_command(app, pilot, command: str, *, settle_times: int = 10):
    """聚焦输入条、填入指令并回车；返回提交后打开的栈顶弹窗（或主屏）。"""
    prompt = prompt_of(app)
    prompt.focus()
    prompt.text = command
    await pilot.press("enter")
    await settle(pilot, settle_times)
    return screen_of(app)


async def wait_idle(app, pilot, *, timeout: float = 15.0) -> None:
    """等演示 / 真实回合收尾；超时视为冒烟失败。"""
    for _ in range(int(timeout / 0.05)):
        await pilot.pause(0.05)  # 至少让出一帧，避免回合刚好结束时跳过 UI 收尾
        if not app._busy:
            return
    assert not app._busy, "回合未结束"


def assert_scrollbar_style(widget, what: str) -> None:
    """滑块样式同 app.css 共用规则（/sessions 列表 / /settings 下拉同款）：1 列宽、透明轨道、
    滑块 #475569，hover #94a3b8，拖动 #38bdf8。"""
    styles = widget.styles
    assert (styles.scrollbar_size_vertical == 1
            and styles.scrollbar_background.is_transparent
            and styles.scrollbar_color.hex.lower() == "#475569"
            and styles.scrollbar_color_hover.hex.lower() == "#94a3b8"
            and styles.scrollbar_color_active.hex.lower() == "#38bdf8"), \
        f"{what} 滑块样式与 /sessions 不一致: {styles.scrollbar_size_vertical}, {styles.scrollbar_color}"
