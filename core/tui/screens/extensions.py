"""扩展列表弹窗（/extensions）：只读展示全部扩展与来源标签。"""

from __future__ import annotations

from textual.widgets.option_list import Option

from core.extension import ExtensionInfo
from core.tui.screens.base import _entry_row, _ListPickerScreen, _rebuild_options


def _summary(entry: ExtensionInfo) -> str:
    """第二行：已启用展示 hook 事件，未启用展示原因。"""
    if not entry.enabled:
        return entry.detail or "disabled"
    return f"hooks: {', '.join(entry.events)}" if entry.events else "no hooks"


class ExtensionsScreen(_ListPickerScreen):
    """扩展列表弹窗（/extensions）：行 = ● 名称 [user|builtin] + hook / 未启用原因。

    只读浏览：搜索过滤名称，Esc 关闭；Enter / 单击不触发动作。数据来自
    core.extension.list_extensions()（loader 扫描与内置注册时登记）。"""

    TITLE = "Extensions"
    HINT = "  ↑/↓ browse    Esc close"
    LIST_ID = "extensions-list"
    SEARCH_PLACEHOLDER = "Search extensions…"

    def __init__(self, extensions: list[ExtensionInfo]) -> None:
        super().__init__()
        self._extensions = extensions

    def _reload(self) -> None:
        _rebuild_options(
            self._list(),
            [Option(_entry_row(entry.name, _summary(entry), entry.origin),
                    id=f"{entry.origin}:{entry.name}")
             for entry in self._extensions if self._match(entry.name)],
        )
