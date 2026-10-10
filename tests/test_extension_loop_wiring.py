"""主循环接线：Agent step 的产出按顺序落会话；扩展四事件在 step 内生效。"""
import contextlib

import pytest

import core.agent_step as step_mod
import core.loop_with_interrupt as lwi
from core import interaction
from core.extension import reset_extensions
from core.extension.dispatcher import add as add_hook
from core.tools import ToolPool
from core.tools.tool_base import BaseTool

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


class _Client:
    current_provider = "test"

    def __init__(self, calls):
        self._calls = calls

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self, async_client=False):
        async def create(**kwargs):
            self._calls["llm"].append(kwargs)
            return object()
        return create


def _terminal_pool(calls, output):
    class _Terminal(BaseTool):
        command: str = ""

        def run(self, tctx=None):
            calls["tools"].append({"command": self.command})
            return output

    return ToolPool({"terminal": _Terminal})


async def _identity(messages, ctx):
    return messages


def _streamer(responses):
    async def fake_stream(stream, on_text=None):
        return responses.pop(0)

    return fake_stream


def _prepare_runtime(monkeypatch, mgr, responses, execute):
    """responses 为 (content, reasoning, tool_calls, finish_reason, usage) 列表。"""
    calls = {"llm": [], "tools": []}

    monkeypatch.setattr(lwi, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(lwi, "inject_background_notifications", lambda: None)
    monkeypatch.setattr(lwi, "SESSION_MANAGER", mgr)
    monkeypatch.setattr(lwi, "assemble_tool_pool", lambda *a, **k: _terminal_pool(calls, execute))
    monkeypatch.setattr(lwi, "shared_model_client", lambda: _Client(calls))
    monkeypatch.setattr(lwi, "prepare_messages", _identity)
    monkeypatch.setattr(interaction, "stream_assistant_response", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "render_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "render_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "render_tool_result_diff", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "render_background_notification", lambda *a, **k: None)
    monkeypatch.setattr(interaction, "render_working_status", contextlib.nullcontext)
    monkeypatch.setattr(interaction, "render_thinking_status", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(interaction, "render_scope", contextlib.nullcontext)
    monkeypatch.setattr(step_mod, "streaming_message", _streamer(responses))
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda *a, **k: None)
    monkeypatch.setattr(step_mod, "should_run_background", lambda *a: False)

    runtime = lwi.AgentRuntime()
    return runtime, calls


def test_before_llm_abort_ends_turn_without_model_call(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr, [], "unused")
    add_hook("before_llm", lambda ctx: ctx.abort("policy"), "test")
    runtime.run_turn()
    assert calls["llm"] == []
    assert mgr.messages[-1]["content"] == "[Extension aborted] policy"


def test_before_llm_mutation_reaches_model(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr, [("answer", "", [], "stop", None)], "unused")
    add_hook("before_llm", lambda ctx: ctx.messages.append({"role": "user", "content": "injected"}), "test")
    runtime.run_turn()
    assert any(m["content"] == "injected" for m in calls["llm"][0]["messages"])


def test_after_llm_rewrites_content_and_injects(monkeypatch):
    mgr = _Mgr()
    runtime, _ = _prepare_runtime(monkeypatch, mgr, [("original", "", [], "stop", None)], "unused")

    def hook(ctx):
        ctx.content = "rewritten"
        ctx.inject_messages.append({"role": "user", "content": "note"})

    add_hook("after_llm", hook, "test")
    runtime.run_turn()
    assert mgr.messages[-2]["content"] == "rewritten"
    assert mgr.messages[-1] == {"role": "user", "content": "note"}


def test_before_tool_block_writes_result_and_skips_execution(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "unused")
    add_hook("before_tool", lambda ctx: ctx.block("no shell"), "test")
    runtime.run_turn()
    assert calls["tools"] == []
    tool_message = next(m for m in mgr.messages if m.get("tool_call_id") == "t1")
    assert tool_message["content"] == "[Extension blocked] no shell"


def test_before_tool_args_mutation_reaches_executor_and_permission(monkeypatch):
    mgr = _Mgr()
    runtime, calls = _prepare_runtime(monkeypatch, mgr,
                                      [("", "", [TOOL_CALL], "tool_calls", None),
                                       ("done", "", [], "stop", None)], "raw")
    seen = []
    monkeypatch.setattr(step_mod, "trigger_hooks", lambda event, tool_call: seen.append(tool_call) or None)
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "ls -la"}), "test")
    runtime.run_turn()
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
    runtime.run_turn()
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
    runtime.run_turn()
    assert len(calls["llm"]) == 1
    assert calls["tools"] == []
    assert mgr.messages[-1]["role"] == "assistant"
    assert mgr.messages[-1]["content"] == ""
