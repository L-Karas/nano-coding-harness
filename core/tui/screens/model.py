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

# /effort 思考深度档位（键 = shared_model_client().set_thinking_level 的 map 键，顺序即 Tabs 从左到右）
_EFFORT_LEVELS = ("minimal", "low", "medium", "high", "max")


class ModelPickerScreen(_ListPickerScreen):
    """模型选择弹窗（/model）：OptionList 展示可用模型（↑/↓ 原生首尾循环），Enter/点击行
    → 经 core.client.shared_model_client().set_model_client 切换当前模型（thinking_level
    不参与）并关闭弹窗；失败 notify 留在窗内重试，Esc 关闭。

    行 = 模型名 + [Provider Name]（暗灰）+ 可选 (current model) 标记。
    rows 由调用方从 core.client.get_model_list() 取值，本类只负责展示与切换。
    """

    TITLE = "🤖 Select a model (Enter switches the current model)"
    HINT = "  ↑/↓ browse    Enter switch model    Esc close"
    LIST_ID = "model-list"

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self._rows = rows
        self._current = ("", "")  # 当前模型 (provider, model)；_reload 经懒导入解析

    def _reload(self) -> None:
        # 当前模型取自 shared_model_client（不可用时为空，无标记）
        self._current = current_model_state()[:2]
        _rebuild_options(self._list(), [Option(self._row_text(model, tag), id=str(i))
                                        for i, (model, tag) in enumerate(self._rows)])

    def _row_text(self, model: str, provider_tag: str) -> Text:
        """行 = 模型名（亮色）+ [Provider Name]（暗灰）+ 当前模型标记（暗绿，同会话列表）"""
        return _model_row_text(model, provider_tag,
                               self._current == (provider_tag.strip("[]"), model))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中行：set_model_client 切换当前模型（thinking_level 不参与，走默认档位）；
        成功关窗，失败 notify 留在窗内。"""
        event.stop()
        index = event.option_index
        if not 0 <= index < len(self._rows):
            return
        model, provider_tag = self._rows[index]
        # 行内 Provider 为 "[Provider]"（展示用）；provider 名原样取自后端，不再做大小写变换
        provider = provider_tag.strip("[]")
        try:
            from core.client import shared_model_client
            shared_model_client().set_model_client(provider, model)
        except Exception as exc:
            self.app.notify(f"Failed to switch to {model}: {_error_text(exc)}",
                            title="⚠️ Model", severity="error")
            return
        self.app.notify(f"Switched to {model} {provider_tag}", title="✅ Model")
        self.dismiss(None)


class EffortScreen(ModalScreen[None]):
    """思考深度设置弹窗（/effort）：Tabs 标签从左到右 minimal/low/medium/high/max（←/→ 或
    点击切换，原生首尾循环）；Enter 经 core.client.shared_model_client().set_thinking_level
    设置当前档位并关窗；失败 notify 留在窗内重试，Esc 关闭。

    current = 当前档位（调用方取值；未知/空档位留在默认首档，Enter 即按首档重设）。
    布局纯 CSS（见 app.css #effort-picker）：弹窗定宽 46，标题/提示行与标签排居中由 CSS
    承担——auto 宽度容器里 align 不生效（Textual _arrange 对 auto 宽度按 0 算对齐偏移），
    故宽度以常量写死而非运行时量宽摆位。
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "apply", "Apply"),
    ]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("🎯 Thinking effort (Enter applies)", classes="picker-title"),
            _InstantTabs(*(Tab(level, id=level) for level in _EFFORT_LEVELS), id="effort-tabs"),
            Static("  ←/→ switch   Enter apply   Esc close", classes="picker-hint"),
            classes="picker",
            id="effort-picker",
        )

    def on_mount(self) -> None:
        tabs = self.query_one("#effort-tabs", Tabs)
        if self._current in _EFFORT_LEVELS:  # 当前档位点亮；空/未知值保持默认首档
            tabs.active = self._current
        self._sync_level_class(tabs, tabs.active)
        tabs.focus()

    def _sync_level_class(self, tabs: Tabs, level: str) -> None:
        """在 Tabs 节点上维护 -effort-<level> 类：CSS 按类把滑块（Underline 高亮段）染成
        对应档位色（见 app.css），与标签文字同色同映射。"""
        for lvl in _EFFORT_LEVELS:
            tabs.set_class(lvl == level, f"-effort-{lvl}")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """←/→ 或点击切档：滑块色随活动标签同步（on_mount 时 Tabs 首个激活事件可能早于
        本屏挂载完成，故 on_mount 里再显式同步一次）。"""
        event.stop()
        self._sync_level_class(event.tabs, event.tab.id or "")

    def action_apply(self) -> None:
        """Enter：当前标签档位 → set_thinking_level（懒导入：演示/冒烟环境可不装 openai）。
        Tabs 不消费 Enter，按键冒泡到本屏 BINDINGS 才轮到 apply。"""
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
