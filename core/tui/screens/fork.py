"""用户消息分叉弹窗（/fork）。"""

from __future__ import annotations

from typing import Any

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from core.config import MESSAGE_PREVIEW_CHARS
from core.tui.screens.base import _ListPickerScreen, _rebuild_options


def _preview(content: str) -> str:
    """压平空白后的前 MESSAGE_PREVIEW_CHARS 个字符（超长加 …）。"""
    text = " ".join(content.split())
    return text[:MESSAGE_PREVIEW_CHARS] + ("…" if len(text) > MESSAGE_PREVIEW_CHARS else "")


class ForkScreen(_ListPickerScreen):
    """用户消息分叉弹窗（/fork）：Enter 回传选中消息 id，Esc 关闭。"""

    TITLE = "Fork from a user message"
    HINT = "  ↑/↓ browse    Enter fork from this message    Esc close"
    LIST_ID = "fork-list"

    def __init__(self, messages: list[Any]) -> None:
        super().__init__()
        self._messages = messages

    def _reload(self) -> None:
        _rebuild_options(self._list(), [Option(_preview(m.content), id=m.id) for m in self._messages])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option_id)
