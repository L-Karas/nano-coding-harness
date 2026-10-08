"""四个扩展事件在主循环的接线：abort/改参/拦截/结果改写与注入。"""
import contextlib

import pytest

import core.loop_with_interrupt as lwi
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook

TOOL_CALL = {"id": "t1", "type": "function",
             "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}


@pytest.fixture(autouse=True)
def _clean_extensions():
    reset_extensions()
    yield
    reset_extensions()


class _Mgr:
    def __init__(self):
        self.messages = []

    def load_messages(self):
        return list(self.messages)

    def update_messages(self, messages):
        self.messages = list(messages)

    def add_message(self, message):
        self.messages.append(message)


def _prepare_runtime(monkeypatch, mgr, responses, execute):
    """responses 为 (content, reasoning, tool_calls, finish_reason, usage) 列表。"""
    calls = {"llm": [], "tools": []}

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", mgr)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: ([], {"terminal": object()}))
    monkeypatch.setattr(lwi, "should_run_background", lambda *a: False)
    monkeypatch.setattr(lwi, "trigger_hooks", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_result_diff", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_background_notification", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_working_status", contextlib.nullcontext)

    async def _prepare(messages, ctx):
        return messages

    async def _execute(handler, args, name, ctx):
        calls["tools"].append(args)
        return execute

    monkeypatch.setattr(lwi, "prepare_messages", _prepare)
    monkeypatch.setattr(lwi, "execute_tool", _execute)

    runtime = lwi.AgentRuntime()

    async def _call_llm(**kwargs):
        calls["llm"].append(kwargs)
        return object()

    async def _stream(_stream):
        return responses.pop(0)

    monkeypatch.setattr(runtime, "call_llm", _call_llm)
    monkeypatch.setattr(runtime, "stream", _stream)
    return runtime, calls


def test_before_llm_abort_ends_turn_without_model_call(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr, [], "unused")
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    runtime.submit(runtime.run())
    assert calls["llm"] == []
    assert mgr.messages[-1]["content"] == "[Extension aborted] policy"


def test_before_llm_mutation_reaches_model(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr, [("answer", "", [], "stop", None)], "unused")
    add_hook("before_llm", lambda ctx: ctx.messages.append({"role": "user", "content": "injected"}), "test")
    runtime.submit(runtime.run())
    assert any(m["content"] == "injected" for m in calls["llm"][0]["messages"])


def test_after_llm_rewrites_content_and_injects(monkeypatch):
    mgr = _Mgr()
    runtime, _ = _prepare_runtime(monkeypatch, mgr, [("original", "", [], "stop", None)], "unused")

    def hook(ctx):
        ctx.content = "rewritten"
        ctx.inject_messages.append({"role": "user", "content": "note"})

    add_hook("after_llm", hook, "test")
    runtime.submit(runtime.run())
    assert mgr.messages[-2]["content"] == "rewritten"
    assert mgr.messages[-1] == {"role": "user", "content": "note"}


def test_before_tool_block_writes_result_and_skips_execution(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "unused")
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    runtime.submit(runtime.run())
    assert calls["tools"] == []
    tool_message = next(m for m in mgr.messages if m.get("tool_call_id") == "t1")
    assert tool_message["content"] == "[Extension blocked] no shell"


def test_before_tool_args_mutation_reaches_executor_and_permission(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "raw")
    seen = []
    monkeypatch.setattr(lwi, "trigger_hooks", lambda event, tool_call: seen.append(tool_call) or None)
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "ls -la"}), "test")
    runtime.submit(runtime.run())
    assert calls["tools"] == [{"command": "ls -la"}]
    assert '"command": "ls -la"' in seen[0].function.arguments


def test_after_tool_result_rewrite_and_inject(monkeypatch):
    mgr = _Mgr()
    runtime, _ = _prepare_runtime(monkeypatch, mgr,
                                  [("", "", [TOOL_CALL], "tool_calls", None),
                                   ("done", "", [], "stop", None)], "raw")

    def hook(ctx):
        ctx.result = "processed"
        ctx.inject_messages.append({"role": "user", "content": "after-tool note"})

    add_hook("after_tool", hook, "test")
    runtime.submit(runtime.run())
    tool_messages = [m for m in mgr.messages if m.get("tool_call_id") == "t1"]
    assert tool_messages[0]["content"] == "processed"
    assert mgr.messages[-2] == {"role": "user", "content": "after-tool note"}
    assert mgr.messages[-1]["content"] == "done"


def test_after_llm_clear_tool_calls_ends_turn(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "unused")
    add_hook("after_llm", lambda ctx: setattr(ctx, "tool_calls", []), "test")
    runtime.submit(runtime.run())
    assert len(calls["llm"]) == 1
    assert calls["tools"] == []
    assert mgr.messages[-1]["role"] == "assistant"
    assert mgr.messages[-1]["content"] == ""
