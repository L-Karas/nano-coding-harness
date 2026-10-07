"""会话选择弹窗（/sessions）：切会话 / Delete 原地确认删除。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from textual.widgets.option_list import Option

from core.tui.screens.base import _InlineConfirm, _ListPickerScreen, _SessionRow, _rebuild_options


class SessionPickerScreen(_InlineConfirm, _ListPickerScreen):
    """会话选择弹窗：Enter 切换 / Delete 原地确认删除 / Esc 取消；dismiss 结果
    (session id 或 None, 列表是否已删空)。删除当前会话经 on_delete_current 立即清空聊板。"""

    TITLE = "Select a session"
    HINT = "  ↑/↓ browse    Enter switch    Delete remove    Esc close"
    LIST_ID = "sess-list"
    CONFIRM = True
    BINDINGS = [("delete", "remove_selected", "Delete")]

    def __init__(self, manager: Any, on_delete_current: Optional[Callable[[], None]] = None) -> None:
        super().__init__()
        self._manager = manager
        self._on_delete_current = on_delete_current
        self._sessions: list[Any] = []  # 列表快照（confirm 回调按高亮行取回会话）

    def _row(self, session) -> _SessionRow:
        return _SessionRow(
            session.title or session.id,
            current=session.id == self._manager.current_session,
            timestamp=str(session.timestamp)[:16],  # 展示到分钟；排序仍按完整时间戳
        )

    def _reload(self) -> None:
        self._sessions = sorted(self._manager.load_session_list(), key=lambda s: s.timestamp, reverse=True)
        _rebuild_options(self._list(),
                         [Option(self._row(s), id=s.id) for s in self._sessions])

    def action_cancel(self) -> None:
        if not self._cancel_confirm():  # 确认中：Esc 只撤销确认，弹窗保持打开
            self.dismiss((None, False))

    def action_remove_selected(self) -> None:
        """Delete：窗内底部红字原地确认；Enter 才真删（确认目标 = 按下 Delete 时的会话）。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        session = self._sessions[olist.highlighted]
        self._ask_confirm(f'Delete "{session.title or session.id}"?',
                          lambda: self._delete_session(session.id))

    def _delete_session(self, session_id: str) -> None:
        if session_id == self._manager.current_session and self._on_delete_current is not None:
            self._on_delete_current()  # 立即清空聊板（弹窗可继续开着）
        self._manager.delete_session(session_id)
        if self._manager.load_session_list():
            self._reload()
        else:
            self.dismiss((None, True))  # 删空后退出，主界面显示空列表卡片

    def _selected(self, option_id: Optional[str]) -> None:
        """Enter/点击选中行：切到该会话；原地确认中由 _InlineConfirm 先行拦截。"""
        if option_id:
            self.dismiss((option_id, False))
