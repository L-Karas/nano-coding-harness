"""tool_execute 接线：接管取代内置执行；未接管 / fail-open 回退内置。"""
import asyncio

import pytest

import core.agent_step as step_mod
from core.agent_step import StepPolicy, StepRenderer, run_agent_step
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook
from core.runtime_context import AgentRunContext, ToolContext
from core.tools import ToolPool
from core.tools.tool_base import BaseTool


class _Client:
    current_provider = "test"

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self, async_client=False):
        async def create(**kwargs):
            return object()
        return create


def _pool(calls):
    class _Echo(BaseTool):
        value: str = ""

        def run(self, tctx=None):
            calls.append(self.value)
            return f"echo:{self.value}"

    class _WriteFile(BaseTool):
        path: str = ""
        content: str = ""

        def run(self, tctx=None):
            calls.append("write_file")
            return "written"

    return ToolPool({"echo": _Echo, "write_file": _WriteFile})


def _tool_call(name="echo", args='{"value": "x"}'):
    return {"id": "t1", "type": "function", "function": {"name": name, "arguments": args}}


def _run(monkeypatch, tool_call, calls, *, policy=None, renderer=None):
    async def fake_stream(stream, on_text=None):
        return ("", "", [tool_call], "tool_calls", None)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    return asyncio.run(run_agent_step(
        [],
        "sys",
        client=_Client(),
        pool=_pool(calls),
        tctx=ToolContext(agent_run=AgentRunContext()),
        max_tokens=100,
        policy=policy or StepPolicy(use_extensions=True, permission_hooks=False),
        renderer=renderer or StepRenderer(),
    ))


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


def test_handle_replaces_builtin_and_after_tool_sees_result(monkeypatch):
    seen = []

    def replace(ctx):
        if ctx.tool_name == "echo":
            ctx.handle("ext:x")

    add_hook("tool_execute", replace, "ext_a")
    add_hook("after_tool", lambda ctx: seen.append(ctx.result), "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls)
    assert calls == []                                     # 内置未执行
    assert outcome.followup_messages[0]["content"] == "ext:x"
    assert seen == ["ext:x"]                               # after_tool 可见


def test_handled_error_flag_reaches_renderer(monkeypatch):
    seen = []
    add_hook("tool_execute", lambda ctx: ctx.handle("[Error] remote down", is_error=True), "ext_a")
    renderer = StepRenderer(on_tool_result=lambda text, err: seen.append((text, err)))
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls, renderer=renderer)
    assert calls == []
    assert seen == [("[Error] remote down", True)]
    assert outcome.followup_messages[0]["content"] == "[Error] remote down"


def test_without_handle_builtin_still_runs(monkeypatch):
    def observe(ctx):
        assert ctx.tool_name == "echo"
        assert ctx.args == {"value": "x"}

    add_hook("tool_execute", observe, "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls)
    assert calls == ["x"]
    assert outcome.followup_messages[0]["content"] == "echo:x"


def test_hook_exception_falls_back_to_builtin(monkeypatch):
    def broken(ctx):
        raise RuntimeError("boom")

    add_hook("tool_execute", broken, "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls)
    assert calls == ["x"]
    assert outcome.followup_messages[0]["content"] == "echo:x"


def test_handled_tool_skips_background_routing(monkeypatch):
    started = []
    monkeypatch.setattr(step_mod, "should_run_background", lambda *a: True)
    monkeypatch.setattr(step_mod, "start_background_task",
                        lambda call, pool, tctx: started.append(call) or "bg-1")
    add_hook("tool_execute", lambda ctx: ctx.handle("ext:x"), "ext_a")
    calls = []
    outcome = _run(monkeypatch, _tool_call(), calls,
                   policy=StepPolicy(use_extensions=True, permission_hooks=False, background=True))
    assert started == []
    assert outcome.followup_messages[0]["content"] == "ext:x"


def test_handled_write_file_skips_diff_preview(monkeypatch):
    previews = []
    monkeypatch.setattr(step_mod, "preview_write", lambda *a: previews.append(a) or "diff")
    diffs = []
    renderer = StepRenderer(on_diff=lambda d: diffs.append(d))
    add_hook("tool_execute", lambda ctx: ctx.handle("ext:written"), "ext_a")
    calls = []
    outcome = _run(monkeypatch,
                   _tool_call("write_file", '{"path": "a.txt", "content": "hi"}'),
                   calls,
                   renderer=renderer,
                   policy=StepPolicy(use_extensions=True, permission_hooks=False, diff_preview=True))
    assert calls == []
    assert previews == [] and diffs == []
    assert outcome.followup_messages[0]["content"] == "ext:written"
    assert "payload" not in outcome.followup_messages[0]


def test_before_tool_block_precedes_tool_execute(monkeypatch):
    calls = []
    add_hook("before_tool", lambda ctx: ctx.block("no"), "ext_a")
    add_hook("tool_execute", lambda ctx: calls.append("execute"), "ext_a")
    outcome = _run(monkeypatch, _tool_call(), [])
    assert calls == []
    assert outcome.followup_messages[0]["content"] == "[Extension blocked] no"


def test_permission_gate_runs_before_tool_execute(monkeypatch):
    calls = []
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda *a, **k: "denied")
    add_hook("tool_execute", lambda ctx: calls.append("execute"), "ext_a")
    outcome = _run(monkeypatch, _tool_call(), [],
                   policy=StepPolicy(use_extensions=True, permission_hooks=True))
    assert calls == []
    assert outcome.followup_messages[0]["content"] == "[denied]"


def test_use_extensions_false_does_not_dispatch(monkeypatch):
    calls = []
    add_hook("tool_execute", lambda ctx: ctx.handle("ext:x"), "ext_a")
    outcome = _run(monkeypatch, _tool_call(), calls,
                   policy=StepPolicy(use_extensions=False, permission_hooks=False))
    assert calls == ["x"]
    assert outcome.followup_messages[0]["content"] == "echo:x"
