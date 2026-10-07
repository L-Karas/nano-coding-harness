"""设置弹窗（/settings）：编辑 AgentConfig 参数；Enter 两段式（先编辑，再保存），Esc 关闭。

表单格式照注册弹窗：.field-row（左侧参数名 + 无边框控件）+ 开关行 + 提示行。
模型字段为下拉框（选项来自 core.client.get_model_list，值为 'provider:model'）；
打开时按当前配置预填。交互约定（见 SettingsScreen）：
- 浏览态字段锁定：打字 / 空格不生效，↑/↓ 或 Tab 切字段；
- Enter 进入编辑态（输入框解锁全选、下拉展开）；编辑后再次 Enter 校验全部字段并写盘，成功后留在窗内；
- 下拉在浮层里确认选项的那个 Enter 即第二次 Enter（确认 + 落盘），无需再按一次；
- 开关例外：Enter 直接切换并写盘；焦点移开即退出编辑态（未保存输入回退到编辑前值），
  下个字段第一次 Enter 重新进入编辑态。
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Select, Static, Switch

from core.config import AgentConfig, AgentConfigManager
from core.tui.screens.base import _ArrowNav, _error_text, _model_row_text
from core.tui.screens.model import _EFFORT_LEVELS

# 模型下拉字段（值为 'provider:model'）与其余字段名（即左侧标签）；分组顺序即表单顺序
_MODEL_FIELDS = ("default_model", "default_sub_model", "default_fallback_model")
_THINKING_FIELDS = ("default_thinking_level", "default_sub_model_thinking_level")
# 数字字段 → 解析类型（int/float）：顺序即表单顺序，控件 type= 与保存转换都由它决定
_NUMBER_FIELDS: dict[str, type] = {
    "default_max_tokens": int,
    "escalated_max_tokens": int,
    "summary_max_tokens": int,
    "compact_threshold": float,
    "reserve_threshold": float,
}
_BOOL_FIELDS = (
    ("auto_compact", "Auto compact context"),
    ("enable_experimental_tools", "Enable experimental tools"),
    ("enable_defer_tools", "Enable deferred tool loading"),
    ("enable_defer_skills", "Enable deferred skill loading"),
)

_FIELD_ID = "set-{}"  # 控件 id：字段名（下划线原样）→ #set-default_max_tokens


def _model_options(rows: list[tuple[str, str]]) -> list[tuple[Text, tuple[str, str]]]:
    """模型下拉选项 (label, value)：label = 'model [provider]'（带提供方信息，样式同 /model
    列表行：模型亮 / [provider] 暗），value = (provider, model)，保存时拼回 'provider:model'。
    不用纯字符串 label：'[provider]' 会被 Rich markup 当标签吞掉，须用 Text 原样渲染。
    未选择态由 Select 自身的 prompt 占位行充当（allow_blank）。"""
    return [(_model_row_text(model, provider), (provider, model)) for model, provider in rows]


def _labeled_field(name: str, widget: Widget) -> Horizontal:
    """字段行：左侧参数名（定宽对齐，app.css .field-label）+ 右侧控件；无下划线。"""
    return Horizontal(Static(name, classes="field-label"), widget, classes="field-row")


class _LockedInput(Input):
    """默认锁定的输入框：Enter（SettingsScreen 统一处理）前忽略打字 / 粘贴与自身编辑 action。

    锁定时 check_action 返回 False 只禁掉本控件绑定，按键事件照常冒泡，
    ↑/↓ 导航与 SettingsScreen 的 Enter 处理不受影响。
    select_on_focus=False：聚焦只预览（否则聚焦即全选，锁定态与编辑态长得一样）；
    两种状态靠 -editing class 区分（app.css：锁定值暗且无光标，编辑态整行高亮 + 光标）。"""

    locked: bool = True

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.set_class(not locked, "-editing")
        if locked:
            self.selection = (0, 0)  # 清全选高亮：锁定态不应看起来可编辑

    # Textual 会把 _on_key 沿 MRO 逐类派发（子类返回也拦不住 Input._on_key），
    # prevent_default 才会中断后续类的处理器，从而挡住字符插入。
    def _on_key(self, event: events.Key) -> None:
        if self.locked:
            event.prevent_default()

    def _on_paste(self, event: events.Paste) -> None:
        if self.locked:
            event.prevent_default()

    def check_action(self, action: str, parameters: tuple[Any, ...]) -> bool | None:
        return False if self.locked else super().check_action(action, parameters)


class SettingsScreen(_ArrowNav, ModalScreen[None]):
    """AgentConfig 设置表单：↑/↓ 切字段、下拉展开后 ↑/↓ 选项（见 _ArrowNav）。

    两段式 Enter：浏览态字段只读，Enter 进入编辑态（输入框解锁并全选、下拉展开），
    再次 Enter 校验全部字段并落盘（不关窗）；下拉在浮层里确认选项的 Enter 即第二次；
    开关例外，Enter 直接切换并落盘。
    焦点移到别的字段即退出编辑态，未保存的输入回退到编辑前值（下次 Enter 重新进入编辑态）。
    值非法 / 写盘失败 notify 留在窗内；Esc 关窗（未保存的改动丢弃）。
    未在表单中的参数（如 compact_type）保存时原样保留。"""

    BINDINGS = [("escape", "cancel", "Cancel")]

    _editing: Widget | None = None  # 当前处于编辑态的字段（None = 浏览态）
    _pre_edit: tuple[_LockedInput, str] | None = None  # 编辑前值：未保存就切走焦点时回退

    def compose(self) -> ComposeResult:
        # 模型下拉以 prompt 作占位（未选择态即空值，保存为空串），on_mount 再 set_options
        widgets = [_labeled_field(name, Select([], prompt="Select a model",
                                               id=_FIELD_ID.format(name)))
                   for name in _MODEL_FIELDS]
        widgets += [_labeled_field(name, Select([(level, level) for level in _EFFORT_LEVELS],
                                                allow_blank=False, id=_FIELD_ID.format(name)))
                    for name in _THINKING_FIELDS]
        # 数字字段留空 = 回退默认值，占位即该默认值；整数 / 小数控件由解析类型决定
        widgets += [_labeled_field(name, _LockedInput(placeholder=str(AgentConfig.model_fields[name].default),
                                                      select_on_focus=False,
                                                      type="integer" if cast is int else "number",
                                                      id=_FIELD_ID.format(name)))
                    for name, cast in _NUMBER_FIELDS.items()]
        widgets += [Horizontal(Static(label, classes="switch-label"),
                               Switch(value=False, id=_FIELD_ID.format(name)),
                               classes="switch-row")
                    for name, label in _BOOL_FIELDS]
        widgets.append(Static("  Tab next field    Enter edit / save    Esc close",
                              classes="picker-hint"))
        picker = Vertical(*widgets, classes="picker", id="settings-picker")
        picker.border_title = "Settings"
        yield picker

    def on_mount(self) -> None:
        """按当前配置预填；配置读不出（损坏文件）时直接抛，由调用方兜底。"""
        self._config = AgentConfigManager.load_config().config
        self._fill_model_selects()
        for name in _THINKING_FIELDS:
            self.query_one(f"#{_FIELD_ID.format(name)}", Select).value = getattr(self._config, name)
        for name in _NUMBER_FIELDS:
            self.query_one(f"#{_FIELD_ID.format(name)}", Input).value = str(getattr(self._config, name))
        for name, _ in _BOOL_FIELDS:
            self.query_one(f"#{_FIELD_ID.format(name)}", Switch).value = bool(getattr(self._config, name))
        self.query_one(f"#{_FIELD_ID.format(_MODEL_FIELDS[0])}", Select).focus()

    def _fill_model_selects(self) -> None:
        """模型下拉选项取自 get_model_list；取数失败提示并留空（不影响其他字段）。
        预填：'provider:model' 精确匹配；裸模型名（旧配置）按名匹配首个 provider，保存时
        迁移为 'provider:model'；不在模型表的值原样附加可选，保存不丢。"""
        try:
            from core.client import get_model_list
            options = _model_options(get_model_list())
        except Exception as exc:
            options = []
            self.app.notify(f"Cannot load model list: {_error_text(exc)}", title="⚠️ Settings")
        by_model: dict[str, tuple[str, str]] = {}  # 裸模型名 → 首个 (provider, model)（旧值迁移用）
        by_key: dict[str, tuple[str, str]] = {}
        for _label, pair in options:
            by_model.setdefault(pair[1], pair)
            by_key[f"{pair[0]}:{pair[1]}"] = pair
        for name in _MODEL_FIELDS:
            select = self.query_one(f"#{_FIELD_ID.format(name)}", Select)
            current = str(getattr(self._config, name))
            selected = by_key.get(current) or by_model.get(current)
            if selected is None and current:  # 不在模型表：原值原样可选，保存不丢
                options_for_field = options + [(Text(current), ("", current))]
                selected = ("", current)
            else:
                options_for_field = options
            select.set_options(options_for_field)
            if current:
                select.value = selected
            else:
                select.clear()  # 未设置：占位显示 prompt

    def _values(self) -> dict[str, Any]:
        """当前配置打底 + 表单值覆盖：未在表单中的参数原样保留。
        数字字段留空 = 回退 AgentConfig 默认值（不提供“未设置”语义）。"""
        values: dict[str, Any] = self._config.model_dump()
        for name in _MODEL_FIELDS:
            select = self.query_one(f"#{_FIELD_ID.format(name)}", Select)
            # 存 'provider:model'；不在模型表的原值（provider 段为空）原样存
            if select.is_blank():
                values[name] = ""
            else:
                provider, model = select.value
                values[name] = f"{provider}:{model}" if provider else model
        for name in _THINKING_FIELDS:
            values[name] = self.query_one(f"#{_FIELD_ID.format(name)}", Select).value
        for name, cast in _NUMBER_FIELDS.items():
            text = self.query_one(f"#{_FIELD_ID.format(name)}", Input).value.strip()
            values[name] = cast(text) if text else AgentConfig.model_fields[name].default
        values.update({name: self.query_one(f"#{_FIELD_ID.format(name)}", Switch).value
                       for name, _ in _BOOL_FIELDS})
        return values

    # ---------- 两段式 Enter：编辑 → 保存 ----------

    def on_key(self, event: events.Key) -> None:
        if event.key == "space":
            # 空格在本表单不承担功能：开关只认 Enter（避免"切了不落盘"），
            # 也不让它冒泡到 App 的空格绑定（toggle_clarify）
            event.prevent_default()
            event.stop()
            return
        if event.key != "enter":
            super().on_key(event)  # ↑/↓ 导航（_ArrowNav）
            return
        if any(select.expanded for select in self.query(Select)):
            return  # 下拉浮层展开：Enter 交给浮层选选项（选中经 on_select_changed 落盘）
        event.prevent_default()
        event.stop()
        self._handle_enter(self.focused)

    def on_select_changed(self, event: Select.Changed) -> None:
        """下拉浮层里 Enter 确认选项后即落盘：确认键就是第二次 Enter。"""
        if self._editing is event.select:
            self._save()

    def set_focus(self, widget: Widget | None, scroll_visible: bool = True,
                  from_app_focus: bool = False) -> None:
        """焦点移出编辑字段即退出编辑态（下拉浮层内部焦点切换不算移出）。"""
        if (self._editing is not None and widget is not None
                and not self._inside_editing(widget)):
            self._deactivate()
        super().set_focus(widget, scroll_visible=scroll_visible, from_app_focus=from_app_focus)

    def _handle_enter(self, focused: Widget | None) -> None:
        """Enter：开关直接切换 + 保存；其余字段第一次进入编辑态、编辑态中再次按下保存。"""
        if focused is None:
            return
        if isinstance(focused, Switch):
            focused.toggle()
            self._save()
        elif focused is self._editing:
            self._save()
        else:
            self._activate(focused)

    def _activate(self, widget: Widget) -> None:
        """进入编辑态：输入框解锁并全选当前值；下拉展开选项。"""
        self._deactivate()
        if isinstance(widget, _LockedInput):
            self._pre_edit = (widget, widget.value)  # 未保存就切走焦点时回退到这里
            widget.set_locked(False)
            widget.selection = (0, len(widget.value))  # 全选：直接输入即替换
        elif isinstance(widget, Select):
            self._editing = widget  # 先记状态：展开会把焦点交给浮层，set_focus 据此放行
            widget.action_show_overlay()
            return
        self._editing = widget

    def _inside_editing(self, widget: Widget) -> bool:
        """widget 是否属于当前编辑字段（下拉浮层是 Select 的子节点）。"""
        return widget is self._editing or self._editing in widget.ancestors_with_self

    def _deactivate(self, restore: bool = True) -> None:
        """退出编辑态：锁定所有输入框（开关无编辑态，不需复位）。
        restore=True（焦点移开等放弃场景）时把未保存的输入回退到编辑前值；保存成功传 False。"""
        if restore and self._pre_edit is not None:
            widget, old_value = self._pre_edit
            widget.value = old_value
        self._pre_edit = None
        for widget in self.query(_LockedInput):
            widget.set_locked(True)
        self._editing = None

    def _save(self) -> None:
        """校验全部字段并落盘；成功后退出编辑态、留在窗内（Esc 才关窗）。"""
        try:
            config = AgentConfig(**self._values())
            AgentConfigManager(config).save_config()
        except Exception as exc:
            self.app.notify(f"Settings not saved: {_error_text(exc)}",
                            title="⚠️ Settings", severity="error")
            return
        self._config = config
        self._deactivate(restore=False)  # 已落盘：保留刚输入的值
        self.app.notify("Settings saved", title="✅ Settings")

    def action_cancel(self) -> None:
        self.dismiss(None)
