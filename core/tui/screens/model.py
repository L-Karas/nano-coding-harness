"""模型与思考档位弹窗：/model 切换模型、/effort 切换思考深度。"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, Tab, Tabs
from textual.widgets.option_list import Option

from core.tui.screens.base import (
    _InstantTabs,
    _ListPickerScreen,
    _error_text,
    _model_row_text,
    _rebuild_options,
)
from core.tui.utils import current_model_state

# 档位键 = set_thinking_level 的 map 键，顺序即 Tabs 从左到右
_EFFORT_LEVELS = ("minimal", "low", "medium", "high", "max")


class ModelPickerScreen(_ListPickerScreen):
    """模型选择弹窗（/model）：Enter/点击行经 set_model_client 切换当前模型并关窗；
    失败 notify 留在窗内重试。rows 来自 core.client.get_model_list()。"""

    TITLE = "Select a model"
    HINT = "  ↑/↓ browse    Enter switch model    Esc close"
    LIST_ID = "model-list"
    SEARCH_PLACEHOLDER = "Search models…"

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self._rows = rows
        self._current = ("", "")  # 当前模型 (provider, model)；_reload 经懒导入解析

    def _reload(self) -> None:
        self._current = current_model_state()[:2]
        _rebuild_options(self._list(),
                         [Option(self._row_text(model, provider), id=str(i))
                          for i, (model, provider) in enumerate(self._rows)
                          if self._match(model)])

    def _row_text(self, model: str, provider: str) -> Text:
        return _model_row_text(model, provider, self._current == (provider, model))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        option_id = event.option_id or ""
        if not option_id.isdigit():  # 选项 id = 原始行下标（过滤后列表位置不再对应数据源）
            return
        index = int(option_id)
        if not 0 <= index < len(self._rows):
            return
        model, provider = self._rows[index]
        try:
            from core.client import shared_model_client
            shared_model_client().set_model_client(provider, model)
        except Exception as exc:
            self.app.notify(f"Failed to switch to {model}: {_error_text(exc)}",
                            title="⚠️ Model", severity="error")
            return
        self.app.notify(f"Switched to {model} [{provider}]", title="✅ Model")
        self.dismiss(None)


class EffortScreen(ModalScreen[None]):
    """思考深度设置弹窗（/effort）：Tabs 五档（←/→ 或点击，首尾循环）；Enter 经
    set_thinking_level 生效并关窗，失败留在窗内；Esc 关闭。current 空/未知时留在默认首档。

    布局纯 CSS（#effort-picker 定宽 46）：auto 宽度容器里 align 不生效，故宽度写死。"""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "apply", "Apply"),
    ]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        picker = Vertical(
            _InstantTabs(*(Tab(level, id=level) for level in _EFFORT_LEVELS), id="effort-tabs"),
            Static("  ←/→ switch   Enter apply   Esc close", classes="picker-hint"),
            classes="picker",
            id="effort-picker",
        )
        picker.border_title = "Thinking effort"
        yield picker

    def on_mount(self) -> None:
        tabs = self.query_one("#effort-tabs", Tabs)
        if self._current in _EFFORT_LEVELS:
            tabs.active = self._current
        self._sync_level_class(tabs, tabs.active)
        tabs.focus()

    def _sync_level_class(self, tabs: Tabs, level: str) -> None:
        """维护 -effort-<level> 类：CSS 按类给滑块（Underline 高亮段）与标签同色上色。"""
        for lvl in _EFFORT_LEVELS:
            tabs.set_class(lvl == level, f"-effort-{lvl}")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """首档激活事件可能早于本屏挂载完成，故 on_mount 里再显式同步一次。"""
        event.stop()
        self._sync_level_class(event.tabs, event.tab.id or "")

    def action_apply(self) -> None:
        """Enter（Tabs 不消费，冒泡到本屏 BINDINGS 才轮到 apply）。"""
        level = self.query_one("#effort-tabs", Tabs).active
        if level not in _EFFORT_LEVELS:
            return
        try:
            from core.client import shared_model_client
            shared_model_client().set_thinking_level(level)
        except Exception as exc:
            self.app.notify(f"Failed to set effort: {_error_text(exc)}",
                            title="⚠️ Effort", severity="error")
            return
        self.app.notify(f"Thinking effort → {level}", title="✅ Effort")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
