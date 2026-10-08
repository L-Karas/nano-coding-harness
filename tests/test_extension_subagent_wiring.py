"""子代理四节点接线：abort、改参送达执行器、拦截写回、结果改写。"""
import asyncio

import pytest

import core.sub_agent as sa
import core.tools
import core.tools.tool_loader
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook

TOOL_ROUND = ("", "", [{"id": "t1", "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}],
              "tool_calls", None)
DONE_ROUND = ("done", "", [], "stop", None)


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


class _FakeModelClient:
    current_provider = "test"

    def __init__(self, calls):
        self.calls = calls

    def get_model_client(self, **_):
        async def create(**kwargs):
            self.calls.setdefault("model_messages", []).append(kwargs["messages"])
            return object()
        return create

    def clamp_max_tokens(self, requested):
        return requested


def _patch(monkeypatch, responses, execute):
    calls = {"args": []}

    async def _streaming_message(stream, ctx=None):
        return responses.pop(0)

    async def _execute_tool(handler, args, name, ctx):
        calls["args"].append(args)
        return execute

    async def _prepare(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _FakeModelClient(calls))
    monkeypatch.setattr(sa, "with_retry_async", lambda fn, provider="": fn())
    monkeypatch.setattr(sa, "prepare_messages", _prepare)
    monkeypatch.setattr(sa, "streaming_message", _streaming_message)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(sa, "trigger_hooks", lambda *a: None)
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: ([], {"terminal": object()}))
    monkeypatch.setattr(core.tools.tool_loader, "execute_tool", _execute_tool)
    return calls


def test_before_llm_abort_returns_notice(monkeypatch):
    responses = [DONE_ROUND]
    _patch(monkeypatch, responses, "unused")
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "(subagent aborted by extension: policy)"
    assert responses == [DONE_ROUND]  # 模型未被调用


def test_before_llm_mutation_reaches_model(monkeypatch):
    responses = [DONE_ROUND]
    calls = _patch(monkeypatch, responses, "unused")
    add_hook("before_llm", lambda ctx: ctx.messages.append({"role": "user", "content": "injected"}), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert any(m.get("content") == "injected" for m in calls["model_messages"][0])


def test_before_tool_args_mutation_reaches_executor(monkeypatch):
    responses = [TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "ls -la"}), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert calls["args"] == [{"command": "ls -la"}]


def test_before_tool_block_writes_result(monkeypatch):
    responses = [TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    assert calls["args"] == []
    second_round = calls["model_messages"][1]
    tool_message = next(m for m in second_round if m.get("role") == "tool")
    assert tool_message["content"] == "[Extension blocked] no shell"


def test_after_tool_rewrites_result(monkeypatch):
    responses = [TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")
    add_hook("after_tool", lambda ctx: setattr(ctx, "result", "processed"), "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    second_round = calls["model_messages"][1]
    tool_message = next(m for m in second_round if m.get("role") == "tool")
    assert tool_message["content"] == "processed"
