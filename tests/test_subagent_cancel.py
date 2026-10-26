"""子代理的跨回合取消（docs/adr/0009-background-cancel.md）：sa 条目带句柄，
cancel_work 指名停掉正在跑的子代理任务，右栏条目随 finally 清除，
结果经同一通知通道回传 <status>cancelled</status>。"""
import asyncio

import pytest

import core.agent_step as step_mod
import core.background_task as bt
import core.sub_agent as sa
import core.tools
from core.runtime_context import AgentRunContext, ToolContext
from core.runtime_state import RuntimeState
from core.tools import ToolPool
from core.tools.extra_tools.subagent import SpawnSubagent


def _patch_spawn(monkeypatch, state):
    monkeypatch.setattr(sa, "RUNTIME_STATE", state)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: object())

    async def prepare(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "prepare_messages", prepare)

    class _Pool:
        def schemas(self):
            return []

    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: _Pool())


def test_cancel_work_stops_running_subagent(monkeypatch):
    state = RuntimeState()
    _patch_spawn(monkeypatch, state)

    async def forever(*args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(step_mod, "run_agent_step", forever)

    async def main():
        task = asyncio.create_task(sa.spawn_subagent("explore"))
        await asyncio.sleep(0.05)
        subs = state.snapshot().subagents
        assert len(subs) == 1 and subs[0].cancellable

        assert state.cancel_work(subs[0].id) is True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state.snapshot().subagents == [], "取消后右栏不留残影"

    asyncio.run(main())


class _SpawnCall:
    id = "call_sa"

    class function:
        name = "spawn_subagent"
        arguments = '{"description": "explore", "goal": "explore", "role": "researcher"}'


def test_cancel_work_reports_cancelled_notification(monkeypatch):
    """端到端：■ 指名停子代理（经 background_task 路由）后，结果以
    <status>cancelled</status> 注入通知通道。"""
    state = RuntimeState()
    _patch_spawn(monkeypatch, state)
    monkeypatch.setattr(bt, "RUNTIME_STATE", state)

    async def forever(*args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(step_mod, "run_agent_step", forever)

    async def main():
        bt.start_background_task(_SpawnCall(), ToolPool({"spawn_subagent": SpawnSubagent}),
                                 ToolContext(agent_run=AgentRunContext()))
        await asyncio.sleep(0.05)
        subs = state.snapshot().subagents
        assert len(subs) == 1 and subs[0].cancellable
        assert subs[0].role == "researcher"

        assert state.cancel_work(subs[0].id) is True
        for _ in range(200):  # 等 bg 任务收尾、结果入队
            snap = state.snapshot().background
            if snap and snap[0].status == "completed":
                break
            await asyncio.sleep(0.02)

        notes = bt.collect_background_results()
        assert len(notes) == 1
        assert "<status>cancelled</status>" in notes[0]
        assert state.snapshot().subagents == []

    asyncio.run(main())
