"""write/edit 工具 diff 只在执行成功时渲染并落 payload——回归保护。

失败（输出以 "[Tool Error]:"/"[Unknown Tool]:" 开头）时 diff 是未落地预览：不渲染、不记录，
回放才不会把未应用改动显示成已应用。"""
import asyncio
import json
from contextlib import nullcontext

import pytest

import core.loop_with_interrupt as lwi
import core.tools.base_tools.diff as git_module
from core.runtime_context import AgentRunContext

DIFF_ARG_KEYS = {
    "write_file": ("path", "content"),
    "edit_file": ("path", "old_text", "new_text"),
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(git_module, "WORKDIR", tmp_path)  # preview_* 基于 git 模块的 WORKDIR 解析
    (tmp_path / "a.py").write_text("old line\n", encoding="utf-8")

    renders, added = [], []

    def fake_add(message):
        added.append(message)

    monkeypatch.setattr(lwi, "render_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(lwi, "render_working_status", lambda *a, **k: nullcontext())
    monkeypatch.setattr(lwi, "render_tool_result_diff", lambda rows: renders.append(rows))
    monkeypatch.setattr(lwi.SESSION_MANAGER, "add_message", fake_add)
    return tmp_path, renders, added


def _handler(output: str):
    async def run(**_):
        return output

    return run


def _run(ctx, tool_name, args, handler):
    tmp_path, renders, added = ctx
    tool_call = {
        "id": "call_1", "type": "function",
        "function": {"name": tool_name, "arguments": json.dumps(args)},
    }
    runtime = lwi.AgentRuntime.__new__(lwi.AgentRuntime)  # 跳过 __init__：不跑 run()，无需事件循环线程
    asyncio.run(runtime.call_tools([tool_call], {tool_name: handler}, AgentRunContext()))
    return renders, added


def test_success_write_payload_and_diff(ctx):
    renders, added = _run(ctx, "write_file", {"path": "a.py", "content": "new line\n"},
                          _handler("Wrote 42 bytes to a.py."))
    assert renders, "成功时应渲染 diff"
    assert added[-1]["payload"] == renders[0], "payload 应与渲染的 diff 行一致"


def test_short_success_output_still_renders(ctx):
    """回归：旧守卫 output not in output[:20] 会吞掉 20 字符以内的成功输出。"""
    renders, added = _run(ctx, "edit_file", {"path": "a.py", "old_text": "old line", "new_text": "new line"},
                          _handler("Edited successfully."))
    assert renders, "短成功输出也必须渲染 diff"
    assert "payload" in added[-1]


def test_failure_renders_nothing_and_no_payload(ctx):
    renders, added = _run(ctx, "write_file", {"path": "a.py", "content": "new line\n"},
                          _handler("[Tool Error]: boom"))
    assert not renders, "失败时不渲染 diff"
    assert "payload" not in added[-1], "失败时不得落 payload"


def test_failure_with_long_error_no_payload(ctx):
    """回归：旧守卫对长错误（>20 字符）会照常渲染。"""
    renders, added = _run(ctx, "edit_file", {"path": "a.py", "old_text": "missing", "new_text": "x"},
                          _handler("[Tool Error]: text not found in /very/long/path/a.py"))
    assert not renders
    assert "payload" not in added[-1]


def test_unknown_tool_renders_nothing(ctx):
    renders, added = _run(ctx, "edit_file", {"path": "a.py", "old_text": "missing", "new_text": "x"},
                          _handler("[Unknown Tool]: nope"))
    assert not renders
    assert "payload" not in added[-1]
