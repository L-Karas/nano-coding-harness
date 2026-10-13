"""Agent step 契约：一次模型调用 + 工具派发；扩展注入顺序、abort、中断占位、请求视图与钳制。"""
import asyncio
import json

import pytest

import core.agent_step as step_mod
from core.agent_step import StepPolicy, StepRenderer, group_parallel_tool_calls, run_agent_step
from core.extension import reset_extensions
from core.extension.builtin import register_builtin_extensions
from core.extension.builtin.permission import Rules
from core.extension.dispatcher import add as add_hook
from core.runtime_context import AgentRunContext, ToolContext
from core.tools import ToolPool
from core.tools.base_tools.edit import EditFile
from core.tools.base_tools.glob import Glob
from core.tools.base_tools.grep import Grep
from core.tools.base_tools.read import ReadFile
from core.tools.base_tools.write import WriteFile
from core.tools.extra_tools.cron import ListCrons
from core.tools.extra_tools.memory import SaveMemory
from core.tools.extra_tools.skill import LoadSkill
from core.tools.extra_tools.subagent import SpawnSubagent
from core.tools.tool_base import BaseTool
from core.tools.web_search.web_extract import WebExtract
from core.tools.web_search.web_search import WebSearch


class Echo(BaseTool):
    """Echo."""
    value: str = ""

    def run(self, tctx=None):
        return f"echo:{self.value}"


class ParallelProbe(BaseTool):
    """声明 parallel_safe 的可并发测试工具：子类覆写 arun 定义行为。"""
    value: str = ""
    parallel_safe: bool = True


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


def _tool_call_cmd(cmd, call_id="t1"):
    return _tool_call("terminal", json.dumps({"command": cmd}), call_id)


def _run(
    monkeypatch,
    responses,
    *,
    client=None,
    history=None,
    pool=None,
    policy=None,
    renderer=None,
    tctx=None,
):
    async def fake_stream(stream, on_text=None):
        return responses.pop(0)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    return asyncio.run(run_agent_step(
        history or [],
        "sys",
        client=client or _Client(),
        pool=pool or ToolPool({"echo": Echo}),
        tctx=tctx or ToolContext(agent_run=AgentRunContext()),
        max_tokens=1000,
        policy=policy or StepPolicy(use_extensions=False),
        renderer=renderer or StepRenderer(),
    ))


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    register_builtin_extensions(rules=Rules())
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
                   policy=StepPolicy(use_extensions=True))
    assert outcome.followup_messages == [
        {"role": "user", "content": "llm-note"},
        {"role": "tool", "tool_call_id": "t1", "content": "rewritten"},
        {"role": "user", "content": "tool-note"},
    ]


def test_before_llm_abort_returns_outcome_without_request(monkeypatch):
    client = _Client()
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    outcome = _run(
        monkeypatch,
        [],
        client=client,
        policy=StepPolicy(use_extensions=True),
    )
    assert outcome.aborted is True
    assert outcome.abort_reason == "policy"
    assert client.kwargs is None


def test_before_tool_block_writes_blocked_result(monkeypatch):
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    outcome = _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=True))
    assert outcome.followup_messages[0]["content"] == "[Extension blocked] no shell"


def test_floor_command_blocked_even_with_use_extensions_false(monkeypatch):
    outcome = _run(monkeypatch, [("", "", [_tool_call_cmd("sudo ls")], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=False))
    assert outcome.followup_messages[0]["content"] == "Permission denied: 'sudo' is on the deny list."


def test_gate_sees_args_after_extension_rewrite(monkeypatch):
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "sudo ls"}), "test")
    outcome = _run(monkeypatch, [("", "", [_tool_call_cmd("ls")], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=True))
    assert outcome.followup_messages[0]["content"].startswith("Permission denied: 'sudo'")


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
                   policy=StepPolicy(use_extensions=False, background=True))
    assert started and outcome.followup_messages[0]["content"].startswith("[Background task `bg-1` started]")

    started.clear()
    _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)])
    assert started == []


def test_background_route_sees_rewritten_call_arguments(monkeypatch):
    started = []
    monkeypatch.setattr(step_mod, "start_background_task",
                        lambda call, pool, tctx: started.append(call) or "bg-1")
    monkeypatch.setattr(step_mod, "should_run_background", lambda *_: True)
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "ls -la"}), "test")

    outcome = _run(monkeypatch, [("", "", [_tool_call_cmd("ls")], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=True, background=True))
    assert started
    assert json.loads(started[0].function.arguments) == {"command": "ls -la"}
    assert outcome.followup_messages[0]["content"].startswith("[Background task `bg-1` started]")


def test_renderer_receives_tool_call_and_result(monkeypatch):
    seen = []
    renderer = StepRenderer(on_tool_call=lambda n, a: seen.append(("call", n, a)),
                            on_tool_result=lambda text, err: seen.append(("result", text, err)))
    _run(monkeypatch, [("", "", [_tool_call()], "tool_calls", None)], renderer=renderer)
    assert ("call", "echo", {"value": "x"}) in seen
    assert ("result", "echo:x", False) in seen


def _names(groups):
    return [[call["function"]["name"] for call in group] for group in groups]


_GROUP_POOL = ToolPool({
    "read_file": ReadFile,
    "glob": Glob,
    "grep": Grep,
    "write_file": WriteFile,
    "edit_file": EditFile,
})


def test_group_parallel_tool_calls_groups_between_barriers():
    calls = [
        _tool_call("read_file", call_id="t1"),
        _tool_call("grep", call_id="t2"),
        _tool_call("write_file", call_id="t3"),
        _tool_call("glob", call_id="t4"),
    ]
    assert _names(group_parallel_tool_calls(calls, _GROUP_POOL)) == [
        ["read_file", "grep"], ["write_file"], ["glob"]]


def test_group_parallel_tool_calls_keeps_parallel_runs_separate():
    calls = [
        _tool_call("write_file", call_id="t1"),
        _tool_call("read_file", call_id="t2"),
        _tool_call("read_file", call_id="t3"),
        _tool_call("edit_file", call_id="t4"),
    ]
    assert _names(group_parallel_tool_calls(calls, _GROUP_POOL)) == [
        ["write_file"], ["read_file", "read_file"], ["edit_file"]]


def test_group_parallel_tool_calls_all_parallel_is_one_group():
    calls = [
        _tool_call("read_file", call_id="t1"),
        _tool_call("grep", call_id="t2"),
        _tool_call("glob", call_id="t3"),
    ]
    assert _names(group_parallel_tool_calls(calls, _GROUP_POOL)) == [["read_file", "grep", "glob"]]


def test_group_parallel_tool_calls_unknown_tool_is_own_group():
    calls = [
        _tool_call("read_file", call_id="t1"),
        _tool_call("frobnicate", call_id="t2"),
        _tool_call("grep", call_id="t3"),
    ]
    assert _names(group_parallel_tool_calls(calls, _GROUP_POOL)) == [
        ["read_file"], ["frobnicate"], ["grep"]]


def test_group_parallel_tool_calls_empty_input():
    assert group_parallel_tool_calls([], _GROUP_POOL) == []


def test_group_parallel_tool_calls_uses_declared_parallel_safe():
    """内置工具中声明 parallel_safe 的 9 个连成一个并发组，其余工具仍是屏障。"""
    names = ["read_file", "glob", "grep", "list_crons", "load_skill",
             "save_memory", "spawn_subagent", "web_search", "web_extract"]
    pool = ToolPool({
        "read_file": ReadFile,
        "glob": Glob,
        "grep": Grep,
        "list_crons": ListCrons,
        "load_skill": LoadSkill,
        "save_memory": SaveMemory,
        "spawn_subagent": SpawnSubagent,
        "web_search": WebSearch,
        "web_extract": WebExtract,
    })
    calls = [_tool_call(name, call_id=f"t{i}") for i, name in enumerate(names)]
    assert _names(group_parallel_tool_calls(calls, pool)) == [names]


def test_parallel_tool_calls_run_concurrently(monkeypatch):
    both = asyncio.Event()
    active = []

    class Probe(ParallelProbe):
        async def arun(self, tctx=None):
            active.append(self.value)
            if len(active) == 2:
                both.set()
            try:
                await asyncio.wait_for(both.wait(), timeout=0.5)
            finally:
                active.remove(self.value)
            return f"done:{self.value}"

    outcome = _run(monkeypatch,
                   [("", "", [_tool_call("read_file", '{"value": "a"}', "t1"),
                              _tool_call("grep", '{"value": "b"}', "t2")], "tool_calls", None)],
                   pool=ToolPool({"read_file": Probe, "grep": Probe}))
    assert [m["content"] for m in outcome.followup_messages] == ["done:a", "done:b"]


def test_parallel_group_preserves_call_order(monkeypatch):
    class Probe(ParallelProbe):
        async def arun(self, tctx=None):
            await asyncio.sleep(0.05 if self.value == "slow" else 0)
            return f"done:{self.value}"

    outcome = _run(monkeypatch,
                   [("", "", [_tool_call("read_file", '{"value": "slow"}', "t1"),
                              _tool_call("grep", '{"value": "fast"}', "t2")], "tool_calls", None)],
                   pool=ToolPool({"read_file": Probe, "grep": Probe}))
    assert [m["tool_call_id"] for m in outcome.followup_messages] == ["t1", "t2"]
    assert [m["content"] for m in outcome.followup_messages] == ["done:slow", "done:fast"]


def test_barrier_tool_separates_parallel_groups(monkeypatch):
    log = []

    class Probe(ParallelProbe):
        async def arun(self, tctx=None):
            log.append(("start", self.value))
            await asyncio.sleep(0)
            log.append(("end", self.value))
            return f"done:{self.value}"

    class Barrier(BaseTool):
        value: str = ""

        def run(self, tctx=None):
            log.append(("start", self.value))
            log.append(("end", self.value))
            return f"done:{self.value}"

    _run(monkeypatch,
         [("", "", [
             _tool_call("read_file", '{"value": "a"}', "t1"),
             _tool_call("grep", '{"value": "b"}', "t2"),
             _tool_call("write_file", '{"value": "c"}', "t3"),
             _tool_call("read_file", '{"value": "d"}', "t4"),
         ], "tool_calls", None)],
         pool=ToolPool({"read_file": Probe, "grep": Probe, "write_file": Barrier}))
    assert log == [("start", "a"), ("start", "b"), ("end", "a"), ("end", "b"),
                   ("start", "c"), ("end", "c"), ("start", "d"), ("end", "d")]


@pytest.mark.parametrize("cancel_turn", [False, True])
def test_interrupt_mid_parallel_group_places_prompts(monkeypatch, cancel_turn):
    started = asyncio.Event()
    blocker = asyncio.Event()

    class Probe(ParallelProbe):
        async def arun(self, tctx=None):
            started.set()
            await blocker.wait()
            return f"done:{self.value}"

    ctx = AgentRunContext()
    calls = [_tool_call("read_file", '{"value": "a"}', "t1"),
             _tool_call("grep", '{"value": "b"}', "t2")]

    async def fake_stream(stream, on_text=None):
        return ("", "", calls, "tool_calls", None)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)

    async def driver():
        task = asyncio.create_task(run_agent_step(
            [],
            "sys",
            client=_Client(),
            pool=ToolPool({"read_file": Probe, "grep": Probe}),
            tctx=ToolContext(agent_run=ctx),
            max_tokens=1000,
            policy=StepPolicy(use_extensions=False),
            renderer=StepRenderer(),
        ))
        await started.wait()
        ctx.cancel()
        if cancel_turn:  # 生产 Esc：TurnRunner 同时取消 ctx 与回合 task
            task.cancel()
        return await task

    outcome = asyncio.run(driver())
    assert [m["content"] for m in outcome.followup_messages] == [
        "[User interrupted]", "[User interrupted]"]


def test_parallel_group_renderer_order(monkeypatch):
    class Probe(ParallelProbe):
        async def arun(self, tctx=None):
            return f"done:{self.value}"

    seen = []
    renderer = StepRenderer(
        on_tool_call=lambda name, _args: seen.append(("call", name)),
        on_tool_result=lambda text, _err: seen.append(("result", text)))
    _run(monkeypatch,
         [("", "", [_tool_call("read_file", '{"value": "a"}', "t1"),
                     _tool_call("grep", '{"value": "b"}', "t2")], "tool_calls", None)],
         pool=ToolPool({"read_file": Probe, "grep": Probe}),
         renderer=renderer)
    assert seen == [("call", "read_file"), ("call", "grep"),
                    ("result", "done:a"), ("result", "done:b")]
