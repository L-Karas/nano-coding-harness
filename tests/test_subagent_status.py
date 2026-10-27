"""RuntimeState.snapshot 的子代理分区记录执行阶段，且子代理结束（含异常）后不留残影。"""
import asyncio

import pytest

import core.agent_step as step_mod
import core.context.compact.context_compact as compact_mod
import core.sub_agent as sa
import core.tools
import core.tools.tool_pool
from core.runtime_state import RuntimeState
from core.tools import ToolResult
from tests.support import StubModelClient


def _patch(monkeypatch, rounds, execute_tool):
    state = RuntimeState()
    client = StubModelClient()
    monkeypatch.setattr(sa, "RUNTIME_STATE", state)
    seen = []

    def only_agent():
        subs = state.snapshot().subagents
        assert len(subs) == 1
        return subs[0].id

    async def fake_streaming_message(stream, on_text=None):
        entry = state.snapshot().subagents[0]
        seen.append((entry.phase, entry.detail))
        return rounds.pop(0)

    class _Pool:
        def schemas(self):
            return []

        def is_parallel_safe(self, name):
            return False

        async def execute(
            self,
            name,
            args,
            tctx=None,
        ):
            entry = state.snapshot().subagents[0]
            seen.append((entry.phase, entry.detail))
            return ToolResult(content=await execute_tool())

    prepared = []
    original_prepare = sa.prepare_messages

    async def recording_prepare(messages, ctx=None, sub_model=False):
        prepared.append((len(messages), sub_model))
        return await original_prepare(messages, ctx, sub_model)

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: client)
    monkeypatch.setattr(compact_mod, "shared_sub_model_client", lambda: client)
    monkeypatch.setattr(sa, "prepare_messages", recording_prepare)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(step_mod, "streaming_message", fake_streaming_message)
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: _Pool())
    return seen, prepared, state


_TOOL_ROUND = ("", "", [{"id": "t1", "type": "function",
                         "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}], "tool_calls", None)


def test_phases_and_cleanup(monkeypatch):
    rounds = [_TOOL_ROUND, ("done", "", [], "stop", None)]

    async def execute_tool():
        return "out"

    seen, prepared, state = _patch(monkeypatch, rounds, execute_tool)
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert seen == [("thinking", ""), ("tool", "terminal"), ("thinking", "")]
    # 每轮模型调用前过一次 prepare_messages 且带 sub_model=True：history 不含 system，
    # 开局 user 一条，第二轮前已追加 assistant+tool
    assert prepared == [(1, True), (3, True)], prepared
    assert state.snapshot().subagents == []


def test_cleanup_on_error(monkeypatch):
    rounds = [_TOOL_ROUND]

    async def execute_tool():
        raise RuntimeError("boom")

    _, _, state = _patch(monkeypatch, rounds, execute_tool)
    with pytest.raises(RuntimeError):
        asyncio.run(sa.spawn_subagent("do it"))
    assert state.snapshot().subagents == []
