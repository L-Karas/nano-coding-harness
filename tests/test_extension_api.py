"""ExtensionAPI 注册缓冲、host 身份与上下文对象的状态方法。"""
import pytest

from core.extension.api import EVENTS, ExtensionAPI
from core.extension.builtin import _BuiltinExtensionAPI
from core.extension.context import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext,
    Ballot, Decision, ToolExecuteContext,
)
from core.extension.protocol import Registration


def test_on_buffers_and_commit_returns_registrations():
    api = ExtensionAPI()
    fn = lambda ctx: None
    api.on("before_llm", fn)
    assert api._commit() == [Registration("before_llm", fn, "user", False)]
    assert api._commit() == []  # 已清空


def test_on_rejects_unknown_event_and_non_callable():
    api = ExtensionAPI()
    with pytest.raises(ValueError):
        api.on("never", lambda ctx: None)
    with pytest.raises(TypeError):
        api.on("before_llm", "not-callable")


def test_external_api_rejects_builtin_stage_and_fail_closed():
    api = ExtensionAPI()
    with pytest.raises(ValueError):
        api.on("before_tool", lambda ctx: None, stage="gate")
    with pytest.raises(ValueError):
        api.on("before_tool", lambda ctx: None, fail_closed=True)


def test_builtin_api_accepts_gate_stage():
    api = _BuiltinExtensionAPI()
    fn = lambda ctx: None
    api.on("before_tool", fn, stage="gate", fail_closed=True)
    assert api._commit() == [Registration("before_tool", fn, "gate", True)]


def test_on_rejects_unknown_stage_for_external_and_builtin():
    with pytest.raises(ValueError, match="未知扩展阶段"):
        ExtensionAPI().on("before_tool", lambda ctx: None, stage="bogus")
    with pytest.raises(ValueError, match="未知扩展阶段"):
        _BuiltinExtensionAPI().on(
            "before_tool", lambda ctx: None, stage="bogus")


def test_builtin_flag_is_read_only():
    external = ExtensionAPI()
    builtin = _BuiltinExtensionAPI()
    assert external.builtin is False and builtin.builtin is True
    with pytest.raises(AttributeError):
        external.builtin = True
    with pytest.raises(AttributeError):
        builtin.builtin = False


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

    tool.deny("no shell again")
    assert [decision.action for decision in tool.decisions] == ["deny", "deny"]


def test_before_tool_facts_are_read_only():
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    with pytest.raises(AttributeError, match="read-only"):
        ctx.tool_name = "read_file"
    with pytest.raises(AttributeError, match="read-only"):
        ctx.cwd = None
    with pytest.raises(AttributeError, match="read-only"):
        ctx.decisions = Ballot()
    with pytest.raises(AttributeError, match="read-only"):
        ctx.blocked = True
    with pytest.raises(AttributeError, match="read-only"):
        ctx.block_reason = "forged"
    with pytest.raises(AttributeError, match="read-only"):
        del ctx.tool_name
    with pytest.raises(AttributeError, match="read-only"):
        del ctx.blocked
    assert (ctx.tool_name, ctx.blocked, ctx.block_reason) == ("terminal", False, "")


def test_contexts_reject_foreign_attributes():
    tool = BeforeToolContext(tool_name="terminal", args={})
    with pytest.raises(AttributeError):
        tool.aborted = True
    with pytest.raises(AttributeError):
        tool.handled = True

    execute = ToolExecuteContext(tool_name="terminal", args={})
    with pytest.raises(AttributeError):
        execute.blocked = True


def test_ballot_rejects_invalid_elements_and_actions():
    ballot = Ballot()
    with pytest.raises(TypeError):
        ballot.append("allow")
    with pytest.raises(ValueError):
        ballot.append(Decision("approve"))
    ballot.append(Decision("allow", "trusted", source="ext"))
    assert ballot == [Decision("allow", "trusted", source="ext")]


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


def test_other_context_facts_are_read_only():
    llm = AfterLLMContext(content="x", tool_calls=[])
    with pytest.raises(AttributeError, match="read-only"):
        llm.finish_reason = "stop"

    before_llm = BeforeLLMContext(messages=[], tools=[], max_tokens=1)
    with pytest.raises(AttributeError, match="read-only"):
        before_llm.aborted = True
    with pytest.raises(AttributeError, match="read-only"):
        del before_llm.aborted
    before_llm.abort("policy")
    assert before_llm.aborted is True

    after = AfterToolContext(tool_name="terminal", args={}, result="out")
    with pytest.raises(AttributeError, match="read-only"):
        after.is_error = True
    with pytest.raises(AttributeError, match="read-only"):
        after.tool_name = "read_file"

    execute = ToolExecuteContext(tool_name="terminal", args={})
    with pytest.raises(AttributeError, match="read-only"):
        execute.tctx = None
    with pytest.raises(AttributeError, match="read-only"):
        execute.handled = True
    with pytest.raises(AttributeError, match="read-only"):
        del execute.handled
    execute.handle("done")
    assert execute.handled is True
