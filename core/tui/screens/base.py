"""弹窗基础设施：原地确认 / OptionList 弹窗骨架 / 会话行 / 通用小工具（拆自 widgets.py）。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, Tabs
from textual.widgets.option_list import Option

_MODEL_NAME_STYLE = "#f8fafc"
_MODEL_PROVIDER_STYLE = "#64748b"  # 暗灰：所属提供商
_MODEL_CURRENT_STYLE = "#34d399"  # 当前模型标记（同 _SessionRow 的 current session 标记色）
_ENTRY_NAME_STYLE = "bold #4ade80"  # 列表行的「● 名称」段（/skills、/mcp 工具列表共用）
_ENTRY_DESC_STYLE = "#64748b"  # 列表行的描述段


def _entry_row(name: str, desc: str = "") -> Text:
    """「● 名称」列表行；描述 strip 头尾空白后整段自第二行起（内部换行 / 缩进原样保留）。"""
    row = Text()
    row.append("● " + name, style=_ENTRY_NAME_STYLE)
    desc = (desc or "").strip()
    if desc:
        row.append("\n" + desc, style=_ENTRY_DESC_STYLE)
    return row


def _model_row_text(model: str, provider_tag: str, current: bool = False) -> Text:
    """模型行 = 模型名（亮色）+ [Provider Name]（暗灰）+ 可选 (current model) 标记（暗绿）"""
    row = Text.assemble((model, _MODEL_NAME_STYLE),
                        (" " + provider_tag, _MODEL_PROVIDER_STYLE))
    if current:
        row.append("  (current model)", style=_MODEL_CURRENT_STYLE)
    return row


def _error_text(exc: Exception) -> str:
    """异常文本带类型名前缀：str 为空的异常（如 KeyError('')）不至于只显示空串。"""
    return f"{type(exc).__name__}: {exc}"


def _rebuild_options(olist: OptionList, options: list[Option]) -> None:
    """整列替换并保留原高亮位置（set_options 会把高亮清空）。"""
    old_index = olist.highlighted
    olist.set_options(options)
    if options:
        olist.highlighted = min(old_index or 0, len(options) - 1)


class _InlineConfirm:
    """破坏性操作的原地二次确认（不弹确认窗）：把弹窗最下方提示行改成红色确认文案，
    Enter 继续、Esc 撤销（撤销后弹窗保持打开）；确认期间高亮行一旦变化即自动撤销，
    避免确认到与提示不符的对象。

    子类须提供一个 id="confirm-hint" 的提示 Static，在销毁键回调里调
    _ask_confirm(message, action) 武装；Enter 默认经本类的 on_option_list_option_selected
    确认（带选择行为的子类覆写），action_cancel 先经 _cancel_confirm 分流。"""

    _CONFIRM_STYLE = "#f87171"  # 红色：同 LoginScreen 注销项
    _pending: Optional[Callable[[], None]] = None
    _default_hint = ""

    def _hint(self) -> Static:
        return self.query_one("#confirm-hint", Static)

    def _ask_confirm(self, message: str, action: Callable[[], None]) -> None:
        if self._pending is None:
            self._default_hint = str(self._hint().render())  # 记住原提示文案，撤销时恢复
        self._pending = action
        self._hint().update(Text.assemble((f"  ⚠ {message}    Enter continue    Esc cancel",
                                           self._CONFIRM_STYLE)))

    def _cancel_confirm(self) -> bool:
        """Esc / 高亮变化：撤销待确认并恢复原提示行；返回是否确实撤销了。"""
        if self._pending is None:
            return False
        self._pending = None
        self._hint().update(self._default_hint)
        return True

    def _run_confirm(self) -> bool:
        """Enter：执行待确认操作；先复位再执行（操作可能刷新列表 / 关窗）。"""
        if self._pending is None:
            return False
        action = self._pending
        self._cancel_confirm()
        action()
        return True

    def action_cancel(self) -> None:
        """Esc：确认中先撤销确认（弹窗保持打开），否则关闭弹窗。"""
        if not self._cancel_confirm():
            self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter：确认已武装的原地操作；无待确认时不做动作（带选择行为的子类覆写本方法）。"""
        event.stop()
        self._run_confirm()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """↑/↓ 或列表重建改高亮：确认目标已不是当前行，自动撤销。"""
        self._cancel_confirm()


class _InstantTabs(Tabs):
    """滑块无滑行延迟的 Tabs：原生切档时对 Underline 高亮段做 0.3s 位移动画，文字高亮
    即时切换、滑块滞后半拍观感拖沓。这里强制动画关闭（动画只影响位置过渡，滑块色仍随
    TabActivated 即时同步）。"""

    def _highlight_active(self, animate: bool = True) -> None:
        super()._highlight_active(False)


class _SessionRow:
    """会话列表项：标题靠左、(current session) 标记随后、时间戳顶到行最右。

    OptionList 把每行 prompt 按行宽渲染后再尾部补白（默认左对齐），无法表达右对齐列；
    因此在渲染时按 OptionList 给到的实际行宽自排版，时间戳恒在行最右。宽度随终端布局
    变化，每次渲染按当时行宽重排，无需预知终端宽度；空间不足时先舍 (current) 标记、
    再截断标题（…），时间戳保持完整。
    """

    _TITLE_STYLE = "#f8fafc"
    _MARK_STYLE = "#34d399"
    _TS_STYLE = "#94a3b8"
    _MARK_TEXT = "  (current session)"

    def __init__(self, title: str, current: bool, timestamp: str) -> None:
        self._title = title
        self._current = current
        self._timestamp = timestamp

    def __rich_measure__(self, options: ConsoleOptions) -> Measurement:
        # 与普通文本一致：自然宽度 = 完整未截断行（超宽行由列表裁剪）
        mark = self._MARK_TEXT if self._current else ""
        return Measurement(1, cell_len(f"{self._title}{mark}  {self._timestamp}"))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        ts = Text(f"  {self._timestamp}", style=self._TS_STYLE)
        free = max(0, options.max_width or 0) - ts.cell_len  # 空间不足时先舍标题、再舍标记
        title = Text(self._title, style=self._TITLE_STYLE)
        if free < 1:  # rich 的 truncate(0) 不清空文本（ellipsis 分支按 max_width-1 取宽），故单独处理
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

    TITLE / HINT / LIST_ID 由子类以类属性给出；PICKER_ID 供样式选择（无则自动生成）；
    CONFIRM=True 时提示行 id 为 confirm-hint（_InlineConfirm 原地确认的锚点）。
    on_mount 自动 _reload 并聚焦列表；Esc 关闭（_InlineConfirm 子类会先撤销确认）。
    不包 CenterMiddle 等全屏容器：会盖掉底层主界面合成（原因见 app.css ModalScreen 注释）。
    """

    TITLE = ""
    HINT = ""
    LIST_ID = ""
    PICKER_ID: Optional[str] = None
    CONFIRM = False

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.TITLE, classes="picker-title"),
            OptionList(id=self.LIST_ID),
            Static(self.HINT, id="confirm-hint" if self.CONFIRM else None, classes="picker-hint"),
            classes="picker",
            id=self.PICKER_ID,
        )

    def on_mount(self) -> None:
        self._reload()
        self._list().focus()

    def _list(self) -> OptionList:
        return self.query_one(f"#{self.LIST_ID}", OptionList)

    def _reload(self) -> None:
        """子类按当前数据源重建选项。"""

    def action_cancel(self) -> None:
        self.dismiss(None)


class _NamedListScreen(_ListPickerScreen):
    """「● 名称 + 描述」列表弹窗：Enter/点击关窗回传名称（/skills 与 /mcp 工具列表共用）。"""

    def __init__(self, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self._items = items

    def _reload(self) -> None:
        _rebuild_options(self._list(), [Option(_entry_row(name, desc), id=name)
                                        for name, desc in self._items])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option_id)
