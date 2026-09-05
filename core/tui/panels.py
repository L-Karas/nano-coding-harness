"""主界面分区部件：ChatApp 左栏的标题栏 / 聊天画板 / 底部停靠区（拆自 ui_textual.compose）。

compose 只保留左右分栏骨架，逐区 yield 本模块的自定义部件；每个分区把
自己的内部结构收进自己的 compose，样式仍按 id 在同目录 app.css 维护。
与 ChatApp 的联动沿用全局 id 查询（状态行 / footer 更新、_CommandInput
的候选列表、渲染 API 的卡片追加均按 id 取件），DOM 层级与拆出前完全一致。

- _TitleBar：标题栏（最外部单线圆角框，内部直接是标题蓝底文本块）；
- _ChatBoard：聊天画板滚动容器（不满一屏时消息从顶部向下填充，见类 docstring）；
- _ChatDock：底部固定区（状态行 + / 指令与 @ 文件候选列表 + 输入条 + 工作目录行）。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static

from core.tui.theme import _PLACEHOLDER, _markup
from core.tui.utils import SLASH_COMMANDS
from core.tui.widgets import _CommandInput


class _TitleBar(Vertical):
    """标题栏：最外部单线圆角框，内部直接是标题背景块（背景只在框内，不遮圆角字形）。"""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__(id="titlebar")
        self._title = title
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        yield Static(_markup(f"[bold cyan]{self._title}[/bold cyan]\n[dim]{self._subtitle}[/dim]",
                             justify="center"),
                     id="titlebar-text", markup=False)


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
    """底部固定区：状态行 + 指令补全列表（按需显示）+ @ 文件补全列表 + 输入条 + 最底工作目录行。

    / 与 @ 候选 ListView 默认隐藏（见 app.css），由 _CommandInput 在键入时按 id 显示/填充；
    cmd-suggest 子项与 SLASH_COMMANDS 一一对应（_CommandInput 按固定子项切换 display）。
    id="dock" 由 ChatApp.compose 在挂载处给出（app.css 的 dock: bottom 把整个停靠区钉在左栏底部）。
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield ListView(*(ListItem(Label(cmd)) for cmd in SLASH_COMMANDS), id="cmd-suggest")
        yield ListView(id="file-suggest")  # @ 文件补全列表（仅 @ 提及编辑时可见）
        with Horizontal(id="inputbar"):  # 上下粗实线输入条：>> 前缀 + 输入框
            yield Static(">> ", id="prompt-mark")
            yield _CommandInput(placeholder=_PLACEHOLDER, id="prompt")
        yield Static("", id="footer", markup=False)  # 最底行：当前工作目录 (git 分支)
