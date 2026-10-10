"""Agent step 契约：一次模型调用 + 工具派发；扩展注入顺序、abort、halt、中断占位、请求视图与钳制。"""
import asyncio

import pytest

import core.agent_step as step_mod
from core.agent_step import StepPolicy, StepRenderer, run_agent_step
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook
from core.runtime_context import AgentRunContext, ToolContext
from core.tools import ToolPool
from core.tools.tool_base import BaseTool


class Echo(BaseTool):
    """Echo."""
    value: str = ""

    def run(self, tctx=None):
        return f"echo:{self.value}"


class _Client:
    current_provider = "test"

    def __init__(self):
        self.kwargs = None
        self.requested = None

    def clamp_max_tokens(self, requested):
        self.requested = requested
        return min(requested, 100)

    def get_model_client(self, async_client=False):
        async def create(**kwargs):
            self.kwargs = kwargs
            return object()
        return create


def _tool_call(name="echo", args='{"value": "x"}', call_id="t1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _run(monkeypatch, responses, *, client=None, history=None, pool=None, policy=None,
         renderer=None, tctx=None):
    async def fake_stream(stream, on_text=None):
        return responses.pop(0)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    return asyncio.run(run_agent_step(
        history or [], "sys",
        client=client or _Client(),
        pool=pool or ToolPool({"echo": Echo}),
        tctx=tctx or ToolContext(agent_run=AgentRunContext()),
        max_tokens=1000,
        policy=policy or StepPolicy(use_extensions=False, permission_hooks=False),
        renderer=renderer or StepRenderer(),
    ))


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


def test_tool_round_returns_ordered_followups(monkeypatch):
    outcome = _run(monkeypatch, [
        ("thought", "reason", [_tool_call()], "tool_calls", {"total_tokens": 3}),
    ])
    assert outcome.assistant_message["content"] == "thought"
    assert outcome.assistant_message["reasoning_content"] == "reason"
    assert outcome.usage == {"total_tokens": 3}
    assert len(outcome.tool_calls) == 1
    assert outcome.followup_messages == [
        {"role": "tool", "tool_call_id": "t1", "content": "echo:x"},
    ]


def test_no_tool_calls_returns_only_injections(monkeypatch):
    outcome = _run(monkeypatch, [("done", "", [], "stop", None)])
    assert outcome.tool_calls == []
    assert outcome.followup_messages == []


def test_finish_reason_is_passed_through(monkeypatch):
    outcome = _run(monkeypatch, [("half", "", [], "length", None)])
    assert outcome.finish_reason == "length"


def test_request_view_and_max_tokens_clamped(monkeypatch):
    client = _Client()
    _run(monkeypatch, [("ok", "", [], "stop", None)], client=client)
    assert client.kwargs["max_tokens"] == 100          # 1000 → clamp 100
    assert client.kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert client.requested == 1000


def test_after_llm_and_after_tool_injections_keep_order(monkeypatch):
    def after_llm(ctx):
        ctx.inject_messages.append({"role": "user", "content": "llm-note"})

    def after_tool(ctx):
        ctx.result = "rewritten"
        ctx.inject_messages.append({"role": "user", "content": "tool-note"})

    add_hook("after_llm", after_llm, "test")
    add_hook("after_tool", after_tool, "test")
    outcome = _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=True, permission_hooks=False))
    assert outcome.followup_messages == [
        {"role": "user", "content": "llm-note"},
        {"role": "tool", "tool_call_id": "t1", "content": "rewritten"},
        {"role": "user", "content": "tool-note"},
    ]


def test_before_llm_abort_returns_outcome_without_request(monkeypatch):
    client = _Client()
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    outcome = _run(monkeypatch, [], client=client, policy=StepPolicy(use_extensions=True))
    assert outcome.aborted is True
    assert outcome.abort_reason == "policy"
    assert client.kwargs is None


def test_before_tool_block_writes_blocked_result(monkeypatch):
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    outcome = _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=True, permission_hooks=False))
    assert outcome.followup_messages[0]["content"] == "[Extension blocked] no shell"


def test_permission_hook_blocks(monkeypatch):
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda *a, **k: "denied by hook")
    outcome = _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=False, permission_hooks=True))
    assert outcome.followup_messages[0]["content"] == "[denied by hook]"


def test_halt_callback_stops_remaining_tools(monkeypatch):
    seen = []

    def halt(name, result):
        seen.append((name, result.content))
        return True

    outcome = _run(monkeypatch, [("", "", [
        _tool_call(args='{"value": "a"}', call_id="t1"),
        _tool_call(args='{"value": "b"}', call_id="t2"),
    ], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=False, permission_hooks=False, should_halt=halt))
    assert outcome.halted is True
    assert seen == [("echo", "echo:a")]
    assert [m["content"] for m in outcome.followup_messages] == [
        "echo:a", "[Error] tool call aborted before execution"]


def test_interrupted_context_places_placeholders(monkeypatch):
    ctx = AgentRunContext()
    ctx.cancelled.set()
    outcome = _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)],
                   tctx=ToolContext(agent_run=ctx))
    assert outcome.followup_messages[0]["content"] == "[User interrupted]"


def test_background_routing_respects_policy(monkeypatch):
    started = []
    monkeypatch.setattr(step_mod, "start_background_task", lambda call, pool, tctx: started.append(call) or "bg-1")
    monkeypatch.setattr(step_mod, "should_run_background", lambda *_: True)

    outcome = _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=False, permission_hooks=False, background=True))
    assert started and outcome.followup_messages[0]["content"].startswith("[Background task `bg-1` started]")

    started.clear()
    _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)])
    assert started == []


def test_renderer_receives_tool_call_and_result(monkeypatch):
    seen = []
    renderer = StepRenderer(on_tool_call=lambda n, a: seen.append(("call", n, a)),
                            on_tool_result=lambda text, err: seen.append(("result", text, err)))
    _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)], renderer=renderer)
    assert ("call", "echo", {"value": "x"}) in seen
    assert ("result", "echo:x", False) in seen
