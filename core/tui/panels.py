"""主界面分区部件：ChatApp 左栏的欢迎标题 / 聊天画板 / 底部停靠区（拆自 ui_textual.compose）。

compose 只保留左右分栏骨架，逐区 yield 本模块的自定义部件；每个分区把
自己的内部结构收进自己的 compose，样式仍按 id 在同目录 app.css 维护。
与 ChatApp 的联动沿用全局 id 查询（状态行 / footer 更新、_CommandInput
的候选列表、渲染 API 的卡片追加均按 id 取件），DOM 层级与拆出前完全一致。

- _Welcome：聊板为空（首次启动 / 新建会话）时的居中欢迎标题（大字标题 + 小字副标题）；
- _ChatBoard：聊天画板滚动容器（不满一屏时消息从顶部向下填充，见类 docstring）；
- _ChatDock：底部固定区（状态行 + / 指令与 @ 文件候选列表 + 权限 yes/no 列表 + clarify
  选项列表 + 输入条 + 工作目录行）。
"""

from __future__ import annotations

from pyfiglet import figlet_format
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from core.tui.theme import _PLACEHOLDER
from core.tui.widgets import _ClarifyList, _CommandInput


def _welcome_text(title: str, subtitle: str) -> Text:
    """欢迎标题：滤除非 ASCII 后渲染为 smblock 块状大字（绿色），末尾接小字副标题"""
    ascii_title = "".join(ch for ch in title.upper() if ch.isascii())
    art = ""
    if ascii_title:
        art = "\n".join(line.rstrip() for line in figlet_format(ascii_title).splitlines())
    text = Text(art, style="#4ade80", justify="center")
    if subtitle:
        text.append(("\n" if art else "") + subtitle, style="#94a3b8")
    return text


class _Welcome(Static):
    """聊板为空（首次启动 / 新建会话）时的居中欢迎标题（figlet 块状大字 + 小字副标题）。
    占满 1fr、内容居中与显隐规则见 app.css #welcome；显示/隐藏由 ChatApp._set_welcome 切换。"""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__(_welcome_text(title, subtitle), id="welcome", markup=False)


class _ChatBoard(VerticalScroll):
    """聊天画板滚动容器：不满一屏时消息从顶部向下填充。

    Textual 的 anchor()（钉底）由 compositor 在每次布局把滚动压到「内容底 - 视口高」：
    内容不满一屏时为负值，卡片被钉在画板底边、上方整片留白（先前的消息只有等新渲染
    出现才被顶上去）。compositor 经 set_reactive 直写滚动、绕过 ≥0 的 validate_scroll_y，
    这里把钉底的负滚动钳制为 0：未溢出时从顶部向下填充；溢出后钉底跟随、
    浏览历史解除 / 滚回底部自动恢复等 native anchor 行为全部不变。
    """

    def set_reactive(self, reactive, value) -> None:
        if reactive.name in ("scroll_y", "scroll_target_y") and value < 0:
            value = 0
        super().set_reactive(reactive, value)


class _ChatDock(Vertical):
    """底部固定区：状态行 + 指令补全列表（按需显示）+ @ 文件补全列表 + 权限确认列表
    + clarify 选项列表 + 输入条 + 最底工作目录行。

    / 与 @ 候选 OptionList 默认隐藏（见 app.css）：选项是纯数据（无 DOM 子项），
    由 _CommandInput 在键入时按 id 用 set_options 整批重建；同刻至多一组可见。
    id="dock" 由 ChatApp.compose 在挂载处给出（app.css 的 dock: bottom 把整个停靠区钉在左栏底部）。
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield OptionList(id="cmd-suggest")  # / 指令候选：选项由 _CommandInput 按过滤结果重建（id = 指令串）
        yield OptionList(id="file-suggest")  # @ 文件补全列表（仅 @ 提及编辑时可见）
        # 权限确认 yes/no 列表：默认隐藏，权限请求时由 ChatApp 弹出并聚焦（行为见 ui_textual 权限确认段）
        yield OptionList(Option("✓ Yes", id="yes"), Option("✗ No", id="no"), id="perm-list")
        # clarify 选项列表（含末尾 Other）：默认隐藏，工具请求时由 ChatApp 弹出并聚焦（见 ui_textual clarify 段）
        yield _ClarifyList(id="clarify-list")
        with Horizontal(id="inputbar"):  # 上下粗实线输入条：>> 前缀 + 输入框
            yield Static(">> ", id="prompt-mark")
            yield _CommandInput(placeholder=_PLACEHOLDER, id="prompt")
        yield Static("", id="footer", markup=False)  # 最底行：当前工作目录 (git 分支)
