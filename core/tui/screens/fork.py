"""用户消息分叉弹窗（/fork）。"""

from __future__ import annotations

from typing import Any

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from core.context.message import content_text
from core.tui.screens.base import _ListPickerScreen, _SessionRow, _rebuild_options

_FORK_PREVIEW_CHARS = 30  # fork 列表预览宽度（存储侧的标题截断长度由 session 自持）


def _preview(content: Any) -> str:
    """生成消息预览。

    Args:
        content: 消息内容；数组只取文本 part。

    Returns:
        压平空白后的前 _FORK_PREVIEW_CHARS 个字符；超长追加 …。
    """
    text = " ".join(content_text(content).split())
    return text[:_FORK_PREVIEW_CHARS] + ("…" if len(text) > _FORK_PREVIEW_CHARS else "")


class ForkScreen(_ListPickerScreen):
    """用户消息分叉弹窗（/fork）：Enter 回传选中消息 id，Esc 关闭。"""

    TITLE = "Fork from a user message"
    HINT = "  ↑/↓ browse    Enter/click fork from this message    Esc close"
    LIST_ID = "fork-list"

    def __init__(self, messages: list[Any]) -> None:
        """初始化分叉弹窗。

        Args:
            messages: 可选用户消息列表。
        """
        super().__init__()
        self._messages = messages

    def _row(self, message: Any) -> _SessionRow:
        """构造分叉选项行。

        Args:
            message: 用户消息。

        Returns:
            消息预览靠左、时间戳（到秒）右对齐的会话行（复用 /sessions 行排版）。
        """
        return _SessionRow(_preview(message.content), current=False, timestamp=message.timestamp)

    def _reload(self) -> None:
        """按消息列表重建分叉选项。"""
        _rebuild_options(self._list(), [Option(self._row(m), id=m.id) for m in self._messages])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """选中消息后关闭弹窗并回传消息 id。

        Args:
            event: 选项选中事件。
        """
        event.stop()
        self.dismiss(event.option_id)
