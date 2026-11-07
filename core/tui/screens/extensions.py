"""扩展列表弹窗（/extensions）：状态标签展示启用状态，Ctrl+E 启用/停用。"""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.widgets.option_list import Option

from core.extension import ExtensionInfo, list_extensions, toggle_extension
from core.tui.screens.base import _entry_row, _ListPickerScreen, _rebuild_options
from core.tui.theme import ACTIVE_STYLE, DISABLED_TEXT, HOOK_TEXT, INACTIVE_STYLE

# 状态后缀 / 颜色：enabled 不显示标签（绿色 ●）；disabled 米黄 ○；error 红色 ○。
_STATUS_STYLES = {"enabled": ACTIVE_STYLE, "disabled": DISABLED_TEXT,
                  "error": INACTIVE_STYLE}


def _hooks(entry: ExtensionInfo) -> str:
    """生成 hook 事件摘要。

    Args:
        entry: 扩展登记项。

    Returns:
        `hooks: ...` 文本；无回调时为 `no hooks`。
    """
    return f"hooks: {', '.join(entry.events)}" if entry.events else "no hooks"


def _row(entry: ExtensionInfo) -> Text:
    """构造扩展列表行。

    Args:
        entry: 扩展登记项。

    Returns:
        状态字形 + 名称 + (状态) + [来源] + 描述 / hooks；失败原因不展示。
    """
    status = entry.status
    row = _entry_row(
        entry.name,
        entry.description,
        entry.origin,
        enabled=entry.enabled,
        status="" if status == "enabled" else status,
        head_style=_STATUS_STYLES[status],
    )
    row.append("\n" + _hooks(entry), style=HOOK_TEXT)
    return row


class ExtensionsScreen(_ListPickerScreen):
    """扩展列表弹窗（/extensions）：行 = 状态字形 名称（状态标签）[user|builtin] + 描述 / hook。

    已启用为绿色 ● 且不显示状态标签；禁用为米黄 ○ + `(disabled)`；加载 / 注册失败为红色 ○
    + `(error)`。状态标签在来源标签前，失败原因不展示。第二行仅显示可选描述与 hooks。
    Ctrl+E 切换当前高亮 user 扩展的 `extension.json.enabled` 并即时切换派发开关（不重新
    注册）；数据来自 core.extension.list_extensions()。builtin 与清单缺失 / 非法项不可切换，
    只提示原因。Enter / 单击保持无动作，搜索栏按名称过滤，Esc 关闭。"""

    TITLE = "Extensions"
    HINT = "  ↑/↓ browse    Ctrl+E enable/disable    Esc close"
    LIST_ID = "extensions-list"
    SEARCH_PLACEHOLDER = "Search extensions…"
    BINDINGS = [Binding("ctrl+e", "toggle_selected", "Enable/Disable", priority=True)]

    def __init__(self, extensions: list[ExtensionInfo]) -> None:
        """初始化扩展列表弹窗。

        Args:
            extensions: 扩展登记项列表。
        """
        super().__init__()
        self._extensions = extensions

    def _reload(self) -> None:
        """按当前搜索词重建扩展列表。"""
        _rebuild_options(
            self._list(),
            [Option(_row(entry), id=f"{entry.origin}:{entry.name}")
             for entry in self._extensions if self._match(entry.name)],
        )

    def _highlighted_entry(self) -> ExtensionInfo | None:
        """取当前高亮行对应的登记项。

        Returns:
            高亮项；高亮为空 / id 非法时为 None。
        """
        olist = self._list()
        if olist.highlighted is None:
            return None
        option = olist.get_option_at_index(olist.highlighted)
        if option.id is None:
            return None
        origin, _, name = str(option.id).partition(":")
        return next((entry for entry in self._extensions
                     if entry.origin == origin and entry.name == name), None)

    def action_toggle_selected(self) -> None:
        """Ctrl+E：切换选中 user 扩展的启用状态并就地刷新。

        刷新后若「请求启用但扩展仍未生效」（加载失败），用警告 toast 提示；成功启用 /
        停用用成功 toast。builtin 没有 extension.json，不写入任何状态。"""
        entry = self._highlighted_entry()
        if entry is None:
            return
        if entry.origin == "builtin":
            self.app.notify("Builtin extensions cannot be toggled.",
                            title="⛔ Extensions")
            return
        result = toggle_extension(entry.name)
        if not result.ok:
            self.app.notify(result.message, title="⚠️ Extensions", severity="error")
            return
        self._extensions = list_extensions()
        self._reload()
        self.app.notify(
            result.message,
            title="⚠️ Extensions" if result.warning else "✅ Extensions",
            severity="warning" if result.warning else "information",
        )
