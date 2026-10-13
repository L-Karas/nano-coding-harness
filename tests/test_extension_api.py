"""ExtensionAPI 注册缓冲与上下文对象的状态方法。"""
import pytest

from core.extension.api import EVENTS, ExtensionAPI
from core.extension.context import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext, ToolExecuteContext,
)


def test_on_buffers_and_commit_returns_registrations():
    api = ExtensionAPI("nano_extension_demo")
    fn = lambda ctx: None
    api.on("before_llm", fn)
    assert api.commit() == [("before_llm", fn, "user", False)]
    assert api.commit() == []  # 已清空


def test_on_rejects_unknown_event_and_non_callable():
    api = ExtensionAPI("nano_extension_demo")
    with pytest.raises(ValueError):
        api.on("never", lambda ctx: None)
    with pytest.raises(TypeError):
        api.on("before_llm", "not-callable")


def test_external_api_rejects_builtin_stage_and_fail_closed():
    api = ExtensionAPI("nano_extension_demo")
    with pytest.raises(ValueError):
        api.on("before_tool", lambda ctx: None, stage="gate")
    with pytest.raises(ValueError):
        api.on("before_tool", lambda ctx: None, fail_closed=True)


def test_builtin_api_accepts_gate_stage():
    api = ExtensionAPI("builtin:permission", builtin=True)
    fn = lambda ctx: None
    api.on("before_tool", fn, stage="gate", fail_closed=True)
    assert api.commit() == [("before_tool", fn, "gate", True)]


def test_five_events_are_declared():
    assert EVENTS == ("before_llm", "after_llm", "before_tool", "tool_execute", "after_tool")


def test_abort_and_block_helpers_capture_state():
    llm = BeforeLLMContext(messages=[], tools=[], max_tokens=10)
    llm.abort("policy")
    assert llm.aborted and llm.abort_reason == "policy"

    tool = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    tool.block("no shell")
    assert tool.decisions[-1].action == "deny"
    assert tool.decisions[-1].reason == "no shell"
    assert tool.blocked is False


def test_after_contexts_start_with_empty_inject_messages():
    assert AfterLLMContext(content=None, tool_calls=[]).inject_messages == []
    assert AfterToolContext(tool_name="terminal", args={}, result="out").inject_messages == []


def test_tool_execute_handle_captures_state_and_normalizes():
    ctx = ToolExecuteContext(tool_name="web_search", args={"query": "x"})
    assert (ctx.handled, ctx.result, ctx.is_error) == (False, "", False)
    ctx.handle(42, is_error=1)
    assert (ctx.handled, ctx.result, ctx.is_error) == (True, "42", True)


def test_tool_execute_result_without_handle_is_not_takeover():
    ctx = ToolExecuteContext(tool_name="web_search", args={})
    ctx.result = "ignored"
    assert ctx.handled is False


def test_tool_execute_context_is_exported_from_package():
    from core.extension import ToolExecuteContext as Exported
    assert Exported is ToolExecuteContext
