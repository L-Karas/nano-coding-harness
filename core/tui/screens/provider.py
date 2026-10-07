"""模型提供商配置弹窗（/provider）：配置状态列表 + API Key 录入窗。"""

from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static
from textual.widgets.option_list import Option

from core.tui.screens.base import (
    _InlineConfirm,
    _ListPickerScreen,
    _error_text,
    _field,
    _rebuild_options,
)


class ProviderScreen(_InlineConfirm, _ListPickerScreen):
    """模型提供商配置弹窗（/provider）。

    rows = (provider, 是否已配置)，来自 core.client.get_provider_list()；Enter/点击 → ApiKeyScreen
    录入（configure_provider 落盘后重取真源刷新）；Delete → 原地确认后 unconfigure_provider。
    """

    TITLE = "Model Providers"
    HINT = "  ↑/↓ browse    Enter set API Key    Delete remove config    Esc close"
    LIST_ID = "provider-list"
    SEARCH_PLACEHOLDER = "Search providers…"
    CONFIRM = True
    # priority：搜索栏聚焦时 Delete 仍是窗级删除（Input 默认把 delete 当删字符）
    BINDINGS = [Binding("delete", "remove_selected", "Delete", priority=True)]

    _NAME_STYLE = "#f8fafc"
    _UNCONFIGURED_STYLE = "#94a3b8"  # 灰：未配置
    _CONFIGURED_STYLE = "#16a34a"  # 暗绿：已配置

    def __init__(self, rows: list[tuple[str, bool]]) -> None:
        super().__init__()
        self._rows = list(rows)  # 快照仅作首屏；配置变更后经 _refresh 重取真源

    def _reload(self) -> None:
        _rebuild_options(self._list(),
                         [Option(self._row_text(provider, configured), id=provider)
                          for provider, configured in self._rows
                          if self._match(provider)])

    def _row_text(self, provider: str, configured: bool) -> Text:
        """行 = provider（后端原名）+ 状态（未配置灰 / 已配置暗绿）。"""
        status = "[● configured]" if configured else "[○ unconfigured]"
        status_style = self._CONFIGURED_STYLE if configured else self._UNCONFIGURED_STYLE
        return Text.assemble((provider, self._NAME_STYLE),
                             (" " + status, status_style))

    def _refresh(self) -> None:
        """配置变更后重取 core.client 真源重建列表（可能新增整行，本地改镜像会漏）。"""
        from core.client import get_provider_list
        self._rows = get_provider_list()
        self._reload()

    def _selected(self, option_id: Optional[str]) -> None:
        """Enter/点击选中行：弹出 ApiKeyScreen 录入该 provider 的 API Key。"""
        if option_id:
            self.app.push_screen(ApiKeyScreen(option_id),
                                 callback=lambda api_key: self._on_key_submitted(option_id, api_key))

    def _on_key_submitted(self, provider: str, api_key: Optional[str]) -> None:
        """ApiKeyScreen 回调：None=Esc 取消；有 key 则落盘并刷新状态。"""
        if not api_key:
            return
        try:
            from core.client import configure_provider
            configure_provider(provider, api_key)
        except Exception as exc:
            self.app.notify(f"Failed to configure {provider}: {_error_text(exc)}",
                            title="⚠️ Provider", severity="error")
            return
        self._refresh()
        self._list().focus()  # 焦点回列表，可继续配置下一家

    def action_remove_selected(self) -> None:
        """Delete：原地确认后删除选中 provider 配置；未配置行无可删内容，提示后返回。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        # 过滤后高亮位置不再对应 self._rows，按选项 id 回查
        provider = str(olist.get_option_at_index(olist.highlighted).id)
        configured = dict(self._rows).get(provider, False)
        if not configured:
            self.app.notify(f"{provider} is not configured — nothing to delete", title="ℹ️ Provider")
            return
        self._ask_confirm(f"Remove key for {provider}?", lambda: self._remove_config(provider))

    def _remove_config(self, provider: str) -> None:
        try:
            from core.client import unconfigure_provider
            unconfigure_provider(provider)
        except Exception as exc:
            self.app.notify(f"Failed to delete {provider} config: {_error_text(exc)}",
                            title="⚠️ Provider", severity="error")
            return
        self._refresh()


class ApiKeyScreen(ModalScreen[Optional[str]]):
    """API Key 录入弹窗：掩码输入，Enter 返回 key（空 key 不关闭），Esc 取消返回 None。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, provider: str) -> None:
        super().__init__()
        self._provider = provider

    def compose(self) -> ComposeResult:
        picker = Vertical(
            _field(Input(placeholder="API Key (masked)", password=True, id="api-key-input")),
            Static("  Enter save    Esc cancel", classes="picker-hint"),
            classes="picker",
        )
        picker.border_title = f"Enter the API Key for {self._provider}"
        yield picker

    def on_mount(self) -> None:
        self.query_one("#api-key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter：key 非空才关闭（空输入留窗内重录）。"""
        event.stop()
        key = event.value.strip()
        if key:
            self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)
