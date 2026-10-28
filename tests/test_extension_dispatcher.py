"""派发器：顺序链式、短路、异步、fail-open、CancelledError 传播、字段归一、模块开关。"""
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
        ctx._set_blocked("denied")

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


def test_decisions_assignment_is_rejected_without_breaking_dispatch(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)
    calls = []

    dispatcher.add("before_tool", lambda ctx: setattr(ctx, "decisions", "oops"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: calls.append("next"), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.decisions == []
    assert calls == ["next"]
    assert logger.exception.called


def test_invalid_vote_element_is_discarded_without_crashing_dispatch(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dispatcher, "_LOGGER", logger)
    calls = []

    def broken(ctx):
        ctx.decisions.append("allow")

    dispatcher.add("before_tool", broken, "ext_a")
    dispatcher.add("before_tool", lambda ctx: calls.append("next"), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.decisions == []
    assert calls == ["next"]
    assert logger.exception.called


def test_foreign_stop_field_cannot_skip_gate():
    seen = []

    def sneak(ctx):
        ctx.aborted = True  # 外来字段：上下文没有这个字段，slots 会拒绝

    def gate(ctx):
        seen.append(ctx.tool_name)

    dispatcher.add("before_tool", sneak, "ext_a")
    dispatcher.add("before_tool", gate, "builtin:permission",
                   stage="gate", builtin=True)
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    _run("before_tool", ctx)
    assert seen == ["terminal"]


def test_read_only_tool_fact_cannot_be_rewritten():
    seen = []

    def rewrite(ctx):
        ctx.tool_name = "read_file"

    def gate(ctx):
        seen.append(ctx.tool_name)

    dispatcher.add("before_tool", rewrite, "ext_a")
    dispatcher.add("before_tool", gate, "builtin:permission",
                   stage="gate", builtin=True)
    _run("before_tool", BeforeToolContext(
        tool_name="terminal", args={"command": "sudo ls"}))
    assert seen == ["terminal"]


def test_dispatch_rejects_unknown_event_and_wrong_context():
    with pytest.raises(ValueError, match="未知扩展事件"):
        _run("typo", BeforeToolContext(tool_name="terminal", args={}))
    with pytest.raises(TypeError, match="需要 BeforeToolContext"):
        _run("before_tool", AfterToolContext(tool_name="terminal", args={}, result="out"))


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


def test_disabled_module_is_skipped_until_reenabled():
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append("ext"), "ext_a")
    dispatcher._set_module_enabled("ext_a", False)
    assert dispatcher.is_module_enabled("ext_a") is False

    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert seen == []

    dispatcher._set_module_enabled("ext_a", True)
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert seen == ["ext"]


def test_disabled_module_skips_all_its_events():
    seen = []
    for event in ("before_llm", "after_tool"):
        dispatcher.add(event, lambda ctx, name=event: seen.append(name), "ext_a")
    dispatcher._set_module_enabled("ext_a", False)

    _run("before_llm", BeforeLLMContext(messages=[], tools=[], max_tokens=1))
    _run("after_tool", AfterToolContext(tool_name="terminal", args={}, result="out"))
    assert seen == []


def test_builtin_runs_even_if_its_module_flag_is_disabled():
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append("builtin"),
                   "builtin:permission", stage="gate", builtin=True)
    dispatcher._set_module_enabled("builtin:permission", False)

    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))

    assert seen == ["builtin"], "内置模块不受扩展派发开关影响"


def test_reset_clears_module_flags():
    dispatcher.add("before_tool", lambda ctx: None, "ext_a")
    dispatcher._set_module_enabled("ext_a", False)
    dispatcher.reset()
    assert dispatcher.is_module_enabled("ext_a") is True
    assert dispatcher.describe_handlers("before_tool") == ()


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


def test_add_many_is_atomic_when_a_registration_is_invalid():
    from core.extension.protocol import Registration

    with pytest.raises(ValueError, match="未知扩展事件"):
        dispatcher.add_many("ext_a", [
            Registration("before_llm", lambda ctx: None),
            Registration("never", lambda ctx: None),
        ])
    assert dispatcher.describe_handlers("before_llm") == ()


def test_describe_handlers_exposes_metadata_without_callbacks():
    dispatcher.add("before_llm", lambda ctx: None, "ext_a")

    assert dispatcher.describe_handlers("before_llm") == (
        dispatcher.HandlerInfo("ext_a", "<lambda>", "user", False, False),
    )
    with pytest.raises(ValueError, match="未知扩展事件"):
        dispatcher.describe_handlers("never")


def test_stale_votes_dropped_before_each_gate_handler():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow"), "ext_a")

    def mutate(ctx):
        ctx.args["command"] = "rm -rf build"

    dispatcher.add("before_tool", mutate, "builtin:mutator", stage="gate", builtin=True)
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append(list(ctx.decisions)),
                   "builtin:second", stage="gate", builtin=True)

    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    _run("before_tool", ctx)
    assert seen == [[]], "前一个 gate 回调改写参数后，后一个 gate 不应看到旧票"


def test_deleted_writable_field_does_not_break_normalize():
    def delete_result(ctx):
        del ctx.result

    dispatcher.add("after_tool", delete_result, "ext_a")
    ctx = AfterToolContext(tool_name="terminal", args={}, result="out")
    _run("after_tool", ctx)
    assert not hasattr(ctx, "result")
