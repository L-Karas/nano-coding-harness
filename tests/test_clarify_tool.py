"""Clarify 选项上限：超过 MAX_OPTIONS 时在工具执行前被参数校验拒绝。"""
import pytest
from pydantic import ValidationError

import core.tools.base_tools.clarify as clarify
from core.template import TOOL_ERROR_PREFIX
from core.tools import ToolPool
from core.tools.base_tools.clarify import MAX_OPTIONS, Clarify


def _make_options(count: int) -> list[str]:
    return [f"option {index}" for index in range(count)]


def test_clarify_accepts_max_options():
    tool = Clarify.model_validate({"options": _make_options(MAX_OPTIONS)})
    assert len(tool.options) == MAX_OPTIONS


def test_clarify_accepts_empty_options():
    assert Clarify.model_validate({"options": []}).options == []


def test_clarify_rejects_more_than_max_options():
    with pytest.raises(ValidationError):
        Clarify.model_validate({"options": _make_options(MAX_OPTIONS + 1)})


def test_tool_pool_rejects_too_many_options_without_asking(monkeypatch):
    calls = []
    monkeypatch.setattr(clarify, "ask_clarify", lambda *args, **kwargs: calls.append(args) or "answer")

    result = ToolPool({"clarify": Clarify}).execute_sync(
        "clarify", {"options": _make_options(MAX_OPTIONS + 1)})

    assert calls == [], "参数校验失败时不得弹出澄清列表"
    assert result.is_error is True
    assert result.content.startswith(TOOL_ERROR_PREFIX)


def test_tool_pool_runs_with_max_options(monkeypatch):
    monkeypatch.setattr(clarify, "ask_clarify", lambda options, multi_select: options[0])

    result = ToolPool({"clarify": Clarify}).execute_sync(
        "clarify", {"options": _make_options(MAX_OPTIONS)})

    assert result.is_error is False
    assert result.content == "option 0"
