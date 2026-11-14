"""TUI 错误分支统一走 error 级 toast：失败不能被静默吞掉。

覆盖 `Turn` 工作线程 / `_client_call` / `_open_mcp` / `_turn_worker` 四条迁移点；
info 级（busy / 不可用 / 中断）由 fork / compact 测试覆盖。
"""
import asyncio

import core.client as client_mod
import core.mcp.mcp_client as mcp_client
from core.tui.ui_textual import ChatApp
from tests.support import chat_app


def _app(events):
    """不跑事件循环的 ChatApp：把 notify 打桩成事件记录。"""
    app = ChatApp(handle_query=lambda query: None, on_interrupt=lambda: None)
    app.notify = lambda message, title="", severity="information", **kw: events.append(
        ("notice", title, message, severity))
    return app


def test_worker_exception_notifies_error():
    async def main() -> None:
        events = []
        async with chat_app(size=(80, 24)) as (app, _pilot):
            app.notify = lambda message, title="", severity="information", **kw: events.append(
                ("notice", title, message, severity))

            def _boom():
                raise RuntimeError("boom")

            thread = app.turn.start(_boom, status_text="Working…", thread_name="agent-turn",
                                    error_title="⚠️ Agent Error")
            for _ in range(300):
                await asyncio.sleep(0.01)
                if not thread.is_alive() and not app.turn.busy:
                    break

            assert ("notice", "⚠️ Agent Error", "RuntimeError: boom", "error") in events, events
            assert not app.turn.busy, "收尾仍须复位忙碌态"
            assert not app.parts.prompt.disabled

    asyncio.run(main())


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
