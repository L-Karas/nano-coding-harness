"""interrupt：Esc 走的路径必须真能停掉进行中的回合——run 以 AgentInterrupted 结束、
阻塞中的 submit 返回、事件循环线程不被卡死。"""
import asyncio
import threading
import time

import core.loop_with_interrupt as lwi


def test_interrupt_stops_running_turn(monkeypatch):
    class _Mgr:
        def load_messages(self):
            return []

        def update_messages(self, _m):
            pass

        def add_message(self, _m):
            pass

    async def _hang(**_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: ([], {}))
    monkeypatch.setattr(lwi, "SESSION_MANAGER", _Mgr())

    runtime = lwi.AgentRuntime()
    monkeypatch.setattr(runtime, "call_llm", _hang)

    outcome = []

    def turn():
        try:
            runtime.submit(runtime.run())
            outcome.append("finished")
        except lwi.AgentInterrupted:
            outcome.append("interrupted")

    worker = threading.Thread(target=turn)
    worker.start()

    deadline = time.monotonic() + 5
    while runtime._current_ctx is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime._current_ctx is not None, "回合未启动"

    assert runtime.interrupt() is True, "进行中的回合应接受中断"
    worker.join(timeout=5)
    assert not worker.is_alive(), "中断后 submit 必须返回（回合已收尾）"
    assert outcome == ["interrupted"], outcome
    assert runtime.interrupt() is False, "空闲时中断应返回 False"
