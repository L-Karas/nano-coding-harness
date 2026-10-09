"""派发器：顺序链式、短路、异步、fail-open、CancelledError 传播、字段归一。"""
import asyncio
from unittest.mock import MagicMock

import pytest

import core.extension.dispatcher as dispatcher
from core.extension.context import AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext


@pytest.fixture(autouse=True)
def _clean_registry():
    dispatcher.reset()
    yield
    dispatcher.reset()


def _run(event, ctx):
    asyncio.run(dispatcher.dispatch(event, ctx))


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


def test_block_short_circuits_remaining_hooks():
    calls = []

    def later(ctx):
        calls.append("later")

    dispatcher.add("before_tool", lambda ctx: ctx.block("denied"), "ext_a")
    dispatcher.add("before_tool", later, "ext_a")
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


def test_result_is_normalized_and_none_content_preserved():
    dispatcher.add("after_tool", lambda ctx: setattr(ctx, "result", 42), "ext_a")
    ctx = AfterToolContext(tool_name="terminal", args={}, result="out")
    _run("after_tool", ctx)
    assert ctx.result == "42"

    dispatcher.add("after_llm", lambda ctx: setattr(ctx, "content", None), "ext_a")
    llm_ctx = AfterLLMContext(content="x", tool_calls=[])
    _run("after_llm", llm_ctx)
    assert llm_ctx.content is None
