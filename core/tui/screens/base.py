"""弹窗基础设施：原地确认 / OptionList 弹窗骨架 / 会话行 / 通用行渲染。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, OptionList, Select, Static, Tabs, TextArea
from textual.widgets.option_list import Option

from core.tui.theme import (
    ACTIVE_GLYPH,
    ACTIVE_STYLE,
    INACTIVE_GLYPH,
    INACTIVE_STYLE,
    SECONDARY_TEXT,
)

_MODEL_NAME_STYLE = "#f8fafc"
_MODEL_PROVIDER_STYLE = SECONDARY_TEXT
_MODEL_CURRENT_STYLE = "#34d399"  # 同 _SessionRow 的 current 标记色
_ENTRY_DESC_STYLE = SECONDARY_TEXT
_ENTRY_TAG_STYLE = "#a5adce"  # [tag] 段（/skills 的来源标记 global/user/project）


def _entry_row(
    name: str,
    desc: str = "",
    tag: str = "",
    *,
    enabled: bool = True,
    status: str = "",
    head_style: Optional[str] = None,
) -> Text:
    """构造「● 名称」行。

    Args:
        name: 名称。
        desc: 描述；strip 后自第二行起显示。
        tag: 源标记；非空时以标记色追加 [tag]。
        enabled: 是否启用；False 时默认改用红色「○ 名称」。
        status: 状态文本；非空时在 tag 前追加 `(status)`。
        head_style: 名称段样式覆盖。

    Returns:
        名称行 Text；内部换行 / 缩进原样保留。
    """
    row = Text()
    glyph = ACTIVE_GLYPH if enabled else INACTIVE_GLYPH
    row_style = head_style or (ACTIVE_STYLE if enabled else INACTIVE_STYLE)
    row.append(f"{glyph} {name}", style=row_style)
    if status:
        row.append(f" ({status})", style=row_style)
    if tag:
        row.append(f" [{tag}]", style=_ENTRY_TAG_STYLE)
    desc = (desc or "").strip()
    if desc:
        row.append("\n" + desc, style=_ENTRY_DESC_STYLE)
    return row


def _model_row_text(model: str, provider: str, current: bool = False) -> Text:
    """构造模型行。

    Args:
        model: 模型名。
        provider: 提供方。
        current: 是否为当前模型。

    Returns:
        模型名（亮色）+ [Provider]（次级灰）+ 可选 (current model) 标记（暗绿）。
    """
    row = Text.assemble((model, _MODEL_NAME_STYLE),
                        (f" [{provider}]", _MODEL_PROVIDER_STYLE))
    if current:
        row.append("  (current model)", style=_MODEL_CURRENT_STYLE)
    return row


def _error_text(exc: Exception) -> str:
    """格式化异常文本。

    Args:
        exc: 异常。

    Returns:
        带类型名前缀的文本；str 为空的异常也不至于只显示空串。
    """
    return f"{type(exc).__name__}: {exc}"


def _field(widget: Widget) -> Horizontal:
    """构造表单字段行。

    Args:
        widget: 字段控件。

    Returns:
        「❯」标记 + 控件的 Horizontal；行样式同搜索栏（app.css .field-row / .field-mark）。
    """
    return Horizontal(Static("❯", classes="field-mark"), widget, classes="field-row")


class _ArrowNav:
    """表单窗键盘导航 mixin：↑/↓ / Tab 切换字段；下拉浮层展开时（Enter 打开）不拦截 ↑/↓，
    让浮层用 ↑/↓ 选选项、Enter 确认——即未打开下拉时 ↑/↓ 不会展开下拉。文本区聚焦时
    ↑/↓ 留给 TextArea 移光标（表单里的 args / headers 多行输入区）。"""

    def on_key(self, event: events.Key) -> None:
        """用 ↑/↓ 切换表单字段。

        Args:
            event: 按键事件。

        Tab 由 Textual 默认焦点链处理；下拉浮层展开或文本区聚焦时不拦截 ↑/↓。
        """
        if event.key not in ("up", "down"):
            return
        if isinstance(self.focused, TextArea):
            return  # 文本区内 ↑/↓ 归光标，交给 TextArea 绑定
        if any(select.expanded for select in self.query(Select)):
            return  # 浮层已展开：交给 SelectOverlay 的 OptionList
        event.prevent_default()  # 不拦则 Select 的 ↑/↓ 绑定会展开浮层
        event.stop()
        if event.key == "up":
            self.focus_previous()
        else:
            self.focus_next()


def _rebuild_options(olist: OptionList, options: list[Option]) -> None:
    """整列替换选项并保留原高亮位置。

    Args:
        olist: 目标列表。
        options: 新选项列表。

    set_options 会清空高亮，这里按旧下标恢复。
    """
    old_index = olist.highlighted
    olist.set_options(options)
    if options:
        olist.highlighted = min(old_index or 0, len(options) - 1)


class _InlineConfirm:
    """破坏性操作的原地二次确认（不弹窗）：底部提示行改红色确认文案，Enter 继续 / Esc 撤销
    （弹窗保持打开）；确认期间高亮变化即自动撤销，避免确认到与提示不符的对象。

    子类须提供 id="confirm-hint" 的 Static；Enter 经本类 on_option_list_option_selected 分流：
    有待确认先确认，否则交子类 _selected 选择；action_cancel 先经 _cancel_confirm 分流。"""

    _CONFIRM_STYLE = "#f87171"
    _pending: Optional[Callable[[], None]] = None
    _default_hint = ""

    def _hint(self) -> Static:
        """取确认提示行。

        Returns:
            #confirm-hint Static。
        """
        return self.query_one("#confirm-hint", Static)

    def _ask_confirm(self, message: str, action: Callable[[], None]) -> None:
        """进入原地二次确认。

        Args:
            message: 确认文案。
            action: Enter 后执行的动作。
        """
        if self._pending is None:
            self._default_hint = str(self._hint().render())
        self._pending = action
        self._hint().update(Text.assemble((f"  ⚠ {message}    Enter continue    Esc cancel",
                                           self._CONFIRM_STYLE)))

    def _cancel_confirm(self) -> bool:
        """撤销待确认并恢复原提示行。

        Returns:
            确实撤销了待确认时为 True。
        """
        if self._pending is None:
            return False
        self._pending = None
        self._hint().update(self._default_hint)
        return True

    def _run_confirm(self) -> bool:
        """Enter：先复位待确认（恢复提示行）再执行动作（动作可能刷新列表 / 关窗）。

        Returns:
            有待确认并已执行时为 True。
        """
        if self._pending is None:
            return False
        action = self._pending
        self._cancel_confirm()
        action()
        return True

    def action_cancel(self) -> None:
        """Esc：先撤销待确认，否则关闭弹窗。"""
        if not self._cancel_confirm():
            self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """处理列表 Enter。

        Args:
            event: 选项选中事件。

        先确认已武装的原地操作（有则不再选择），否则走子类 _selected。
        """
        event.stop()
        if not self._run_confirm():
            self._selected(event.option_id)

    def _selected(self, option_id: Optional[str]) -> None:
        """执行无待确认时的选择动作。

        Args:
            option_id: 选中项 id。

        纯确认型屏（注销）保持默认空实现。
        """

    def on_input_changed(self, event: Input.Changed) -> None:
        """输入变化时撤销进行中的原地确认。

        Args:
            event: 输入变化事件。

        确认屏的输入框即搜索栏；改选后旧确认目标已失效。
        """
        self._cancel_confirm()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """高亮变化时自动撤销确认。

        Args:
            event: 高亮变化事件。

        确认目标已不是当前行。
        """
        self._cancel_confirm()


class _InstantTabs(Tabs):
    """滑块无滑行延迟的 Tabs（强制关闭切档位移画；滑块色仍随 TabActivated 即时同步）。"""

    def _highlight_active(self, animate: bool = True) -> None:
        """高亮当前 Tab，强制关闭滑块位移动画。

        Args:
            animate: 基类动画开关；此处始终传 False。
        """
        super()._highlight_active(False)


class _SessionRow:
    """会话列表项：标题靠左、(current session) 随后、时间戳顶到行最右。

    OptionList 无法表达右对齐列，故按渲染时给到的实际行宽自排版；空间不足时标题按剩余
    宽度截断（…），(current) 标记仅在剩余空间足够时追加，时间戳保持完整。"""

    _TITLE_STYLE = "#f8fafc"
    _MARK_STYLE = "#34d399"
    _TS_STYLE = SECONDARY_TEXT
    _MARK_TEXT = "  (current session)"

    def __init__(
        self,
        title: str,
        current: bool,
        timestamp: str,
    ) -> None:
        """初始化会话行。

        Args:
            title: 会话标题。
            current: 是否为当前会话。
            timestamp: 时间戳文本。
        """
        self._title = title
        self._current = current
        self._timestamp = timestamp

    def __rich_measure__(self, options: ConsoleOptions) -> Measurement:
        """计算自然宽度。

        Args:
            options: Rich 控制台选项。

        Returns:
            完整未截断行的宽度；超宽行由列表裁剪。
        """
        # 自然宽度 = 完整未截断行（超宽行由列表裁剪）
        mark = self._MARK_TEXT if self._current else ""
        return Measurement(1, cell_len(f"{self._title}{mark}  {self._timestamp}"))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """按实际行宽排版并渲染会话行。

        Args:
            console: Rich 控制台。
            options: 渲染选项。

        Yields:
            单行 Text；时间戳右对齐，标题按剩余宽度截断，标记仅在剩余空间足够时追加。
        """
        ts = Text(f"  {self._timestamp}", style=self._TS_STYLE)
        free = max(0, options.max_width or 0) - ts.cell_len  # 标题先截断，标记仅在剩余空间足够时追加
        title = Text(self._title, style=self._TITLE_STYLE)
        if free < 1:  # rich 的 truncate(0) 不清空文本（ellipsis 分支按 max_width-1 取宽）
            title = Text("")
        elif title.cell_len > free:
            title.truncate(free, overflow="ellipsis")
        row = Text()
        row.append_text(title)
        free -= title.cell_len
        if self._current and cell_len(self._MARK_TEXT) <= free:
            row.append_text(Text(self._MARK_TEXT, style=self._MARK_STYLE))
            free -= cell_len(self._MARK_TEXT)
        row.append(" " * free)  # 空白填隙：时间戳顶到行最右
        row.append_text(ts)
        row.no_wrap = True  # 极端窄行不折行，超宽部分交给 OptionList 裁剪
        yield row


class _ListPickerScreen(ModalScreen[Any]):
    """OptionList 弹窗骨架：标题 + 列表 + 提示行。

    TITLE / HINT / LIST_ID 由子类给出；CONFIRM=True 时提示行 id 为 confirm-hint（_InlineConfirm
    锚点）；SEARCH_PLACEHOLDER 非空时顶部加搜索栏（默认聚焦；过滤由子类 _reload 读 self._query）；
    on_mount 自动 _reload 并聚焦搜索栏 / 列表；Esc 关闭。不包 CenterMiddle 等全屏容器，
    避免盖掉底层主界面合成（原因见 app.css）。"""

    TITLE = ""
    HINT = ""
    LIST_ID = ""
    PICKER_ID: Optional[str] = None
    CONFIRM = False
    SEARCH_PLACEHOLDER = ""
    SEARCH_ID = "search-input"

    BINDINGS = [("escape", "cancel", "Cancel")]

    _query = ""  # casefold 后的搜索词；无搜索栏的屏恒空（子串匹配全过）

    def _match(self, name: str) -> bool:
        """判断名称是否匹配当前搜索词。

        Args:
            name: 待匹配名称。

        Returns:
            当前搜索词是否为 name 的子串；未输入 / 无搜索栏时恒 True。
        """
        return self._query in name.casefold()

    def compose(self) -> ComposeResult:
        """组装标题、可选搜索栏、列表与提示行。

        Yields:
            弹窗内容部件。
        """
        widgets = []
        if self.SEARCH_PLACEHOLDER:
            widgets.append(Horizontal(
                Static("❯", id="search-prompt"),
                Input(placeholder=self.SEARCH_PLACEHOLDER, id=self.SEARCH_ID),
                id="search-row",
            ))
        widgets.append(OptionList(id=self.LIST_ID))
        widgets.append(Static(self.HINT, id="confirm-hint" if self.CONFIRM else None,
                              classes="picker-hint"))
        picker = Vertical(*widgets, classes="picker", id=self.PICKER_ID)
        picker.border_title = self.TITLE  # 标题嵌在上边框左端（样式见 app.css .picker）
        yield picker

    def on_mount(self) -> None:
        """挂载后重建列表并聚焦搜索栏或列表。"""
        self._reload()
        (self._search() if self.SEARCH_PLACEHOLDER else self._list()).focus()

    def _list(self) -> OptionList:
        """取弹窗列表。

        Returns:
            #LIST_ID OptionList。
        """
        return self.query_one(f"#{self.LIST_ID}", OptionList)

    def _search(self) -> Input:
        """取搜索输入框。

        Returns:
            #SEARCH_ID Input。
        """
        return self.query_one(f"#{self.SEARCH_ID}", Input)

    def _reload(self) -> None:
        """子类按当前数据源重建选项（搜索屏按名称子串过滤 self._query）。"""

    def on_input_changed(self, event: Input.Changed) -> None:
        """处理搜索栏输入。

        Args:
            event: 输入变化事件。

        记录 casefold 查询词并重建列表（撤销确认由 _InlineConfirm 自理）。
        """
        if event.input.id != self.SEARCH_ID:
            return
        event.stop()
        self._query = event.value.strip().casefold()
        self._reload()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理搜索栏 Enter。

        Args:
            event: 提交事件。

        选中当前高亮行（走列表原有的选择 / 原地确认逻辑）。
        """
        if event.input.id != self.SEARCH_ID:
            return
        event.stop()
        self._list().action_select()

    def on_key(self, event: events.Key) -> None:
        """处理搜索栏聚焦时的方向键。

        Args:
            event: 按键事件。

        ↑/↓ 仍移动列表高亮（输入框自身不消费方向键）；Tab 切焦点不受影响。
        """
        if (self.SEARCH_PLACEHOLDER and event.key in ("up", "down")
                and self._search().has_focus):
            event.stop()
            if event.key == "up":
                self._list().action_cursor_up()
            else:
                self._list().action_cursor_down()

    def action_cancel(self) -> None:
        """Esc：关闭弹窗并返回 None。"""
        self.dismiss(None)


@dataclass(frozen=True)
class _NamedItem:
    """命名列表行：名称 + 描述 + 可选标签（/skills 传来源，/mcp 工具列表留空）。"""

    name: str
    description: str = ""
    tag: str = ""


class _NamedListScreen(_ListPickerScreen):
    """「● 名称 + [tag] + 描述」列表弹窗：Enter/点击关窗回传名称（/mcp 工具列表使用；
    /skills 覆写选择结果，以区分立即调用与带补充消息）。"""

    def __init__(self, items: list[_NamedItem]) -> None:
        """初始化命名列表弹窗。

        Args:
            items: 命名条目列表。
        """
        super().__init__()
        self._items = items

    def _reload(self) -> None:
        """按当前搜索词重建命名列表。"""
        _rebuild_options(self._list(), [Option(_entry_row(item.name, item.description, item.tag), id=item.name)
                                        for item in self._items
                                        if self._match(item.name)])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """选中条目后关闭弹窗并回传名称。

        Args:
            event: 选项选中事件。
        """
        event.stop()
        self.dismiss(event.option_id)
