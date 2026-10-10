"""子代理接线：Agent step 的产出回填本地消息；扩展四事件在 step 内生效。"""
import asyncio

import pytest

import core.agent_step as step_mod
import core.sub_agent as sa
import core.tools
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook
from core.tools import ToolResult

TOOL_ROUND = ("", "", [{"id": "t1", "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}],
              "tool_calls", None)
DONE_ROUND = ("done", "", [], "stop", None)
TWO_TOOL_ROUND = ("", "", [
    {"id": "t1", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "ls"}'}},
    {"id": "t2", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "pwd"}'}},
], "tool_calls", None)


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

    class _Pool:
        def schemas(self):
            return []

        async def execute(self, name, args, tctx=None):
            calls["args"].append(args)
            return ToolResult(content=execute)

    async def _prepare(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: _FakeModelClient(calls))
    monkeypatch.setattr(sa, "prepare_messages", _prepare)
    monkeypatch.setattr(sa, "build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: _Pool())

    async def fake_stream(stream, on_text=None):
        return responses.pop(0)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda *a: None)
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


def test_after_tool_inject_messages_batch_end_order(monkeypatch):
    responses = [TWO_TOOL_ROUND, DONE_ROUND]
    calls = _patch(monkeypatch, responses, "raw")

    def hook(ctx):
        ctx.inject_messages.append({"role": "user", "content": f"note-{ctx.args['command']}"})

    add_hook("after_tool", hook, "test")
    assert asyncio.run(sa.spawn_subagent("do it")) == "done"
    second_round = calls["model_messages"][1]
    tool_indices = [i for i, m in enumerate(second_round) if m.get("role") == "tool"]
    note_indices = [i for i, m in enumerate(second_round)
                    if isinstance(m.get("content"), str) and m["content"].startswith("note-")]
    assert len(tool_indices) == 2
    assert len(note_indices) == 2
    assert max(tool_indices) < min(note_indices)
