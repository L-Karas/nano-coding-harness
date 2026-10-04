"""主界面左栏分区部件：欢迎标题 / 聊天画板 / 底部停靠区（App 只组装，样式按 id 在 app.css）。"""

from __future__ import annotations

from pyfiglet import figlet_format
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from core.tui.footer import _FooterBar
from core.tui.surface import _StatusLine
from core.tui.theme import _PLACEHOLDER
from core.tui.widgets import _ClarifyList, _CommandInput


def _welcome_text(title: str, subtitle: str) -> Text:
    """欢迎标题：非 ASCII 字符滤除后渲染为块状大字（绿色），末尾接小字副标题。"""
    ascii_title = "".join(ch for ch in title.upper() if ch.isascii())
    art = ""
    if ascii_title:
        art = "\n".join(line.rstrip() for line in figlet_format(ascii_title).splitlines())
    text = Text(art, style="#4ade80", justify="center")
    if subtitle:
        text.append(("\n" if art else "") + subtitle, style="#94a3b8")
    return text


class _Welcome(Static):
    """空聊板时的居中欢迎标题（显隐由 App._set_welcome 切换，#welcome 样式见 app.css）。"""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__(_welcome_text(title, subtitle), id="welcome", markup=False)


class _ChatBoard(VerticalScroll):
    """聊天画板：不满一屏时消息从顶部向下填充，溢出后钉底（anchor）。"""

    def set_reactive(self, reactive, value) -> None:
        """钉底经 set_reactive 直写负滚动值（绕过 validate_scroll_y）：未溢出时钳为 0，
        避免内容被钉在底边、上方留白；溢出后的 native anchor 行为不变。"""
        if reactive.name in ("scroll_y", "scroll_target_y") and value < 0:
            value = 0
        super().set_reactive(reactive, value)


class _ChatDock(Vertical):
    """底部固定区：状态行 + / 与 @ 候选列表 + 权限 / clarify 列表 + 输入条 + 页脚两行。

    候选 OptionList 默认隐藏，由 _CommandInput 按 id 整批重建；同刻至多一组可见。"""

    def compose(self) -> ComposeResult:
        yield _StatusLine(id="status")
        yield OptionList(id="cmd-suggest")  # / 指令候选（id = 指令串）
        yield OptionList(id="file-suggest")  # @ 文件补全列表（仅 @ 提及编辑时可见）
        yield OptionList(Option("✓ Yes", id="yes"), Option("✗ No", id="no"), id="perm-list")
        yield _ClarifyList(id="clarify-list")
        with Horizontal(id="inputbar"):  # 上下粗实线输入条：>> 前缀 + 输入框
            yield Static(">> ", id="prompt-mark")
            yield _CommandInput(placeholder=_PLACEHOLDER, id="prompt")
        yield _FooterBar(id="footer-bar")
