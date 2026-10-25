"""TUI 错误分支统一走 error 级 toast：失败不能被静默吞掉。

覆盖 `_spawn_worker` / `_client_call` / `_open_mcp` / `_turn_worker` 四条迁移点；
info 级（busy / 不可用 / 中断）由 fork / compact 测试覆盖。
"""
import core.client as client_mod
import core.mcp.mcp_client as mcp_client
from core.tui.ui_textual import ChatApp


def _app(events):
    """不跑事件循环的 ChatApp：把 notify 打桩成事件记录。"""
    app = ChatApp(handle_query=lambda query: None, on_interrupt=lambda: None)
    app.notify = lambda message, title="", severity="information", **kw: events.append(
        ("notice", title, message, severity))
    return app


def test_worker_exception_notifies_error():
    events = []
    app = _app(events)
    app.call_from_thread = lambda fn, *a, **kw: fn()
    app._set_idle = lambda: events.append(("idle",))

    def _boom():
        raise RuntimeError("boom")

    app._spawn_worker(_boom, "agent-turn", "⚠️ Agent Error").join()

    assert ("notice", "⚠️ Agent Error", "RuntimeError: boom", "error") in events, events
    assert events[-1] == ("idle",), "收尾仍须复位忙碌态"


def test_client_call_failure_notifies_error(monkeypatch):
    events = []
    app = _app(events)

    def _boom():
        raise RuntimeError("no openai")

    monkeypatch.setattr(client_mod, "get_provider_list", _boom)
    app._open_providers()

    assert events == [("notice", "⚠️ Provider List", "no openai", "error")], events


def test_open_mcp_failure_notifies_error(monkeypatch):
    events = []
    app = _app(events)

    def _boom():
        raise RuntimeError("broken")

    monkeypatch.setattr(mcp_client, "get_mcp_server_list", _boom)
    app._open_mcp()

    assert events == [("notice", "⚠️ MCP Servers", "broken", "error")], events


def test_turn_worker_save_failure_notifies_error():
    events = []
    app = _app(events)

    class _BadManager:
        def add_message(self, message):
            raise RuntimeError("disk full")

    app._manager = _BadManager()
    app._handle = lambda query: events.append(("handled", query))
    app._turn_worker("hi")

    assert events == [
        ("notice", "⚠️ Session Error", "保存会话消息失败：disk full", "error"),
        ("handled", "hi"),
    ], events
