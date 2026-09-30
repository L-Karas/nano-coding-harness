"""自定义提供方 / 模型注册与注销弹窗（/login，别名 /logout）。"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from core.tui.screens.base import _InlineConfirm, _ListPickerScreen, _error_text, _model_row_text, _rebuild_options


def _custom_provider_names(screen: ModalScreen) -> list[str]:
    """自定义提供方名列表（core.client.get_provider_list(custom_provider=True) 真源）；
    懒导入失败（无 openai 环境）时提示并返回空表。"""
    try:
        from core.client import get_provider_list
        return [provider for provider, _configured in get_provider_list(custom_provider=True)]
    except Exception as exc:
        screen.app.notify(f"Cannot load custom providers: {_error_text(exc)}",
                          title="⚠️ Login", severity="error")
        return []


class LoginScreen(_ListPickerScreen):
    """自定义提供方 / 模型 注册（注销）菜单弹窗（/login，别名 /logout）：OptionList 四项
    （1 注册自定义提供方 / 2 注册自定义模型 / 3 注销自定义提供方 / 4 注销自定义模型），
    行 = 序号（暗灰）+ 动作文本（注册绿 / 注销红）。

    选中 1 → RegisterProviderScreen 表单；选中 2 → RegisterModelScreen 表单；
    选中 3 → UnregisterProviderScreen 列表；选中 4 → UnregisterModelScreen 列表；
    关闭弹窗后回到本菜单，Esc 逐层关闭。
    """

    TITLE = "Custom providers & models"
    HINT = "  ↑/↓ browse    Enter select    Esc close"
    LIST_ID = "login-list"
    PICKER_ID = "login-picker"

    _NUM_STYLE = "#64748b"  # 暗灰：序号前缀
    _REGISTER_STYLE = "#4ade80"  # 暗绿：注册项（同完成态）
    _UNREGISTER_STYLE = "#f87171"  # 暗红：注销项（同失败态）

    # (选项 id, 文本, 是否注册项)；顺序即列表顺序
    _OPTIONS = (
        ("reg-provider", "Register custom provider", True),
        ("reg-model", "Register custom model", True),
        ("unreg-provider", "Unregister custom provider", False),
        ("unreg-model", "Unregister custom model", False),
    )

    def _option_row(self, num: int, label: str, register: bool) -> Text:
        """行 = 序号（暗灰）+ 动作文本（注册绿 / 注销红）。"""
        style = self._REGISTER_STYLE if register else self._UNREGISTER_STYLE
        return Text.assemble((f"{num}. ", self._NUM_STYLE), (label, style))

    def _reload(self) -> None:
        _rebuild_options(self._list(),
                         [Option(self._option_row(num, label, register), id=oid)
                          for num, (oid, label, register) in enumerate(self._OPTIONS, start=1)])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """选中项 → 对应注册表单 / 注销列表（弹窗关闭后回到本菜单）。"""
        event.stop()
        targets = {  # 运行时查类（本菜单定义在各表单之前）
            "reg-provider": RegisterProviderScreen,
            "reg-model": RegisterModelScreen,
            "unreg-provider": UnregisterProviderScreen,
            "unreg-model": UnregisterModelScreen,
        }
        screen_cls = targets.get(event.option_id or "")
        if screen_cls is not None:
            self.app.push_screen(screen_cls())


class RegisterProviderScreen(ModalScreen[None]):
    """自定义提供方注册表单（/login → 选项 1）：名称 / Base URL / API Key（掩码）三项输入，
    Tab 切换字段（TextArea 才吞 Tab，Input 走默认 focus_next），Enter 提交经
    core.client.login_provider 落盘注册（写入 .custom_providers.json / .custom_models.json），
    成功 notify + 关窗回到菜单；名称或 Base URL 为空、重名（含内置 provider）留在窗内提示，
    Esc 取消。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        picker = Vertical(
            Input(placeholder="Provider name", id="reg-provider-name"),
            Input(placeholder="Base URL (e.g. https://api.example.com/v1)", id="reg-provider-url"),
            Input(placeholder="API Key (optional, masked)", password=True, id="reg-provider-key"),
            Static("  Tab next field    Enter register    Esc cancel", classes="picker-hint"),
            classes="picker",
            id="reg-provider-picker",
        )
        picker.border_title = "Register a custom provider"
        yield picker

    def on_mount(self) -> None:
        self.query_one("#reg-provider-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter：三项取值校验后 login_provider 落盘（懒导入：无 openai 环境在窗内提示）。"""
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
        if not ok:  # login_provider 对已存在（含内置）provider 返回 False
            self.app.notify(f"{name} already exists (built-in or custom)", title="⚠️ Login")
            return
        self.app.notify(f"Custom provider {name} registered", title="✅ Login")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RegisterModelScreen(ModalScreen[None]):
    """自定义模型注册表单（/login → 选项 2）：提供方下拉（仅自定义提供方，on_mount 经
    core.client.get_provider_list(custom_provider=True) 取数，默认选中首项）+ 模型名输入 +
    思考/视觉两个 Switch 开关（默认开，同 login_model 默认值），Enter 经
    core.client.login_model(provider, model, thinking, vision) 落盘注册（写入 .custom_models.json），
    成功 notify + 关窗回到菜单；无自定义提供方 / 未选提供方 / 模型名为空留在窗内提示，
    Esc 取消。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        picker = Vertical(
            Select([], prompt="Custom provider", id="reg-model-provider"),
            Input(placeholder="Model name", id="reg-model-name"),
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
        """装配提供方下拉（自定义提供方列表）；焦点落在模型名输入，Enter 直接提交。"""
        select = self.query_one("#reg-model-provider", Select)
        providers = _custom_provider_names(self)
        select.set_options([(provider, provider) for provider in providers])
        if providers:
            select.value = providers[0]
        else:
            self.app.notify("No custom providers — register one via option 1 first", title="ℹ️ Login")
        self.query_one("#reg-model-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter：提供方 + 三项字段校验后 login_model 落盘（懒导入：无 openai 环境在窗内提示）。"""
        event.stop()
        provider = self.query_one("#reg-model-provider", Select).selection
        model = self.query_one("#reg-model-name", Input).value.strip()
        thinking = self.query_one("#reg-model-thinking", Switch).value
        vision = self.query_one("#reg-model-vision", Switch).value
        if not provider:  # 无自定义提供方时 value 为 Select.NULL，selection 归一为 None
            self.app.notify("No custom provider selected — register a provider first", title="⚠️ Login")
            return
        if not model:
            self.app.notify("Model name is required", title="⚠️ Login", severity="error")
            return
        try:
            from core.client import login_model
            ok = login_model(provider, model, thinking=thinking, vision=vision)
        except Exception as exc:
            self.app.notify(f"Failed to register {model}: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
            return
        if not ok:  # login_model 对 provider 不存在 / 写盘失败返回 False
            self.app.notify(f"Provider {provider} does not exist", title="⚠️ Login", severity="error")
            return
        self.app.notify(f"Custom model {model} registered for {provider}", title="✅ Login")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class UnregisterProviderScreen(_InlineConfirm, _ListPickerScreen):
    """自定义提供方注销窗口（/login → 选项 3）：on_mount 经
    core.client.get_provider_list(custom_provider=True) 列出全部自定义提供方（含未配置项），
    Delete 在窗内底部红字原地确认（再 Enter）后经 core.client.logout_provider 注销
    （该提供方的自定义模型一并清除），成功后 notify + 刷新列表留在窗内（可连续注销）；列表为空
    （无自定义提供方）时窗内提示，Esc 撤销确认 / 取消。"""

    TITLE = "Unregister a custom provider"
    HINT = "  ↑/↓ browse    Delete unregister    Esc close"
    LIST_ID = "unreg-provider-list"
    PICKER_ID = "unreg-provider-picker"
    CONFIRM = True
    BINDINGS = [("delete", "remove_selected", "Delete")]

    def _reload(self) -> None:
        """装配/刷新自定义提供方列表（注销成功后重取）；列表为空只留提示。"""
        providers = _custom_provider_names(self)
        _rebuild_options(self._list(), [Option(provider, id=provider) for provider in providers])
        if not providers:
            self.app.notify("No custom providers to unregister", title="ℹ️ Login")

    def action_remove_selected(self) -> None:
        """Delete：窗内红字原地确认；再按 Enter 才 logout_provider（连带其自定义模型）。"""
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
        if not ok:  # logout_provider 对不存在的 provider / 写盘失败返回 False
            self.app.notify(f"{provider} does not exist", title="⚠️ Login", severity="error")
            return
        self.app.notify(f"Custom provider {provider} unregistered", title="✅ Login")
        self._reload()  # 留在窗内：列表刷新，可连续注销


class UnregisterModelScreen(_InlineConfirm, _ListPickerScreen):
    """自定义模型注销窗口（/login → 选项 4）：on_mount 经
    core.client.get_model_list(custom_model=True) 列出全部自定义模型（行 = 模型名 + [provider]
    暗灰标签，同 /model 选择弹窗），Delete 在窗内底部红字原地确认（再 Enter）后经
    core.client.logout_model(provider, model) 注销，成功后 notify + 刷新列表留在窗内（可连续注销）；
    列表为空（无自定义模型）时窗内提示，Esc 撤销确认 / 取消。"""

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
        """装配/刷新自定义模型列表（注销成功后重取）；懒导入失败或列表为空只留提示。"""
        try:
            from core.client import get_model_list
            self._rows = get_model_list(custom_model=True)
        except Exception as exc:
            self._rows = []
            self.app.notify(f"Cannot load custom models: {_error_text(exc)}",
                            title="⚠️ Login", severity="error")
        _rebuild_options(self._list(), [Option(_model_row_text(model, tag), id=str(i))
                                        for i, (model, tag) in enumerate(self._rows)])
        if not self._rows:
            self.app.notify("No custom models to unregister", title="ℹ️ Login")

    def action_remove_selected(self) -> None:
        """Delete：窗内红字原地确认；再按 Enter 才 logout_model(provider, model)。"""
        olist = self._list()
        if olist.highlighted is None or not 0 <= olist.highlighted < len(self._rows):
            return
        model, provider_tag = self._rows[olist.highlighted]
        # 行内 Provider 为 "[Provider]"（展示用）；provider 名原样取自后端
        provider = provider_tag.strip("[]")
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
        if not ok:  # logout_model 对不存在的 provider / model / 写盘失败返回 False
            self.app.notify(f"{model} from {provider} does not exist", title="⚠️ Login", severity="error")
            return
        self.app.notify(f"Custom model {model} unregistered from {provider}", title="✅ Login")
        self._reload()  # 留在窗内：列表刷新，可连续注销
