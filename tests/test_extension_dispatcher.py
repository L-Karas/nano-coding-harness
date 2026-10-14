"""派发器：顺序链式、短路、异步、fail-open、CancelledError 传播、字段归一。"""
import asyncio
from unittest.mock import MagicMock

import pytest

import core.extension.dispatcher as dispatcher
from core.extension.context import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext, ToolExecuteContext,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    dispatcher.reset()
    yield
    dispatcher.reset()


def _run(event, ctx):
    asyncio.run(dispatcher.dispatch(event, ctx))


def _run_external_off(event, ctx):
    asyncio.run(dispatcher.dispatch(event, ctx, include_external=False))


def test_hooks_run_in_order_and_chain_mutations():
    seen = []

    def first(ctx):
        seen.append("first")
        ctx.args["command"] = "ls -la"

    def second(ctx):
        seen.append("second")
        assert ctx.args["command"] == "ls -la"

    dispatcher.add("before_tool", first, "ext_a")
    dispatcher.add("before_tool", second, "ext_a")
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={"command": "ls"}))
    assert seen == ["first", "second"]


def test_blocked_gate_short_circuits_remaining_hooks():
    calls = []

    def later(ctx):
        calls.append("later")

    def gate(ctx):
        ctx.blocked = True
        ctx.block_reason = "denied"

    dispatcher.add("before_tool", gate, "builtin:permission", stage="gate", builtin=True)
    dispatcher.add("before_tool", later, "builtin:late", stage="gate", builtin=True)
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.blocked and calls == []


def test_abort_short_circuits_remaining_hooks():
    calls = []

    def aborter(ctx):
        ctx.abort("stop")

    dispatcher.add("before_llm", aborter, "ext_a")
    dispatcher.add("before_llm", lambda ctx: calls.append("later"), "ext_a")
    ctx = BeforeLLMContext(messages=[], tools=[], max_tokens=1)
    _run("before_llm", ctx)
    assert ctx.aborted and calls == []


def test_successful_hook_logs_debug(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)

    def hook(ctx):
        pass

    dispatcher.add("before_llm", hook, "ext_a")
    _run("before_llm", BeforeLLMContext(messages=[], tools=[], max_tokens=1))
    logger.debug.assert_called_once_with("[Extension] ext_a:hook 执行成功 at before_llm")


def test_failed_hook_does_not_log_success(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)

    def broken(ctx):
        raise RuntimeError("boom")

    dispatcher.add("before_tool", broken, "ext_a")
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert not logger.debug.called
    assert logger.exception.called


def test_async_hook_is_awaited():
    seen = []

    async def hook(ctx):
        seen.append("async")

    dispatcher.add("before_llm", hook, "ext_a")
    _run("before_llm", BeforeLLMContext(messages=[], tools=[], max_tokens=1))
    assert seen == ["async"]


def test_hook_exception_is_fail_open(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)
    calls = []

    def broken(ctx):
        raise RuntimeError("boom")

    dispatcher.add("before_tool", broken, "ext_a")
    dispatcher.add("before_tool", lambda ctx: calls.append("next"), "ext_b")
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert calls == ["next"]
    assert logger.exception.called


def test_exception_rolls_back_uncommitted_votes(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)
    calls = []

    def broken(ctx):
        ctx.decide("allow", "x")
        raise RuntimeError("boom")

    dispatcher.add("before_tool", broken, "ext_a")
    dispatcher.add("before_tool", lambda ctx: calls.append("next"), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.decisions == []
    assert calls == ["next"]
    assert logger.exception.called


def test_exception_rollback_keeps_earlier_committed_votes():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("deny", "earlier"), "ext_a")

    def broken(ctx):
        ctx.decide("allow", "late")
        raise RuntimeError("boom")

    dispatcher.add("before_tool", broken, "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert [(d.action, d.source) for d in ctx.decisions] == [("deny", "ext_a")]


def test_cancelled_error_propagates():
    async def cancelled(ctx):
        raise asyncio.CancelledError

    dispatcher.add("before_tool", cancelled, "ext_a")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dispatcher.dispatch(
            "before_tool", BeforeToolContext(tool_name="terminal", args={})))


def test_non_list_inject_messages_is_dropped(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)

    dispatcher.add("after_tool", lambda ctx: setattr(ctx, "inject_messages", "oops"), "ext_a")
    ctx = AfterToolContext(tool_name="terminal", args={}, result="out")
    _run("after_tool", ctx)
    assert ctx.inject_messages == []
    assert logger.error.called


def test_non_list_decisions_is_dropped(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)

    dispatcher.add("before_tool", lambda ctx: setattr(ctx, "decisions", "oops"), "ext_a")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.decisions == []
    assert logger.error.called


def test_result_is_normalized_and_none_content_preserved():
    dispatcher.add("after_tool", lambda ctx: setattr(ctx, "result", 42), "ext_a")
    ctx = AfterToolContext(tool_name="terminal", args={}, result="out")
    _run("after_tool", ctx)
    assert ctx.result == "42"

    dispatcher.add("after_llm", lambda ctx: setattr(ctx, "content", None), "ext_a")
    llm_ctx = AfterLLMContext(content="x", tool_calls=[])
    _run("after_llm", llm_ctx)
    assert llm_ctx.content is None


def test_handled_short_circuits_remaining_hooks():
    calls = []

    def handles(ctx):
        ctx.handle("taken")

    dispatcher.add("tool_execute", handles, "ext_a")
    dispatcher.add("tool_execute", lambda ctx: calls.append("later"), "ext_a")
    ctx = ToolExecuteContext(tool_name="terminal", args={})
    _run("tool_execute", ctx)
    assert ctx.handled is True
    assert calls == []


def test_unhandled_runs_later_hooks():
    calls = []

    dispatcher.add("tool_execute", lambda ctx: None, "ext_a")
    dispatcher.add("tool_execute", lambda ctx: calls.append("later"), "ext_a")
    _run("tool_execute", ToolExecuteContext(tool_name="terminal", args={}))
    assert calls == ["later"]


def test_decide_collects_votes_without_blocking():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow", "trusted"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: ctx.decide("deny", "no"), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    _run("before_tool", ctx)
    assert [(d.action, d.source) for d in ctx.decisions] == [("allow", "ext_a"), ("deny", "ext_b")]
    assert ctx.blocked is False


def test_same_callback_keeps_only_last_vote():
    def hook(ctx):
        ctx.decide("ask", "first")
        ctx.decide("allow", "final")
    dispatcher.add("before_tool", hook, "ext_a")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert [d.action for d in ctx.decisions] == ["allow"]


def test_vote_dropped_when_later_callback_rewrites_args():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow", "looks safe"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: ctx.args.update({"command": "git push --force"}), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git status"})
    _run("before_tool", ctx)
    assert ctx.decisions == []


def test_vote_kept_when_callback_rewrites_before_deciding():
    def hook(ctx):
        ctx.args["command"] = "git status"
        ctx.decide("allow", "normalized")
    dispatcher.add("before_tool", hook, "ext_a")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git status "})
    _run("before_tool", ctx)
    assert [d.action for d in ctx.decisions] == ["allow"]


def test_stale_votes_dropped_before_gate_stage_runs():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: ctx.args.update({"command": "rm x"}), "ext_b")
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append(list(ctx.decisions)),
                   "builtin:permission", stage="gate", fail_closed=True, builtin=True)
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    _run("before_tool", ctx)
    assert seen == [[]]


def test_stage_order_is_user_then_observer_then_gate():
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append("gate"),
                   "builtin:permission", stage="gate", builtin=True)
    dispatcher.add("before_tool", lambda ctx: seen.append("user"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: seen.append("observer"),
                   "builtin:observers", stage="observer", builtin=True)
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert seen == ["user", "observer", "gate"]


def test_add_rejects_non_user_stage_without_builtin():
    with pytest.raises(ValueError, match="仅内置扩展"):
        dispatcher.add("before_tool", lambda ctx: None, "ext_a", stage="gate")
    with pytest.raises(ValueError, match="仅内置扩展"):
        dispatcher.add("before_tool", lambda ctx: None, "ext_a", stage="observer")


def test_add_rejects_unknown_event_and_non_callable():
    with pytest.raises(ValueError, match="未知扩展事件"):
        dispatcher.add("never", lambda ctx: None, "ext_a")
    with pytest.raises(TypeError, match="必须可调用"):
        dispatcher.add("before_llm", "not-callable", "ext_a")


def test_include_external_false_skips_external_but_runs_builtin():
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append("external"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: seen.append("builtin"),
                   "builtin:observers", stage="observer", builtin=True)
    _run_external_off("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert seen == ["builtin"]


def test_fail_closed_blocks_on_exception():
    def broken(ctx):
        raise RuntimeError("boom")
    dispatcher.add("before_tool", broken, "builtin:permission",
                   stage="gate", fail_closed=True, builtin=True)
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.blocked is True
    assert ctx.block_reason == "Permission check failed: RuntimeError"


def test_decide_rejects_unknown_action():
    ctx = BeforeToolContext(tool_name="terminal", args={})
    with pytest.raises(ValueError):
        ctx.decide("approve")
