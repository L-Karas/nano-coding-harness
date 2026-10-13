"""Esc（ctx.cancel()）必须真能停掉后台工具。

协程 arun 直接 create_task 并 track 进 ctx，cancel() 才能传到内层 task；
同步 run 走 worker 线程时同理经 run_coroutine_threadsafe 的 future 传回。
否则后台 write_file/terminal/子代理会在中断后继续改仓库，结果还会作为 notification 注入下一回合。

跨回合取消（docs/adr/0009-background-cancel.md）：句柄挂在 RuntimeState 上，
UI 线程经 cancel_work 指名停单个任务；Esc 仍按旧路径取消本回合启动的工作，两条通道互不依赖。
"""
import asyncio
import threading
import time

import core.background_task as bt
from core.runtime_context import AgentRunContext, ToolContext
from core.runtime_state import RuntimeState
from core.template import USER_INTERRUPT_PROMPT
from core.tools import ToolResult, ToolPool
from core.tools.tool_base import BaseTool


class _ToolCall:
    id = "call_1"

    class function:
        name = "fake"
        arguments = "{}"


class SlowTool(BaseTool):
    """Slow."""

    async def arun(self, tctx=None):
        await asyncio.sleep(30)


class _SpawnCall:
    id = "call_2"

    class function:
        name = "spawn_subagent"
        arguments = '{"description": "explore"}'


class SlowSpawnTool(BaseTool):
    """spawn_subagent stand-in：跑到被取消为止。"""

    description: str = ""

    async def arun(self, tctx=None):
        await asyncio.sleep(30)


async def _wait_completed(state: RuntimeState):
    for _ in range(200):  # 等 worker / task 收尾
        snap = state.snapshot().background
        if snap and snap[0].status == "completed":
            break
        await asyncio.sleep(0.02)
    return state.pop_completed_backgrounds()


def test_cancel_stops_background_tool(monkeypatch):
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def main():
        ctx = AgentRunContext()
        bg_id = bt.start_background_task(_ToolCall(), ToolPool({"fake": SlowTool}), ToolContext(agent_run=ctx))
        await asyncio.sleep(0.1)
        assert state.snapshot().background[0].status == "running"

        ctx.cancel()  # Esc
        done = await _wait_completed(state)
        assert done and done[0].id == bg_id
        assert done[0].result == ToolResult.cancelled(USER_INTERRUPT_PROMPT)

    asyncio.run(main())


def test_coroutine_tool_completes_on_caller_loop(monkeypatch):
    """协程 arun 走 create_task 分支：不开 worker 线程也能落结果。"""
    class QuickTool(BaseTool):
        """Quick."""

        async def arun(self, tctx=None):
            return "done"

    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def main():
        bt.start_background_task(_ToolCall(), ToolPool({"fake": QuickTool}))
        done = await _wait_completed(state)
        assert done and done[0].result == ToolResult(content="done")

    asyncio.run(main())


def test_spawn_subagent_always_background():
    assert bt.should_run_background("spawn_subagent", {})
    assert bt.should_run_background("spawn_subagent", {"should_run_in_background": False})


def test_subagent_background_record_has_no_cancel_handle(monkeypatch):
    """子代理的 bg 记录不挂取消句柄：cancel_work(bg-…) 是 no-op，按停只能经 sa 条目；Esc 仍连带停。"""
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def main():
        ctx = AgentRunContext()
        bg_id = bt.start_background_task(_SpawnCall(), ToolPool({"spawn_subagent": SlowSpawnTool}),
                                         ToolContext(agent_run=ctx))
        await asyncio.sleep(0.05)
        entry = state.snapshot().background[0]
        assert entry.is_subagent is True
        assert entry.cancellable is False

        assert state.cancel_work(bg_id) is False
        assert state.snapshot().background[0].status == "running", "bg id 不该能停子代理"

        ctx.cancel()  # 本回合 Esc 仍连带停掉本轮启动的子代理
        done = await _wait_completed(state)
        assert done and done[0].result.is_cancelled

    asyncio.run(main())


def test_collect_background_results_formats_notification(monkeypatch):
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def main():
        class QuickTool(BaseTool):
            """Quick."""

            async def arun(self, tctx=None):
                return "done"

        class FailTool(BaseTool):
            """Fail."""

            async def arun(self, tctx=None):
                raise RuntimeError("boom")

        bt.start_background_task(_ToolCall(), ToolPool({"fake": QuickTool}))
        bt.start_background_task(_ToolCall(), ToolPool({"fake": FailTool}))
        for _ in range(200):
            snap = state.snapshot().background
            if len(snap) == 2 and all(item.status == "completed" for item in snap):
                break
            await asyncio.sleep(0.02)

        notes = "\n".join(bt.collect_background_results())
        assert "<status>completed</status>" in notes
        assert "<result>done</result>" in notes
        assert "<status>error</status>" in notes
        assert "boom" in notes

    asyncio.run(main())


def test_tool_result_cancelled_is_flagged_not_error():
    result = ToolResult.cancelled("[Background task cancelled]")
    assert result.content == "[Background task cancelled]"
    assert result.is_cancelled and not result.is_error
    assert not ToolResult(content="done").is_cancelled


def test_collect_background_results_marks_cancelled(monkeypatch):
    """被停的后台任务以 cancelled 状态通知模型（区别于 completed / error）。"""
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)
    bg_id = state.allocate_background_id()
    state.register_background(bg_id, "terminal(pytest)")
    state.complete_background(bg_id, ToolResult.cancelled("[Background task cancelled]"))

    notes = "\n".join(bt.collect_background_results())
    assert "<status>cancelled</status>" in notes
    assert "[Background task cancelled]" in notes


class SyncTool(BaseTool):
    """Sync only."""

    def run(self, tctx=None):
        time.sleep(0.2)
        return "done"


def test_panel_cancel_stops_background_task_from_ui_thread(monkeypatch):
    """UI 线程经 RuntimeState.cancel_work 指名取消：任务停、结果标记 cancelled。"""
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def main():
        bg_id = bt.start_background_task(_ToolCall(), ToolPool({"fake": SlowTool}), ToolContext())
        await asyncio.sleep(0.05)
        assert state.snapshot().background[0].cancellable

        threading.Thread(target=state.cancel_work, args=(bg_id,)).start()  # 模拟 UI 线程
        done = await _wait_completed(state)
        assert done and done[0].id == bg_id
        assert done[0].result == ToolResult.cancelled("[Background task cancelled]")

    asyncio.run(main())


def test_sync_background_tool_has_no_cancel_handle(monkeypatch):
    """worker 线程路径无可杀句柄：条目不可停、cancel_work 返回 False，但结果照常回流。"""
    state = RuntimeState()
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def main():
        bg_id = bt.start_background_task(_ToolCall(), ToolPool({"fake": SyncTool}), ToolContext())
        assert state.snapshot().background[0].cancellable is False
        assert state.cancel_work(bg_id) is False
        done = await _wait_completed(state)
        assert done and done[0].result == ToolResult(content="done")

    asyncio.run(main())
