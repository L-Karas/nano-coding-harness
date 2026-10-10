"""Esc（ctx.cancel()）必须真能停掉后台工具。

协程 arun 直接 create_task 并 track 进 ctx，cancel() 才能传到内层 task；
同步 run 走 worker 线程时同理经 run_coroutine_threadsafe 的 future 传回。
否则后台 write_file/terminal/子代理会在中断后继续改仓库，结果还会作为 notification 注入下一回合。"""
import asyncio

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
        assert done[0].result == ToolResult(content=USER_INTERRUPT_PROMPT)

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
