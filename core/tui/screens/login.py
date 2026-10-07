"""自定义提供方 / 模型注册与注销弹窗（/login，别名 /logout）。"""

from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from core.tui.screens.base import (
    _ArrowNav,
    _InlineConfirm,
    _ListPickerScreen,
    _error_text,
    _field,
    _model_row_text,
    _rebuild_options,
)


def _custom_provider_names(screen: ModalScreen) -> list[str]:
    """自定义提供方名列表（core.client 真源）；懒导入失败（无 openai 环境）时提示并返回空表。"""
    try:
        from core.client import get_provider_list
        return [provider for provider, _configured in get_provider_list(custom_provider=True)]
    except Exception as exc:
        screen.app.notify(f"Cannot load custom providers: {_error_text(exc)}",
                          title="⚠️ Login", severity="error")
        return []


def _positive_int(text: str) -> Optional[int]:
    """解析正整数（上下文长度 / 最大输出）；非法或 <= 0 返回 None。"""
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


class LoginScreen(_ListPickerScreen):
    """注册（注销）菜单弹窗：1 注册提供方 / 2 注册模型 / 3 注销提供方 / 4 注销模型；
    行 = 序号（暗灰）+ 动作文本（注册绿 / 注销红）；关闭子窗后回到本菜单，Esc 逐层关闭。"""

    TITLE = "Custom providers & models"
    HINT = "  ↑/↓ browse    Enter select    Esc close"
    LIST_ID = "login-list"
    PICKER_ID = "login-picker"

    _NUM_STYLE = "#64748b"
    _REGISTER_STYLE = "#4ade80"
    _UNREGISTER_STYLE = "#f87171"

    # (选项 id, 文本, 是否注册项)；顺序即列表顺序
    _OPTIONS = (
        ("reg-provider", "Register custom provider", True),
        ("reg-model", "Register custom model", True),
        ("unreg-provider", "Unregister custom provider", False),
        ("unreg-model", "Unregister custom model", False),
    )

    def _option_row(self, num: int, label: str, register: bool) -> Text:
        style = self._REGISTER_STYLE if register else self._UNREGISTER_STYLE
        return Text.assemble((f"{num}. ", self._NUM_STYLE), (label, style))

    def _reload(self) -> None:
        _rebuild_options(self._list(),
                         [Option(self._option_row(num, label, register), id=oid)
                          for num, (oid, label, register) in enumerate(self._OPTIONS, start=1)])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        screen_cls = _MENU_TARGETS.get(event.option_id or "")
        if screen_cls is not None:
            self.app.push_screen(screen_cls())


class RegisterProviderScreen(_ArrowNav, ModalScreen[None]):
    """提供方注册表单：名称 / Base URL / API Key（掩码），Tab / ↑/↓ 切字段，Enter 经
    login_provider 落盘；名称或 Base URL 为空、重名（含内置）留在窗内提示；Esc 取消。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        picker = Vertical(
            _field(Input(placeholder="Provider name", id="reg-provider-name")),
            _field(Input(placeholder="Base URL (e.g. https://api.example.com/v1)",
                         id="reg-provider-url")),
            _field(Input(placeholder="API Key (optional, masked)", password=True,
                         id="reg-provider-key")),
            Static("  Tab next field    Enter register    Esc cancel", classes="picker-hint"),
            classes="picker",
            id="reg-provider-picker",
        )
        picker.border_title = "Register a custom provider"
        yield picker

    def on_mount(self) -> None:
        self.query_one("#reg-provider-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        name = self.query_one("#reg-provider-name", Input).value.strip()
        base_url = self.query_one("#reg-provider-url", Input).value.strip()
        api_key = self.query_one("#reg-provider-key", Input).value.strip()
        if not name or not base_url:
            self.app.notify("Provider name and Base URL are required", title="⚠️ Login")
            return
        try:
            from core.client import login_provider
            ok = login_provider(name, base_url, api_key)
        except Exception as exc:
            self.app.notify(f"Failed to register {name}: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
            return
        if not ok:  # 已存在（含内置）/ 写盘失败
            self.app.notify(f"{name} already exists (built-in or custom)", title="⚠️ Login")
            return
        self.app.notify(f"Custom provider {name} registered", title="✅ Login")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RegisterModelScreen(_ArrowNav, ModalScreen[None]):
    """模型注册表单：提供方下拉（仅自定义提供方，占位提示“Select a custom provider”）+
    模型名 + 上下文长度 + 最大输出（可留空 = 未知）+ 思考/视觉开关（默认开，同 login_model
    默认值）。↑/↓ 切字段、Enter 打开下拉后 ↑/↓ 选项（见 _ArrowNav）。

    Enter 经 login_model 落盘；未选提供方 / 模型名为空 / 长度非正整数留在窗内提示；Esc 取消。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        picker = Vertical(
            _field(Select([], prompt="Select a custom provider", id="reg-model-provider")),
            _field(Input(placeholder="Model name", id="reg-model-name")),
            _field(Input(placeholder="Context length (e.g. 128000)", type="integer",
                         id="reg-model-context")),
            _field(Input(placeholder="Max output tokens (e.g. 32000, optional)", type="integer",
                         id="reg-model-max-output")),
            Horizontal(Static("Supports thinking mode", classes="switch-label"),
                       Switch(value=True, id="reg-model-thinking"), classes="switch-row"),
            Horizontal(Static("Supports vision", classes="switch-label"),
                       Switch(value=True, id="reg-model-vision"), classes="switch-row"),
            Static("  Tab next field    Enter register    Esc cancel", classes="picker-hint"),
            classes="picker",
            id="reg-model-picker",
        )
        picker.border_title = "Register a custom model"
        yield picker

    def on_mount(self) -> None:
        select = self.query_one("#reg-model-provider", Select)
        providers = _custom_provider_names(self)
        select.set_options([(provider, provider) for provider in providers])
        if not providers:
            self.app.notify("No custom providers — register one via option 1 first", title="ℹ️ Login")
        select.focus()  # 先选提供方

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        provider = self.query_one("#reg-model-provider", Select).selection
        model = self.query_one("#reg-model-name", Input).value.strip()
        context_length = _positive_int(self.query_one("#reg-model-context", Input).value.strip())
        max_output_raw = self.query_one("#reg-model-max-output", Input).value.strip()
        max_output = _positive_int(max_output_raw) if max_output_raw else None
        thinking = self.query_one("#reg-model-thinking", Switch).value
        vision = self.query_one("#reg-model-vision", Switch).value
        if not provider:  # 未选择（或无可选项）时 selection 归一为 None
            self.app.notify("Select a custom provider first", title="⚠️ Login")
            return
        if not model:
            self.app.notify("Model name is required", title="⚠️ Login", severity="error")
            return
        if context_length is None:
            self.app.notify("Context length must be a positive integer",
                            title="⚠️ Login", severity="error")
            return
        if max_output_raw and max_output is None:
            self.app.notify("Max output tokens must be a positive integer (or left empty)",
                            title="⚠️ Login", severity="error")
            return
        try:
            from core.client import login_model
            ok = login_model(provider, model, thinking=thinking, vision=vision,
                             context_length=context_length, max_output=max_output)
        except Exception as exc:
            self.app.notify(f"Failed to register {model}: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
            return
        if not ok:  # provider 不存在 / 写盘失败
            self.app.notify(f"Provider {provider} does not exist", title="⚠️ Login", severity="error")
            return
        self.app.notify(f"Custom model {model} registered for {provider}", title="✅ Login")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class UnregisterProviderScreen(_InlineConfirm, _ListPickerScreen):
    """自定义提供方注销窗：Delete 原地确认后经 logout_provider 注销（连带其自定义模型），
    成功后刷新列表留在窗内（可连续注销）；列表为空时窗内提示。"""

    TITLE = "Unregister a custom provider"
    HINT = "  ↑/↓ browse    Delete unregister    Esc close"
    LIST_ID = "unreg-provider-list"
    PICKER_ID = "unreg-provider-picker"
    CONFIRM = True
    BINDINGS = [("delete", "remove_selected", "Delete")]

    def _reload(self) -> None:
        providers = _custom_provider_names(self)
        _rebuild_options(self._list(), [Option(provider, id=provider) for provider in providers])
        if not providers:
            self.app.notify("No custom providers to unregister", title="ℹ️ Login")

    def action_remove_selected(self) -> None:
        """Delete：原地确认；Enter 才 logout_provider。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        provider = olist.get_option_at_index(olist.highlighted).id
        if provider:
            self._ask_confirm(f'Unregister "{provider}" and its models?',
                              lambda: self._unregister(provider))

    def _unregister(self, provider: str) -> None:
        try:
            from core.client import logout_provider
            ok = logout_provider(provider)
        except Exception as exc:
            self.app.notify(f"Failed to unregister {provider}: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
            return
        if not ok:  # provider 不存在 / 写盘失败
            self.app.notify(f"{provider} does not exist", title="⚠️ Login", severity="error")
            return
        self.app.notify(f"Custom provider {provider} unregistered", title="✅ Login")
        self._reload()  # 留在窗内可连续注销


class UnregisterModelScreen(_InlineConfirm, _ListPickerScreen):
    """自定义模型注销窗：行 = 模型名 + [provider]（同 /model 弹窗）；Delete 原地确认后经
    logout_model 注销，成功后刷新列表留在窗内；列表为空时窗内提示。"""

    TITLE = "Unregister a custom model"
    HINT = "  ↑/↓ browse    Delete unregister    Esc close"
    LIST_ID = "unreg-model-list"
    PICKER_ID = "unreg-model-picker"
    CONFIRM = True
    BINDINGS = [("delete", "remove_selected", "Delete")]

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[str, str]] = []

    def _reload(self) -> None:
        try:
            from core.client import get_model_list
            self._rows = get_model_list(custom_model=True)
        except Exception as exc:
            self._rows = []
            self.app.notify(f"Cannot load custom models: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
        _rebuild_options(self._list(), [Option(_model_row_text(model, provider), id=str(i))
                                        for i, (model, provider) in enumerate(self._rows)])
        if not self._rows:
            self.app.notify("No custom models to unregister", title="ℹ️ Login")

    def action_remove_selected(self) -> None:
        """Delete：原地确认；Enter 才 logout_model(provider, model)。"""
        olist = self._list()
        if olist.highlighted is None or not 0 <= olist.highlighted < len(self._rows):
            return
        model, provider = self._rows[olist.highlighted]
        self._ask_confirm(f'Unregister "{model}" from {provider}?',
                          lambda: self._unregister(provider, model))

    def _unregister(self, provider: str, model: str) -> None:
        try:
            from core.client import logout_model
            ok = logout_model(provider, model)
        except Exception as exc:
            self.app.notify(f"Failed to unregister {model}: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
            return
        if not ok:  # provider / model 不存在，或写盘失败
            self.app.notify(f"{model} from {provider} does not exist", title="⚠️ Login", severity="error")
            return
        self.app.notify(f"Custom model {model} unregistered from {provider}", title="✅ Login")
        self._reload()  # 留在窗内可连续注销


# 菜单项 id → 目标窗（在类定义后建表，避免运行时查全局）
_MENU_TARGETS = {
    "reg-provider": RegisterProviderScreen,
    "reg-model": RegisterModelScreen,
    "unreg-provider": UnregisterProviderScreen,
    "unreg-model": UnregisterModelScreen,
}
