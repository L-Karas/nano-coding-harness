"""Esc（ctx.cancel()）必须真能停掉后台工具。

协程 handler 直接 create_task 并 track 进 ctx，cancel() 才能传到内层 task；
同步 handler 走 worker 线程时同理经 run_coroutine_threadsafe 的 future 传回。
否则后台 write_file/terminal/子代理会在中断后继续改仓库，结果还会作为 notification 注入下一回合。"""
import asyncio

import core.background_task as bt
from core.runtime_context import AgentRunContext
from core.template import USER_INTERRUPT_PROMPT


class _ToolCall:
    id = "call_1"

    class function:
        name = "fake"
        arguments = "{}"


def test_cancel_stops_background_tool():
    async def slow_tool(**_):
        await asyncio.sleep(30)

    async def main():
        bt.BACKGROUND_TASKS.clear()
        bt.BACKGROUND_RESULTS.clear()
        ctx = AgentRunContext()
        bg_id = bt.start_background_task(_ToolCall(), {"fake": slow_tool}, ctx)
        await asyncio.sleep(0.1)
        assert bt.BACKGROUND_TASKS[bg_id]["status"] == "running"

        ctx.cancel()  # Esc
        for _ in range(100):  # 等 worker 线程收尾
            if bt.BACKGROUND_RESULTS.get(bg_id):
                break
            await asyncio.sleep(0.02)

        assert bt.BACKGROUND_RESULTS.get(bg_id) == USER_INTERRUPT_PROMPT, bt.BACKGROUND_RESULTS

    asyncio.run(main())


def test_coroutine_handler_completes_on_caller_loop():
    """协程 handler 走 create_task 分支：不开 worker 线程也能落结果。"""
    async def quick_tool(**_):
        return "done"

    async def main():
        bt.BACKGROUND_TASKS.clear()
        bt.BACKGROUND_RESULTS.clear()
        bg_id = bt.start_background_task(_ToolCall(), {"fake": quick_tool})
        for _ in range(50):
            if bt.BACKGROUND_RESULTS.get(bg_id):
                break
            await asyncio.sleep(0.01)
        assert bt.BACKGROUND_RESULTS[bg_id] == "done"

    asyncio.run(main())


def test_spawn_subagent_always_background():
    assert bt.should_run_background("spawn_subagent", {})
    assert bt.should_run_background("spawn_subagent", {"should_run_in_background": False})
