"""interrupt：Esc 走的路径必须真能停掉进行中的回合——run 以 AgentInterrupted 结束、
阻塞中的 submit 返回、事件循环线程不被卡死。"""
import asyncio
import threading
import time
from types import SimpleNamespace

import core.agent_loop as lwi
from tests.support import MemorySession, StubModelClient


def test_interrupt_stops_running_turn(monkeypatch):
    mgr = MemorySession()

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: SimpleNamespace(schemas=lambda: []))
    monkeypatch.setattr(lwi, "shared_model_client", StubModelClient)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", mgr)

    runtime = lwi.AgentRuntime()
    monkeypatch.setattr(lwi, "run_agent_step", _hang)

    outcome = []

    def turn():
        try:
            runtime.run_turn()
            outcome.append("finished")
        except lwi.AgentInterrupted:
            outcome.append("interrupted")

    worker = threading.Thread(target=turn)
    worker.start()

    deadline = time.monotonic() + 5
    while runtime.current_context is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.current_context is not None, "回合未启动"

    assert runtime.interrupt() is True, "进行中的回合应接受中断"
    worker.join(timeout=5)
    assert not worker.is_alive(), "中断后 submit 必须返回（回合已收尾）"
    assert outcome == ["interrupted"], outcome
    assert runtime.interrupt() is False, "空闲时中断应返回 False"


def test_is_running_tracks_turn_lifecycle(monkeypatch):
    """UI 的 Esc 以 is_running() 识别外部回合（auto_loop 的 cron / 后台自动回合）：进行中 True，结束后 False。"""
    mgr = MemorySession()

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: SimpleNamespace(schemas=lambda: []))
    monkeypatch.setattr(lwi, "shared_model_client", StubModelClient)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", mgr)

    runtime = lwi.AgentRuntime()
    monkeypatch.setattr(lwi, "run_agent_step", _hang)
    assert runtime.is_running() is False, "空闲 runtime 不应报告 running"

    outcome = []

    def turn():
        try:
            runtime.run_turn()
        except lwi.AgentInterrupted:
            outcome.append("interrupted")

    worker = threading.Thread(target=turn)
    worker.start()
    deadline = time.monotonic() + 5
    while runtime.current_context is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.is_running() is True, "回合进行中应报告 running（Esc 据此中断 auto_loop 回合）"

    runtime.interrupt()
    worker.join(timeout=5)
    assert outcome == ["interrupted"]
    assert runtime.is_running() is False, "回合结束后应恢复 idle"
