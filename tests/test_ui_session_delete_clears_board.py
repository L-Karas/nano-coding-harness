"""删除当前会话时立即清空聊板（不等 /sessions 弹窗关闭）：SessionPickerScreen 删除
当前会话会即刻回调 on_delete_current；ChatApp 以 _clear_cards 作为该回调。"""
import core.tui.commands as commands
from core.tui import ui_textual
from core.tui.screens import SessionPickerScreen


class _Session:
    def __init__(self, sid):
        self.id = sid


class _FakeManager:
    """与 SessionManager.delete_session 同契约：删当前会话会把 current_session 置空。"""

    def __init__(self, ids, current):
        self.sessions = [_Session(i) for i in ids]
        self.current_session = current

    def load_session_list(self):
        return list(self.sessions)

    def delete_session(self, sid):
        if self.current_session == sid:
            self.current_session = ""
        self.sessions = [s for s in self.sessions if s.id != sid]


def _picker(manager, calls):
    scr = SessionPickerScreen(manager, on_delete_current=lambda: calls.append("clear"))
    scr._reload = lambda: calls.append("reload")  # 未挂载：不查询部件
    return scr


def test_delete_current_session_clears_board_immediately():
    calls = []
    manager = _FakeManager(["s1", "s2"], current="s1")
    _picker(manager, calls)._delete_session("s1")
    assert calls == ["clear", "reload"], "删除当前会话应立即清板，弹窗不必关闭"
    assert manager.current_session == ""

    calls.clear()
    manager = _FakeManager(["s1", "s2"], current="s1")
    _picker(manager, calls)._delete_session("s2")
    assert calls == ["reload"], "删除非当前会话不应动聊板"
    assert manager.current_session == "s1"


def test_delete_current_without_callback_is_noop():
    manager = _FakeManager(["s1", "s2"], current="s1")
    scr = SessionPickerScreen(manager)
    scr._reload = lambda: None
    scr._delete_session("s1")  # 未接线不应报错
    assert manager.current_session == ""


def test_list_emptied_renders_placeholder(monkeypatch):
    calls = []
    monkeypatch.setattr(commands, "render_sessions", lambda: calls.append("empty-card"))
    app = ui_textual.ChatApp(handle_query=lambda query: None)
    app._manager = _FakeManager([], current="")
    app._on_session_picked((None, True))
    assert calls == ["empty-card"]
