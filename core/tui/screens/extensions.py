"""扩展列表弹窗（/extensions）：只读展示全部扩展与来源标签。"""

from __future__ import annotations

from textual.widgets.option_list import Option

from core.extension import ExtensionInfo
from core.tui.screens.base import _entry_row, _ListPickerScreen, _rebuild_options


def _summary(entry: ExtensionInfo) -> str:
    """名称行之后：可选描述（第二行）+ hook 事件 / 未启用原因（其后一行）。"""
    if not entry.enabled:
        status = entry.detail or "disabled"
    else:
        status = f"hooks: {', '.join(entry.events)}" if entry.events else "no hooks"
    return f"{entry.description}\n{status}" if entry.description else status


class ExtensionsScreen(_ListPickerScreen):
    """扩展列表弹窗（/extensions）：行 = 状态字形 名称 [user|builtin] + 可选描述 + hook /
    未启用原因。

    已启用为绿色 ●，未启用为红色 ○（同 /mcp 连接失败的 server）。只读浏览：搜索过滤
    名称，Esc 关闭；Enter / 单击不触发动作。数据来自 core.extension.list_extensions()
    （loader 扫描与内置注册时登记）。"""

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
            [Option(_entry_row(entry.name, _summary(entry), entry.origin,
                               enabled=entry.enabled),
                    id=f"{entry.origin}:{entry.name}")
             for entry in self._extensions if self._match(entry.name)],
        )
