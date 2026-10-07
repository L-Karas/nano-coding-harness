"""新增 /compact 指令：指令表顺序紧跟 /sessions；空闲时持 AGENT_LOCK 经 AgentRuntime.submit 在
runtime 的事件循环上调 AgentRuntime.compact 压缩，完成后按会话最新消息重放聊板；忙碌 / 无 runtime 时只提示。"""
import asyncio
from types import SimpleNamespace

import core.tui.commands as commands
from core.runtime_context import AgentInterrupted
from core.tui.ui_textual import ChatApp
from core.tui.utils import SLASH_COMMANDS


class _Runtime:  # 模拟 AgentRuntime：compact 为协程方法，submit 在 runtime 事件循环上跑完并阻塞返回
    compacted = 0

    async def compact(self) -> bool:
        self.compacted += 1
        return True

    def submit(self, coro):
        return asyncio.run(coro)


def _app(monkeypatch, events):
    """不跑事件循环的 ChatApp：渲染与线程收尾打桩，只留被测分支。"""
    monkeypatch.setattr(commands, "render_background_notification",
                        lambda body, title="": events.append(("notice", title, body)))
    monkeypatch.setattr(commands, "render_session_history", lambda session: events.append(("render", session)))
    app = ChatApp(handle_query=lambda query: None, on_interrupt=lambda: None)
    app.call_from_thread = lambda fn, *a, **kw: fn()  # 测试线程内直接执行（App 未运行）
    app._set_idle = lambda: events.append(("idle",))
    app._clear_cards = lambda: events.append(("clear",))
    app._prompt = lambda: SimpleNamespace(disabled=False)
    app._set_status_text = lambda *a, **k: None
    return app


def test_slash_compact_registered_right_after_sessions():
    assert SLASH_COMMANDS[SLASH_COMMANDS.index("/sessions") + 1] == "/compact"


def test_compact_command_compacts_then_rerenders_history(monkeypatch):
    events, runtime = [], _Runtime()
    session = SimpleNamespace(messages=["m1", "m2"])
    app = _app(monkeypatch, events)
    app._runtime = runtime
    app._manager = SimpleNamespace(load_session=lambda sid="": session)
    # 直接跑 worker 主体并 join（不起 _run_compact：其 runtime=None / 忙碌分支见下一个测试）
    app._spawn_worker(lambda: app._compact_worker(runtime), "agent-compact", "⚠️ Compact Error").join()
    assert runtime.compacted == 1, "未调用 AgentRuntime.compact"
    assert [e[0] for e in events] == ["clear", "render", "notice", "idle"], events
    assert events[1][1] is session, "重放的不是当前会话消息"


def test_compact_worker_reports_noop_without_rerender(monkeypatch):
    """低于阈值 / 未选模型：compact 返回 False → 提示未压缩，不重放历史。"""
    events = []

    class _NoopRuntime:
        async def compact(self) -> bool:
            return False

        def submit(self, coro):
            return asyncio.run(coro)

    app = _app(monkeypatch, events)
    app._spawn_worker(lambda: app._compact_worker(_NoopRuntime()), "agent-compact", "⚠️ Compact Error").join()
    assert [e[0] for e in events] == ["notice", "idle"], events
    assert "Nothing to compact" in events[0][2], events


def test_compact_worker_reports_interrupt_without_error_card(monkeypatch):
    """压缩中 Esc：submit 抛 AgentInterrupted → 提示已中断（非错误卡），且收尾复位忙碌态。"""
    events = []

    class _Interrupting:
        def compact(self):
            return None  # submit 直接抛中断：无需真协程

        def submit(self, coro):
            raise AgentInterrupted("User interrupted")

    app = _app(monkeypatch, events)
    app._spawn_worker(lambda: app._compact_worker(_Interrupting()), "agent-compact", "⚠️ Compact Error").join()
    notices = [e for e in events if e[0] == "notice"]
    assert notices and notices[0][1] == "⏹ Compact" and "interrupted" in notices[0][2], events
    assert events[-1] == ("idle",), events


def test_compact_command_guards(monkeypatch):
    events = []
    app = _app(monkeypatch, events)
    app._run_compact()  # 未接 runtime（离线演示 / 冒烟）
    assert events and "unavailable" in events[0][2], events
    events.clear()
    # 回合进行中：/compact 在 on_input_submitted 里走 busy 分支被拒，到不了 _run_compact
    app._busy = True
    app._chat = lambda: SimpleNamespace(anchor=lambda: None)
    app._run_compact = lambda: events.append(("ran",))
    app.on_input_submitted(SimpleNamespace(value="/compact"))
    assert events == [("notice", "⏳ Busy", "Previous turn is still running, please wait…")], events
