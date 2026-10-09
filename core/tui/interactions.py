"""停靠区阻塞式交互：权限确认 + clarify 询问（core.tui.render.ask_permission/ask_clarify 的 App 侧）。

两者均为「后台线程等待 → 停靠区列表作答」；同一时刻至多一个请求（agent 工具串行），
并发请求需改请求队列。"""

from __future__ import annotations

from concurrent.futures import Future

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from core.tui.theme import _PLACEHOLDER
from core.tui.widgets import ClarifyConfirmed, _ClarifyList


class _DockInteractions:
    """权限确认与 clarify 的 mixin。"""

    # ---------- 权限确认 ----------

    def _begin_permission(self, message: str, future: Future[str]) -> None:
        """渲染确认卡 + 停靠区 yes/no 列表并聚焦（Enter 确认高亮项 / Esc 拒绝）。"""
        self._perm_future = future
        self._add_card("perm", Text(message, style="bold #fef3c7"))
        self._perm_pending = True
        self._prompt().disabled = True
        perm = self.query_one("#perm-list", OptionList)
        perm.styles.display = "block"  # 先显示再落高亮（watch_highlighted 会滚动，隐藏态无内容区）
        perm.highlighted = 1  # 默认 No（同旧版 [y/N]）
        perm.focus()
        self._set_status_text("Confirm permission: ↑/↓ Yes/No, Enter confirm, Esc reject.", spin=True)
        self._progress_waiting()

    def _answer_permission(self, value: str) -> None:
        """落地回答：写回等待线程、收起列表；输入条由回合结束的 _set_idle 统一恢复。"""
        if not self._perm_pending:
            return
        future, self._perm_future = self._perm_future, None
        self._perm_pending = False
        self.query_one("#perm-list", OptionList).styles.display = "none"
        self._set_status_text("Working…", spin=True)
        self._progress_resume()
        if future is not None:
            future.set_result(value)

    def action_deny_permission(self) -> None:
        """Esc：权限确认优先（挂起 → 拒绝），其次 clarify（挂起 → 取消），最后中断进行中的回合。"""
        if self._perm_pending:
            self._answer_permission("no")
        elif self._clarify_pending:
            self._answer_clarify("[User cancelled]")
        elif self._interrupt and (self._busy or self._agent_running()):
            if self._busy:  # 外部回合（auto_loop）无 UI 忙碌态，状态行不该被永久占为 Interrupting…
                self._set_status_text("Interrupting…", spin=True)
            self._interrupt()
            self.notify("User interrupted")

    def _agent_running(self) -> bool:
        """runtime 里是否有回合在跑：UI 的 _busy 只覆盖自己发起的回合，
        auto_loop 的 cron / 后台自动回合不经过 UI，Esc 需另经 runtime 判断。"""
        runtime = self._runtime
        return runtime is not None and runtime.is_running()

    # ---------- clarify ----------

    def _begin_clarify(self, questions: list[str], multi_select: bool, future: Future[str]) -> None:
        """弹选项列表（末尾 Other）并聚焦：单选 Enter/点击作答，多选 Space 勾选、Enter 提交。"""
        self._clarify_future = future
        self._clarify_pending = True
        self._clarify_multi = multi_select
        self._clarify_chosen = set()
        self._clarify_options = list(questions)
        self._clarify_other = False
        ol = self.query_one("#clarify-list", _ClarifyList)
        ol.set_options(self._clarify_rows())
        ol.styles.display = "block"  # 先显示再落高亮（watch_highlighted 会滚动，隐藏态无内容区）
        ol.highlighted = 0
        ol.focus()
        self._prompt().disabled = True
        self._set_status_text(self._clarify_hint(), spin=True)
        self._progress_waiting()

    def _clarify_rows(self):
        """选项行：多选带 ◉/◯；末尾 Other（id=other，其余 id=下标）。prompt 用 Text：
        选项文本来自模型，不得按 markup 解析（OptionList 默认 markup=True）。"""
        for index, text in enumerate(self._clarify_options):
            label = (f"{'◉' if index in self._clarify_chosen else '◯'} {text}"
                     if self._clarify_multi else text)
            yield Option(Text(label), id=str(index))
        yield Option(Text("Other (type your own answer)", style="dim"), id="other")

    def _clarify_hint(self) -> str:
        return ("Clarify: ↑/↓ move, Space toggle, Enter confirm, Esc cancel." if self._clarify_multi
                else "Clarify: ↑/↓ move, Enter select, Esc cancel.")

    def _select_clarify(self, index: int) -> None:
        """第 index 项选中：末行 Other → 输入模式；多选 → 切换勾选；单选 → 直接作答。"""
        if not self._clarify_pending or self._clarify_other:
            return
        if index >= len(self._clarify_options):  # 末行固定为 Other（见 _clarify_rows）
            self._enter_clarify_other()
        elif self._clarify_multi:
            self._toggle_clarify(index)
        else:
            self._answer_clarify(self._clarify_options[index])

    def _toggle_clarify(self, index: int) -> None:
        """多选：切换第 index 项勾选并刷新行首 ◉/◯。"""
        if index in self._clarify_chosen:
            self._clarify_chosen.discard(index)
        else:
            self._clarify_chosen.add(index)
        ol = self.query_one("#clarify-list", _ClarifyList)
        label = f"{'◉' if index in self._clarify_chosen else '◯'} {self._clarify_options[index]}"
        ol.replace_option_prompt_at_index(index, Text(label))

    def action_toggle_clarify(self) -> None:
        """Space：多选时切换高亮项勾选；单选 / Other 输入模式空操作。"""
        if not (self._clarify_pending and self._clarify_multi and not self._clarify_other):
            return
        ol = self.query_one("#clarify-list", _ClarifyList)
        if ol.has_focus and ol.highlighted is not None:
            self._select_clarify(ol.highlighted)

    def on_clarify_confirmed(self, event: ClarifyConfirmed) -> None:
        """_ClarifyList Enter：多选提交勾选；单选选中高亮项（Other → 输入模式）。"""
        if not self._clarify_pending or self._clarify_other:
            return
        ol = self.query_one("#clarify-list", _ClarifyList)
        if self._clarify_multi:
            self._answer_clarify(self._clarify_answer())
        elif ol.highlighted is not None:
            self._select_clarify(ol.highlighted)

    def _clarify_answer(self, other: str = "") -> str:
        """多选答案：勾选项逐行（Other 文本追加末行）；空选择给占位文本。"""
        chosen = [self._clarify_options[i] for i in sorted(self._clarify_chosen)]
        if other:
            chosen.append(other)
        return "\n".join(f"- {text}" for text in chosen) if chosen else "[User made no selection]"

    def _enter_clarify_other(self) -> None:
        """Other：启用输入条并以提示语占位，等待自定义回答。"""
        self._clarify_other = True
        prompt = self._prompt()
        prompt.text = ""
        prompt.placeholder = "Type your own answer…"
        prompt.disabled = False
        prompt.focus()
        self._set_status_text("Clarify: type your answer, Enter confirm, Esc cancel.", spin=False)

    def _leave_clarify_other(self) -> None:
        """Other 空提交：回到选项列表继续选择。"""
        self._clarify_other = False
        self._reset_prompt()
        self.query_one("#clarify-list", _ClarifyList).focus()
        self._set_status_text(self._clarify_hint(), spin=True)

    def _reset_prompt(self) -> None:
        """输入条回到默认占位禁用态（_set_idle 在回合结束时再启用）。"""
        prompt = self._prompt()
        prompt.text = ""
        prompt.placeholder = _PLACEHOLDER
        prompt.disabled = True

    def _answer_clarify(self, value: str) -> None:
        """落地回答：写回等待线程、收起列表；输入条恢复禁用（_set_idle 统一恢复）。"""
        if not self._clarify_pending:
            return
        future, self._clarify_future = self._clarify_future, None
        self._clarify_pending = False
        self._clarify_other = False
        self.query_one("#clarify-list", _ClarifyList).styles.display = "none"
        self._reset_prompt()
        self._set_status_text("Working…", spin=True)
        self._progress_resume()
        if future is not None:
            future.set_result(value)

    # ---------- 回合结束复位 ----------

    def _reset_interactions(self) -> None:
        """回合结束兜底：清挂起请求、收起两个列表；输入条复位由 _set_idle 负责。"""
        self._perm_pending = False
        self._perm_future = None
        self._clarify_pending = False
        self._clarify_future = None
        self._clarify_other = False
        self.query_one("#perm-list", OptionList).styles.display = "none"
        self.query_one("#clarify-list", _ClarifyList).styles.display = "none"
