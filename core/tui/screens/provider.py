"""模型提供商配置弹窗（/provider）：配置状态列表 + API Key 录入窗。"""

from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from core.tui.screens.base import _InlineConfirm, _ListPickerScreen, _error_text, _rebuild_options


class ProviderScreen(_InlineConfirm, _ListPickerScreen):
    """模型提供商配置弹窗（/provider）：OptionList 展示配置状态（↑/↓ 原生首尾循环），
    Enter/点击行 → ApiKeyScreen 录入该 provider 的 API Key（掩码显示），再次 Enter 经
    core.client.configure_provider 落盘并重取列表刷新；Delete → 窗内底部红字原地确认后经
    core.client.unconfigure_provider 删除该 provider 配置；Esc 撤销确认 / 关闭。

    rows = (provider, 是否已配置) 结构化状态，由调用方从 core.client.get_provider_list()
    取值；本类只负责展示，状态变更后重取真源而非本地维护镜像。
    """

    TITLE = "Model Providers"
    HINT = "  ↑/↓ browse    Enter set API Key    Delete remove config    Esc close"
    LIST_ID = "provider-list"
    CONFIRM = True
    BINDINGS = [("delete", "remove_selected", "Delete")]

    _NAME_STYLE = "#f8fafc"
    _UNCONFIGURED_STYLE = "#94a3b8"  # 灰：未配置
    _CONFIGURED_STYLE = "#16a34a"  # 暗绿：已配置

    def __init__(self, rows: list[tuple[str, bool]]) -> None:
        super().__init__()
        self._rows = list(rows)  # 快照仅作首屏；配置变更后经 _refresh 重取真源

    def _reload(self) -> None:
        _rebuild_options(self._list(),
                         [Option(self._row_text(provider, configured), id=provider)
                          for provider, configured in self._rows])

    def _row_text(self, provider: str, configured: bool) -> Text:
        """行 = provider（后端原名，原样展示）+ 状态（未配置灰 / 已配置暗绿）"""
        status = "[● configured]" if configured else "[○ unconfigured]"
        status_style = self._CONFIGURED_STYLE if configured else self._UNCONFIGURED_STYLE
        return Text.assemble((provider, self._NAME_STYLE),
                             (" " + status, status_style))

    def _refresh(self) -> None:
        """配置落盘后重取 core.client 最新状态重建列表（configure_provider 可能新增整行，
        本地改镜像会漏；能走到这里说明 configure/unconfigure 已成功导入 core.client）。"""
        from core.client import get_provider_list
        self._rows = get_provider_list()
        self._reload()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中行：弹出 ApiKeyScreen 录入该 provider 的 API Key（原地确认中 = 确认删除）"""
        event.stop()
        if self._run_confirm():
            return
        provider = event.option_id
        if provider:
            self.app.push_screen(ApiKeyScreen(provider),
                                 callback=lambda api_key: self._on_key_submitted(provider, api_key))

    def _on_key_submitted(self, provider: str, api_key: Optional[str]) -> None:
        """ApiKeyScreen 关闭回调：None=Esc 取消；有 key 则 configure_provider 落盘，
        成功后重取列表刷新状态。"""
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
        """Delete：窗内底部红字原地确认，Enter 后删除选中 provider 的配置并重取列表刷新；
        已未配置行无可删内容，提示后返回（不进入确认）。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        provider, configured = self._rows[olist.highlighted]
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
    """API Key 录入弹窗（ProviderScreen 选中行后弹出）：Input(password=True) 掩码显示，
    Enter 提交返回 key（空 key 不关闭，防误提交），Esc 取消返回 None。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, provider: str) -> None:
        super().__init__()
        self._provider = provider

    def compose(self) -> ComposeResult:
        picker = Vertical(
            Input(placeholder="API Key (masked)", password=True, id="api-key-input"),
            Static("  Enter save    Esc cancel", classes="picker-hint"),
            classes="picker",
        )
        picker.border_title = f"Enter the API Key for {self._provider}"
        yield picker

    def on_mount(self) -> None:
        self.query_one("#api-key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter 提交：key 非空才关闭返回（空输入留在窗口内重新录入）"""
        event.stop()
        key = event.value.strip()
        if key:
            self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)
