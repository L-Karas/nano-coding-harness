"""压缩（/compact）可被 Esc 中断：compact 期间注册 _current_ctx / _run_task，
runtime.interrupt()（UI 的 Esc 回调）中断压缩协程，且不落会话、注册状态清理。"""
import asyncio
import threading
from types import SimpleNamespace

import core.loop_with_interrupt as lwi
from core.runtime_context import AgentInterrupted


def test_compact_registers_ctx_and_task_for_interrupt(monkeypatch):
    runtime = lwi.AgentRuntime()
    started = threading.Event()
    seen_ctx = []

    async def _slow(messages, ctx=None, auto_compact=True):  # 代替 compact_history：挂住等中断
        seen_ctx.append(ctx)
        started.set()
        await asyncio.sleep(30)
        return [{"role": "user", "content": "compacted"}], True

    updated = []
    monkeypatch.setattr(lwi, "compact_history", _slow)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", SimpleNamespace(
        load_messages=lambda: [], update_messages=updated.append))

    errors = []

    def _drive() -> None:
        try:
            runtime.submit(runtime.compact())
        except BaseException as exc:  # submit 已把取消统一成 AgentInterrupted
            errors.append(exc)

    thread = threading.Thread(target=_drive, daemon=True)
    thread.start()
    assert started.wait(5), "压缩未启动"
    assert seen_ctx[0] is not None, "compact 未把 ctx 交给压缩（Esc 的中断检查会失效）"

    assert runtime.interrupt() is True, "压缩期间 interrupt() 未生效（_run_task 未注册？）"
    thread.join(5)
    assert not thread.is_alive(), "中断后压缩线程未退出"
    assert errors and isinstance(errors[0], AgentInterrupted), errors
    assert updated == [], "中断后不应写会话"
    assert runtime._current_ctx is None and runtime._run_task is None, "中断后未清理注册状态"
