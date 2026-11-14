"""停靠区询问 Adapter：把 AskState 的视图幂等地落到部件（列表 / 输入条 / 状态行）。

状态与作答协议在 core.tui.ask；本模块只做「部件 = 视图的派生」这一件事。`sync` 可任意频繁
调用：只在「无询问 ↔ 有询问」「选项列表 ↔ Other 输入」这两处跳变时做一次性动作
（设高亮、清输入、改焦点），其余时间保留用户的高亮与输入。
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets.option_list import Option

from core.tui.ask import AskKind, AskState, AskView
from core.tui.parts import Parts
from core.tui.theme import PLACEHOLDER

OTHER_LABEL = "Other (type your own answer)"
OTHER_PROMPT = "Type your own answer…"  # Other 模式输入条占位


class AskPanel:
    """权限确认 / clarify 的部件层：状态从 AskState 读，动作写回后 sync。"""

    def __init__(self, parts: Parts, asks: AskState) -> None:
        """初始化停靠区 Adapter。

        Args:
            parts: 部件取件门面。
            asks: 待答请求状态机。
        """
        self._parts = parts
        self._asks = asks
        self._shown_kind: AskKind | None = None  # 当前已落部件的询问类型（跳变检测）
        self._other = False  # 上次落部件的 Other 模式（跳变检测）

    # ---------- 视图 → 部件 ----------

    def sync(self) -> None:
        """把当前视图幂等地落到部件。

        无询问：收起两个列表、复位输入条（只做一次）。
        有询问：显示对应列表；首次显示时设默认高亮并聚焦；进入 Other 时启用输入条。
        状态行同步显示操作提示（Other 模式停表）。
        """
        view = self._asks.view()
        if view is None:
            self._hide()
            return
        self._parts.prompt.disabled = not view.other_mode
        if view.request.kind is AskKind.PERMISSION:
            self._show_permission()
        else:
            self._show_clarify(view)
        self._parts.status.set_text(self._hint(view), spin=not view.other_mode)

    def _show_permission(self) -> None:
        """显示权限确认列表（只在从其它状态进入时做一次性动作）。"""
        if self._shown_kind is AskKind.PERMISSION:
            return
        self._parts.clarify.styles.display = "none"
        perm = self._parts.perm
        perm.styles.display = "block"
        perm.highlighted = 1  # 默认 No（同旧版 [y/N]）
        perm.focus()
        self._shown_kind = AskKind.PERMISSION
        self._other = False

    def _show_clarify(self, view: AskView) -> None:
        """显示 clarify 选项列表，并在 Other 模式切换输入条。

        Args:
            view: 当前视图。
        """
        clarify = self._parts.clarify
        if self._shown_kind is not AskKind.CLARIFY:
            self._parts.perm.styles.display = "none"
            clarify.set_options(self._rows(view))
            clarify.styles.display = "block"
            clarify.highlighted = 0
            clarify.focus()
            self._shown_kind = AskKind.CLARIFY
        elif view.request.multi:  # 勾选变化：就地刷新行首 ◉/◯，不动高亮
            for index in range(len(view.request.options)):
                clarify.replace_option_prompt_at_index(index, self._option_text(view, index))
        if view.other_mode and not self._other:  # 进入 Other：清空并聚焦输入条
            prompt = self._parts.prompt
            prompt.text = ""
            prompt.placeholder = OTHER_PROMPT
            prompt.focus()
        elif not view.other_mode and self._other:  # 退出 Other：回到列表
            self._reset_prompt()
            clarify.focus()
        self._other = view.other_mode

    def _hide(self) -> None:
        """收起两个列表并复位输入条（只做一次）。"""
        if self._shown_kind is None:
            return
        self._parts.perm.styles.display = "none"
        self._parts.clarify.styles.display = "none"
        self._reset_prompt()
        self._shown_kind = None
        self._other = False

    def _reset_prompt(self) -> None:
        """输入条回到默认占位禁用态（回合收尾的 Turn.finish 再启用）。"""
        prompt = self._parts.prompt
        prompt.text = ""
        prompt.placeholder = PLACEHOLDER
        prompt.disabled = True

    def _rows(self, view: AskView) -> list[Option]:
        """构造 clarify 选项行。

        Args:
            view: 当前视图。

        Returns:
            选项行（多选带 ◉/◯）+ 末尾 Other 行；prompt 用 Text，选项文本来自模型，
            不得按 markup 解析（OptionList 默认 markup=True）。
        """
        rows = [Option(self._option_text(view, index), id=str(index))
                for index in range(len(view.request.options))]
        rows.append(Option(Text(OTHER_LABEL, style="dim"), id="other"))
        return rows

    @staticmethod
    def _option_text(view: AskView, index: int) -> Text:
        """构造单个选项的显示文本。

        Args:
            view: 当前视图。
            index: 选项下标。

        Returns:
            多选带勾选字形，单选原样。
        """
        label = view.request.options[index]
        if view.request.multi:
            label = f"{'◉' if index in view.chosen else '◯'} {label}"
        return Text(label)

    @staticmethod
    def _hint(view: AskView) -> str:
        """生成状态行操作提示。

        Args:
            view: 当前视图。

        Returns:
            对应状态的快捷键提示。
        """
        if view.request.kind is AskKind.PERMISSION:
            return "Confirm permission: ↑/↓ Yes/No, Enter confirm, Esc reject."
        if view.other_mode:
            return "Clarify: type your answer, Enter confirm, Esc cancel."
        if view.request.multi:
            return "Clarify: ↑/↓ move, Space toggle, Enter confirm, Esc cancel."
        return "Clarify: ↑/↓ move, Enter select, Esc cancel."

    # ---------- 动作 ----------

    def answer(self, value: str) -> bool:
        """作答并收起（权限 yes/no、单选选项、Other 文本）。

        Args:
            value: 答案。

        Returns:
            确实关闭了询问为 True。
        """
        closed = self._asks.answer(value)
        if closed:
            self.sync()
        return closed

    def select(self, index: int) -> bool:
        """列表选中一行。

        Args:
            index: 选中下标；等于选项数即 Other 行。

        Returns:
            确实关闭了询问为 True（Other / 多选勾选只切状态）。
        """
        view = self._asks.view()
        if view is None or view.other_mode or view.request.kind is not AskKind.CLARIFY:
            return False
        if index >= len(view.request.options):
            self.enter_other()
            return False
        if view.request.multi:
            self._asks.toggle(index)
            self.sync()
            return False
        return self.answer(view.request.options[index])

    def toggle(self) -> None:
        """Space：切换高亮项勾选（仅多选、列表聚焦时）。"""
        view = self._asks.view()
        if view is None or view.other_mode or not view.request.multi:
            return
        clarify = self._parts.clarify
        if not clarify.has_focus or clarify.highlighted is None:
            return
        self.select(clarify.highlighted)

    def confirm(self) -> bool:
        """Enter：多选提交勾选；单选选中高亮项；高亮在 Other 行则进入输入模式。

        Returns:
            确实关闭了询问为 True。
        """
        view = self._asks.view()
        if view is None or view.other_mode:
            return False
        clarify = self._parts.clarify
        highlighted = clarify.highlighted
        if highlighted is not None and highlighted >= len(view.request.options):
            self.enter_other()  # 多选 / 单选一致：Enter 在 Other 行只进入输入模式
            return False
        if view.request.multi:
            return self.answer("")
        if highlighted is None:
            return False
        return self.select(highlighted)

    def enter_other(self) -> None:
        """进入 Other 手动输入模式。"""
        if self._asks.set_other_mode(True):
            self.sync()

    def submit_other(self, text: str) -> bool:
        """提交 Other 输入；空白则退回选项列表。

        Args:
            text: 输入文本。

        Returns:
            确实关闭了询问为 True；空白提交只退回列表。
        """
        view = self._asks.view()
        if view is None or not view.other_mode:
            return False
        text = text.strip()
        if not text:
            self._asks.set_other_mode(False)
            self.sync()
            return False
        return self.answer(text)

    def cancel(self) -> bool:
        """取消当前询问（Esc / 回合收尾）。

        Returns:
            确实关闭了询问为 True。
        """
        closed = self._asks.cancel()
        if closed:
            self.sync()
        return closed

    # ---------- 焦点 ----------

    def focus_target(self, widget: Widget | None) -> Widget | None:
        """把主屏上的焦点改道到当前交互。

        Args:
            widget: 原焦点目标。

        Returns:
            改道后的目标；目标已禁用时保持原目标。权限挂起 → 权限列表；
            clarify 挂起（非 Other）→ clarify 列表；否则 → 输入条。
        """
        view = self._asks.view()
        if view is None:
            target = self._parts.prompt
        elif view.request.kind is AskKind.PERMISSION:
            target = self._parts.perm
        elif view.other_mode:
            target = self._parts.prompt
        else:
            target = self._parts.clarify
        return widget if widget is target or target.disabled else target
