"""停靠区交互的事件路由：状态在 AskState（core.tui.ask），部件在 AskPanel（core.tui.ask_panel）。

本模块只把 Textual 事件翻译成「改状态 → sync」并处理作答后的回合收尾；无自有状态。

宿主契约（ChatApp 提供）：asks、ask_panel、board、turn（Turn）、_interrupt、_runtime（_agent_running）、
_progress_sync()、_set_status_text()、notify()。
"""

from __future__ import annotations

from concurrent.futures import Future

from core.tui.ask import CLARIFY_CANCELLED, PERMISSION_DENIED, AskKind, AskRequest
from core.tui.blocks import permission
from core.tui.widgets import ClarifyConfirmed


class _DockInteractions:
    """权限确认与 clarify 的 App 侧事件处理（无自有状态）。"""

    # ---------- 发起 ----------

    def _begin_permission(self, message: str, details: list[str], future: Future[str]) -> bool:
        """发起权限确认。

        Args:
            message: 确认文本。
            details: 触发数据段落。
            future: 等待回答的 Future。

        Returns:
            询问已受理（挂起或立即拒绝）为 True；调用方据此决定是否等待 Future。

        挂起请求 + 确认卡 + 停靠区列表（默认高亮 No，同旧版 [y/N]）。已有挂起请求时不排队，
        直接以拒绝落地 Future，等待线程不会悬挂。
        """
        if not self.asks.open(AskRequest(AskKind.PERMISSION), future):
            future.set_result(PERMISSION_DENIED)
            return True
        self.board.add(permission(message, details))
        self.ask_panel.sync()
        self._progress_sync()
        return True

    def _begin_clarify(self, questions: list[str], multi_select: bool, future: Future[str]) -> bool:
        """发起 clarify 询问。

        Args:
            questions: 问题列表。
            multi_select: 是否多选。
            future: 等待回答的 Future。

        Returns:
            询问已受理（挂起或立即取消）为 True；调用方据此决定是否等待 Future。

        弹出选项列表（末尾 Other）并聚焦：单选 Enter/点击作答，多选 Space 勾选、Enter 提交。
        已有挂起请求时不排队，直接以取消落地 Future。
        """
        if not self.asks.open(AskRequest(AskKind.CLARIFY, options=tuple(questions),
                                         multi=multi_select), future):
            future.set_result(CLARIFY_CANCELLED)
            return True
        self.ask_panel.sync()
        self._progress_sync()
        return True

    # ---------- 作答 ----------

    def _answer(self, value: str) -> None:
        """落地回答（权限 "yes"/"no"、单选选项、Other 文本）。

        Args:
            value: 回答值。

        前台回合内的询问由回合结束的 Turn.finish 收尾；回合外（子代理 / cron）没有收尾回合，
        就地恢复空闲界面。
        """
        self._after_ask(self.ask_panel.answer(value))

    def _select_clarify(self, index: int) -> None:
        """选中 clarify 选项。

        Args:
            index: 选项下标。

        末行 Other → 输入模式；多选 → 切换勾选；单选 → 直接作答。
        """
        self._after_ask(self.ask_panel.select(index))

    def action_toggle_clarify(self) -> None:
        """Space：多选时切换高亮项勾选；单选 / Other 输入模式空操作。"""
        self.ask_panel.toggle()

    def on_clarify_confirmed(self, event: ClarifyConfirmed) -> None:
        """处理 clarify 列表的 Enter 确认。

        Args:
            event: 确认事件。

        多选提交勾选；单选选中高亮项；高亮停在 Other 行则进入输入模式。
        """
        self._after_ask(self.ask_panel.confirm())

    def action_deny_permission(self) -> None:
        """Esc：权限确认优先（挂起 → 拒绝），其次 clarify（挂起 → 取消），最后中断进行中的回合。"""
        view = self.asks.view()
        if view is not None:
            if view.request.kind is AskKind.PERMISSION:
                self._answer(PERMISSION_DENIED)
            else:
                self._after_ask(self.ask_panel.cancel())
            return
        if self._interrupt and (self.turn.busy or self._agent_running()):
            if self.turn.busy:  # 外部回合（auto_loop）无 UI 忙碌态，状态行不该被永久占为 Interrupting…
                self._set_status_text("Interrupting…", spin=True)
            self._interrupt()
            self.notify("User interrupted")

    def _after_ask(self, closed: bool) -> None:
        """询问关闭后的界面收尾。

        Args:
            closed: 本次动作是否确实关闭了询问。

        回合仍在跑：回到 Working… 并重估进度；回合外：就地恢复空闲界面。
        """
        if not closed:
            return
        if self.turn.busy:
            self._set_status_text("Working…", spin=True)
            self._progress_sync()
        else:
            self.turn.finish()

    def _agent_running(self) -> bool:
        """判断 runtime 里是否有回合在跑。

        Returns:
            有回合运行时为 True。Turn.busy 只覆盖 UI 自己发起的回合，
            auto_loop 的 cron / 后台自动回合不经过 UI，Esc 需另经 runtime 判断。
        """
        runtime = self._runtime
        return runtime is not None and runtime.is_running()
