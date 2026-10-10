"""write/edit 工具 diff 只在执行成功时渲染并落 payload——回归保护。

失败（ToolResult.is_error=True，来源是工具抛异常）时 diff 是未落地预览：不渲染、不记录，
回放才不会把未应用改动显示成已应用。"""
import asyncio
import json

import pytest

import core.agent_step as step_mod
import core.tools.base_tools.diff as git_module
from core.agent_step import StepPolicy, StepRenderer, run_agent_step
from core.runtime_context import ToolContext
from core.tools import ToolPool
from core.tools.tool_base import BaseTool


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(git_module, "WORKDIR", tmp_path)  # preview_* 基于 git 模块的 WORKDIR 解析
    (tmp_path / "a.py").write_text("old line\n", encoding="utf-8")

    renders = []
    return tmp_path, renders


def _tool(output="", exc=None):
    """测试替身工具：字段覆盖 write/edit 的参数，run 返回或抛异常。"""
    class _Fake(BaseTool):
        path: str = ""
        content: str = ""
        old_text: str = ""
        new_text: str = ""

        def run(self, tctx=None):
            if exc is not None:
                raise exc
            return output

    return _Fake


class _Client:
    current_provider = "test"

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self, async_client=False):
        async def create(**kwargs):
            return object()
        return create


def _run(monkeypatch, fixture, tool_name, args, tool_cls):
    tmp_path, renders = fixture
    pool = ToolPool({} if tool_cls is None else {tool_name: tool_cls})
    responses = [("", "", [{
        "id": "call_1", "type": "function",
        "function": {"name": tool_name, "arguments": json.dumps(args)},
    }], "tool_calls", None)]

    async def fake_stream(stream, on_text=None):
        return responses.pop(0)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    renderer = StepRenderer(on_diff=renders.append)
    outcome = asyncio.run(run_agent_step(
        [], "sys",
        client=_Client(),
        pool=pool,
        tctx=ToolContext(),
        max_tokens=100,
        policy=StepPolicy(use_extensions=False, permission_hooks=False, diff_preview=True),
        renderer=renderer,
    ))
    return outcome, renders


def test_success_write_payload_and_diff(ctx, monkeypatch):
    outcome, renders = _run(monkeypatch, ctx, "write_file", {"path": "a.py", "content": "new line\n"},
                            _tool(output="Wrote 42 bytes to a.py."))
    assert renders, "成功时应渲染 diff"
    assert outcome.followup_messages[-1]["payload"] == renders[0], "payload 应与渲染的 diff 行一致"


def test_short_success_output_still_renders(ctx, monkeypatch):
    """回归：旧守卫 output not in output[:20] 会吞掉 20 字符以内的成功输出。"""
    outcome, renders = _run(monkeypatch, ctx, "edit_file",
                            {"path": "a.py", "old_text": "old line", "new_text": "new line"},
                            _tool(output="Edited successfully."))
    assert renders, "短成功输出也必须渲染 diff"
    assert "payload" in outcome.followup_messages[-1]


def test_failure_renders_nothing_and_no_payload(ctx, monkeypatch):
    outcome, renders = _run(monkeypatch, ctx, "write_file", {"path": "a.py", "content": "new line\n"},
                            _tool(exc=RuntimeError("boom")))
    assert not renders, "失败时不渲染 diff"
    assert "payload" not in outcome.followup_messages[-1], "失败时不得落 payload"


def test_failure_with_long_error_no_payload(ctx, monkeypatch):
    """回归：旧守卫对长错误（>20 字符）会照常渲染。"""
    outcome, renders = _run(monkeypatch, ctx, "edit_file", {"path": "a.py", "old_text": "missing", "new_text": "x"},
                            _tool(exc=RuntimeError("text not found in /very/long/path/a.py")))
    assert not renders
    assert "payload" not in outcome.followup_messages[-1]


def test_unknown_tool_renders_nothing(ctx, monkeypatch):
    outcome, renders = _run(monkeypatch, ctx, "edit_file",
                            {"path": "a.py", "old_text": "missing", "new_text": "x"}, None)
    assert not renders
    assert "payload" not in outcome.followup_messages[-1]


def test_failure_passes_error_flag_to_render(ctx, monkeypatch):
    """实时路径的 is_error 必须来自执行器，而不是回到文本前缀解析。"""
    tmp_path, _ = ctx
    flags = []

    async def fake_stream(stream, on_text=None):
        return ("", "", [{
            "id": "call_1", "type": "function",
            "function": {"name": "write_file", "arguments": json.dumps({"path": "a.py", "content": "x"})},
        }], "tool_calls", None)

    monkeypatch.setattr(step_mod, "streaming_message", fake_stream)
    renderer = StepRenderer(on_tool_result=lambda text, err: flags.append(err))
    asyncio.run(run_agent_step(
        [], "sys", client=_Client(), pool=ToolPool({"write_file": _tool(exc=RuntimeError("boom"))}),
        tctx=ToolContext(), max_tokens=100,
        policy=StepPolicy(use_extensions=False, permission_hooks=False, diff_preview=True),
        renderer=renderer,
    ))
    assert flags == [True]
