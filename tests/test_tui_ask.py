"""待答请求状态机（AskState）：纯测试，不启动 Textual。"""
from concurrent.futures import Future

from core.tui.ask import (
    CLARIFY_CANCELLED,
    CLARIFY_NO_SELECTION,
    PERMISSION_DENIED,
    AskKind,
    AskRequest,
    AskState,
)


def _state_with(request: AskRequest) -> tuple[AskState, Future]:
    state, future = AskState(), Future()
    assert state.open(request, future)
    return state, future


def test_at_most_one_pending_ask():
    state, future = _state_with(AskRequest(AskKind.PERMISSION))
    assert not state.open(AskRequest(AskKind.CLARIFY, options=("A",)), Future()), \
        "已有挂起请求时拒绝"
    assert state.view().request.kind is AskKind.PERMISSION
    assert state.cancel() and future.result(timeout=0) == PERMISSION_DENIED
    assert state.view() is None


def test_permission_answer_requires_yes_or_no_and_is_idempotent():
    state, future = _state_with(AskRequest(AskKind.PERMISSION))
    assert not state.answer("maybe"), "非法答案不落地"
    assert not future.done()
    assert state.answer("yes")
    assert future.result(timeout=0) == "yes"
    assert not state.answer("no"), "已关闭后再次作答是 no-op"


def test_clarify_single_uses_option_text_and_other_mode_uses_raw_text():
    state, future = _state_with(AskRequest(AskKind.CLARIFY, options=("A", "B")))
    assert state.answer("B") and future.result(timeout=0) == "B"

    state, future = _state_with(AskRequest(AskKind.CLARIFY, options=("A", "B")))
    assert state.set_other_mode(True)
    assert state.view().other_mode
    assert state.answer("custom") and future.result(timeout=0) == "custom"


def test_clarify_multi_accumulates_and_composes():
    state, future = _state_with(AskRequest(AskKind.CLARIFY, options=("A", "B", "C"), multi=True))
    assert state.toggle(0) and state.toggle(2)
    assert state.view().chosen == frozenset({0, 2})
    assert state.toggle(2) and state.view().chosen == frozenset({0}), "再次 toggle 取消勾选"
    assert state.answer("") and future.result(timeout=0) == "- A"


def test_clarify_multi_with_other_appends_line():
    state, future = _state_with(AskRequest(AskKind.CLARIFY, options=("A", "B"), multi=True))
    state.toggle(1)
    state.set_other_mode(True)
    assert state.answer("mine") and future.result(timeout=0) == "- B\n- mine"


def test_clarify_multi_without_selection_has_placeholder_answer():
    state, future = _state_with(AskRequest(AskKind.CLARIFY, options=("A",), multi=True))
    assert state.answer("") and future.result(timeout=0) == CLARIFY_NO_SELECTION


def test_cancel_policy_per_kind_and_idempotent():
    state, future = _state_with(AskRequest(AskKind.PERMISSION))
    assert state.cancel() and future.result(timeout=0) == PERMISSION_DENIED
    assert not state.cancel(), "无挂起请求时 cancel 是 no-op"

    state, future = _state_with(AskRequest(AskKind.CLARIFY, options=("A",)))
    assert state.cancel() and future.result(timeout=0) == CLARIFY_CANCELLED


def test_toggle_and_other_mode_are_noops_for_wrong_kind():
    state, _future = _state_with(AskRequest(AskKind.PERMISSION))
    assert not state.toggle(0)
    assert not state.set_other_mode(True)
    state.cancel()

    state, _future = _state_with(AskRequest(AskKind.CLARIFY, options=("A",)))
    assert not state.toggle(0), "单选没有勾选状态"
