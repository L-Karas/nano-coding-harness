"""扩展列表弹窗（/extensions）：展示全部扩展与来源标签，Ctrl+E 启用/停用。"""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets.option_list import Option

from core.extension import ExtensionInfo, list_extensions, toggle_extension
from core.tui.screens.base import _entry_row, _ListPickerScreen, _rebuild_options


def _hooks(entry: ExtensionInfo) -> str:
    """hook 事件摘要；无回调时显示 no hooks。"""
    return f"hooks: {', '.join(entry.events)}" if entry.events else "no hooks"


def _summary(entry: ExtensionInfo) -> str:
    """名称行之后：可选描述（第二行）+ 状态。

    启用项显示 hooks / no hooks；禁用但加载成功的项显示 `disabled · hooks: ...` /
    `disabled · no hooks`；未加载项显示 `load failed` / `missing register(api)` 等原因，
    配置禁用且加载失败时为 `disabled · <原因>`。
    """
    if entry.enabled:
        status = _hooks(entry)
    elif entry.detail:
        status = entry.detail
    else:
        status = f"disabled · {_hooks(entry)}"
    return f"{entry.description}\n{status}" if entry.description else status


class ExtensionsScreen(_ListPickerScreen):
    """扩展列表弹窗（/extensions）：行 = 状态字形 名称 [user|builtin] + 可选描述 + hook /
    未启用原因。

    已启用为绿色 ●，未启用 / 加载失败为红色 ○。Ctrl+E 切换当前高亮 user 扩展的
    `extension.json.enabled` 并即时切换派发开关（不重新注册）；数据来自
    core.extension.list_extensions()。builtin 与清单缺失 / 非法项不可切换，只提示原因。
    Enter / 单击保持无动作，搜索栏按名称过滤，Esc 关闭。"""

    TITLE = "Extensions"
    HINT = "  ↑/↓ browse    Ctrl+E enable/disable    Esc close"
    LIST_ID = "extensions-list"
    SEARCH_PLACEHOLDER = "Search extensions…"
    BINDINGS = [Binding("ctrl+e", "toggle_selected", "Enable/Disable", priority=True)]

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

    def _highlighted_entry(self) -> ExtensionInfo | None:
        """当前高亮行对应的登记项；高亮为空 / id 非法时返回 None。"""
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
        ok, message, enabled = toggle_extension(entry.name)
        if not ok:
            self.app.notify(message, title="⚠️ Extensions", severity="error")
            return
        self._extensions = list_extensions()
        self._reload()
        after = next((item for item in self._extensions
                      if item.origin == entry.origin and item.name == entry.name), None)
        warning = enabled is True and after is not None and not after.enabled
        self.app.notify(
            message,
            title="⚠️ Extensions" if warning else "✅ Extensions",
            severity="warning" if warning else "information",
        )
