"""ExtensionAPI 注册缓冲与上下文对象的状态方法。"""
import pytest

from core.extension.api import EVENTS, ExtensionAPI
from core.extension.context import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext,
)


def test_on_buffers_and_commit_returns_registrations():
    api = ExtensionAPI("nano_extension_demo")
    fn = lambda ctx: None
    api.on("before_llm", fn)
    assert api.commit() == [("before_llm", fn)]
    assert api.commit() == []  # 已清空


def test_on_rejects_unknown_event_and_non_callable():
    api = ExtensionAPI("nano_extension_demo")
    with pytest.raises(ValueError):
        api.on("never", lambda ctx: None)
    with pytest.raises(TypeError):
        api.on("before_llm", "not-callable")


def test_four_events_are_declared():
    assert EVENTS == ("before_llm", "after_llm", "before_tool", "after_tool")


def test_abort_and_block_helpers_capture_state():
    llm = BeforeLLMContext(messages=[], tools=[], max_tokens=10)
    llm.abort("policy")
    assert llm.aborted and llm.abort_reason == "policy"

    tool = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    tool.block("no shell")
    assert tool.blocked and tool.block_reason == "no shell"


def test_after_contexts_start_with_empty_inject_messages():
    assert AfterLLMContext(content=None, tool_calls=[]).inject_messages == []
    assert AfterToolContext(tool_name="terminal", args={}, result="out").inject_messages == []
