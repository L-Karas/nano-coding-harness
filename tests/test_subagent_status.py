"""SUBAGENT_TASKS 记录执行阶段，且子代理结束（含异常）后不留残影。"""
import asyncio

import pytest

import core.context.compact.context_compact as compact_mod
import core.sub_agent as sa
import core.tools
import core.tools.tool_loader


class _FakeModelClient:
    current_provider = "test"

    def get_model_client(self, **_):
        async def create(**_):
            return object()  # 假 stream，真解析见 fake_streaming_message
        return create

    def compact_threshold(self) -> int:
        return 10**9  # 测试消息量远小于此：预算管线不触发压缩

    def reserve_threshold(self) -> int:
        return 0

    def clamp_max_tokens(self, requested: int) -> int:
        return requested


def _patch(monkeypatch, rounds, execute_tool):
    seen = []

    def only_agent():
        assert len(sa.SUBAGENT_TASKS) == 1
        return next(iter(sa.SUBAGENT_TASKS))

    async def fake_streaming_message(stream, ctx=None):
        aid = only_agent()
        seen.append((sa.SUBAGENT_TASKS[aid]["phase"], sa.SUBAGENT_TASKS[aid]["detail"]))
        return rounds.pop(0)

    async def wrapped_execute_tool(handler, args, name, ctx):
        aid = only_agent()
        seen.append((sa.SUBAGENT_TASKS[aid]["phase"], sa.SUBAGENT_TASKS[aid]["detail"]))
        return await execute_tool()

    prepared = []
    original_prepare = sa.prepare_messages

    async def recording_prepare(messages, ctx=None, sub_model=False):
        prepared.append((len(messages), sub_model))
        return await original_prepare(messages, ctx, sub_model)

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _FakeModelClient())
    monkeypatch.setattr(compact_mod, "shared_sub_model_client", lambda: _FakeModelClient())
    monkeypatch.setattr(sa, "with_retry_async", lambda fn, provider="": fn())
    monkeypatch.setattr(sa, "prepare_messages", recording_prepare)
    monkeypatch.setattr(sa, "streaming_message", fake_streaming_message)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "trigger_hooks", lambda *a: None)
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: ([], {"terminal": object()}))
    monkeypatch.setattr(core.tools.tool_loader, "execute_tool", wrapped_execute_tool)
    return seen, prepared


_TOOL_ROUND = ("", "", [{"id": "t1", "type": "function",
                         "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}], "tool_calls", None)


def test_phases_and_cleanup(monkeypatch):
    rounds = [_TOOL_ROUND, ("done", "", [], "stop", None)]

    async def execute_tool():
        return "out"

    seen, prepared = _patch(monkeypatch, rounds, execute_tool)
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert seen == [("thinking", ""), ("tool", "terminal"), ("thinking", "")]
    # 每轮模型调用前过一次 prepare_messages 且带 sub_model=True：开局 system+user 两条，
    # 第二轮前已追加 assistant+tool
    assert prepared == [(2, True), (4, True)], prepared
    assert sa.SUBAGENT_TASKS == {}


def test_cleanup_on_error(monkeypatch):
    rounds = [_TOOL_ROUND]

    async def execute_tool():
        raise RuntimeError("boom")

    _patch(monkeypatch, rounds, execute_tool)
    with pytest.raises(RuntimeError):
        asyncio.run(sa.spawn_subagent("do it"))
    assert sa.SUBAGENT_TASKS == {}
