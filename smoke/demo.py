"""离线演示 Agent：`--smoke` 自检与无模型时的 UI 演示，覆盖流式回复 / 工具卡片 / 右栏数据源 /
权限确认（query 以 "sudo " 开头触发）；真实回合见 core.agent_loop.make_agent_turn。"""

from __future__ import annotations

import time

from core.runtime_state import RUNTIME_STATE, TodoEntry
from core.todo import todo_write
from core.tools import ToolResult
from core.tui.render import (
    ask_permission,
    render_assistant_response,
    render_background_notification,
    render_scope,
    render_thinking_status,
    render_tool_call,
    render_tool_result,
    render_tool_result_diff,
    render_working_status,
    stream_assistant_response,
)


def _demo_agent(query: str) -> None:
    with render_thinking_status():
        time.sleep(0.6)
    chunks = [f"## Answer for: {query}\n",
              "### 1. Textual UI rewrite succeeded\n",
              "### 2. Streaming Markdown cards\n\n```python\nprint('Hello nano-harness')\n```\n",
              "> Demo mode needs no API key — to connect a real agent call `run(handle_query=..., session_manager=...)`\n"
              "Links are clickable: 👉 [Textual documentation](https://textual.textualize.io/)"]
    with render_scope():
        for chunk in chunks:
            time.sleep(0.16)
            stream_assistant_response(chunk)  # 每 chunk 传新增片段（UI 端增量追加，勿传累计全量）
    render_tool_call("terminal", {"command": "ls -la"})
    with render_working_status():
        time.sleep(0.6)
    render_tool_result("total 0\n-rw-r--r-- 1 user user 1234 demo.txt\n(demo output)")
    render_tool_result_diff([(" ", 1, "def main():"), ("-", 2, "    print('old')"),
                             ("+", 2, "    print('new')")])
    # 右栏信息分区演示：写入与真实 Agent 相同的 RuntimeState（ADR-0004），UI 经 1s 轮询上屏，
    # 无需额外渲染调用；todo_write 即 agent 的 todo 工具实现
    todo_write([TodoEntry("Refactor right panel into collapsible sections", "in_progress"),
                TodoEntry("Color-code list rows by status", "completed")])
    bg_id = RUNTIME_STATE.allocate_background_id()
    RUNTIME_STATE.register_background(bg_id, "terminal(command='pip install -r requirements.txt')")
    RUNTIME_STATE.register_subagent("sa-0001", "summarize the asyncio doc", "explore asyncio docs")
    time.sleep(1.6)  # 右栏可见 running 态（1s 轮询粒度）
    todo_write([TodoEntry("Refactor right panel into collapsible sections", "completed")])
    RUNTIME_STATE.complete_background(bg_id, ToolResult(content="(demo finished)"))
    RUNTIME_STATE.update_subagent("sa-0001", "tool", "read_file")
    time.sleep(1.6)  # 右栏可见状态流转（✓ 完成 / 子代理阶段切换）
    RUNTIME_STATE.remove_subagent("sa-0001")
    render_background_notification("Background task bg-0001 finished (demo)")
    if query.startswith("sudo"):
        answer = ask_permission("High-risk command detected — allow execution?", [f"command: {query}"])
        render_assistant_response(f"**Permission**: {'Allowed' if answer.strip().lower() in ('y', 'yes') else 'Denied'}")
