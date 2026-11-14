"""部件取件门面：唯一知道 `#id` 与部件类型对应关系的地方。

App 与各 Adapter 只经属性取件，不再散落 `query_one("#…")`；部件树形状（含 app.css 依赖的 id）
变化时只改本模块。属性是惰性的：`__init__` 不查询，挂载后才可访问。
"""

from __future__ import annotations

from typing import Any

from textual.containers import Vertical
from textual.widgets import OptionList, Static

from core.tui.footer import _FooterBar
from core.tui.info_panel import _InfoPanel
from core.tui.panels import _ChatBoard
from core.tui.status import _StatusLine
from core.tui.widgets import _ClarifyList, _CommandInput


class Parts:
    """ChatApp 部件取件门面。

    宿主契约：构造时传入已挂载的 App（App 提供 `query_one`）。取件失败按 Textual 语义抛
    `NoMatches`，调用方自担「此时该部件应存在」。
    """

    def __init__(self, app: Any) -> None:
        """绑定 App。

        Args:
            app: Textual App 实例；本类只在属性访问时使用它。
        """
        self._app = app

    @property
    def board(self) -> _ChatBoard:
        """聊天画板（#chat）。

        Returns:
            卡片挂载的滚动容器。
        """
        return self._app.query_one("#chat", _ChatBoard)

    @property
    def prompt(self) -> _CommandInput:
        """输入条（#prompt）。

        Returns:
            带 / 与 @ 补全的输入框。
        """
        return self._app.query_one("#prompt", _CommandInput)

    @property
    def status(self) -> _StatusLine:
        """状态行（#status）。

        Returns:
            文本 + spinner 的状态行。
        """
        return self._app.query_one("#status", _StatusLine)

    @property
    def left(self) -> Vertical:
        """左栏容器（#left）。

        Returns:
            承载欢迎标题 / 聊板 / 停靠区的容器；`-welcome` 类挂在这里。
        """
        return self._app.query_one("#left", Vertical)

    @property
    def right(self) -> Vertical:
        """右栏容器（#right）。

        Returns:
            信息面板 + 折叠标签的容器；`-collapsed` 类挂在这里。
        """
        return self._app.query_one("#right", Vertical)

    @property
    def info_tab(self) -> Static:
        """右栏折叠标签（#info-tab-glyph）。

        Returns:
            »/« 字形部件。
        """
        return self._app.query_one("#info-tab-glyph", Static)

    @property
    def info(self) -> _InfoPanel:
        """右栏信息面板（#info-panel）。

        Returns:
            Todos / Background Tasks / Subagents 分区。
        """
        return self._app.query_one("#info-panel", _InfoPanel)

    @property
    def perm(self) -> OptionList:
        """权限确认列表（#perm-list）。

        Returns:
            Yes / No 选项列表。
        """
        return self._app.query_one("#perm-list", OptionList)

    @property
    def clarify(self) -> _ClarifyList:
        """clarify 选项列表（#clarify-list）。

        Returns:
            带 Enter 确认消息的选项列表。
        """
        return self._app.query_one("#clarify-list", _ClarifyList)

    @property
    def footer(self) -> _FooterBar:
        """页脚（#footer-bar 及其三行）。

        Returns:
            cwd / 占用条 / 审批模式三行。
        """
        return self._app.query_one(_FooterBar)
