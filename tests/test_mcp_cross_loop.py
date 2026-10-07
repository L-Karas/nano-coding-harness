"""MCP 工具调用必须落到 MCP 后台 loop：session_group 是在 _get_loop() 上建连的，
在 agent 自己的 loop 里直接 await 会永远挂住（anyio/asyncio 原语不跨 loop 唤醒）。"""
import asyncio
from types import SimpleNamespace

import mcp_types

import core.mcp.mcp_client as mcp_client


def test_async_tool_call_runs_on_mcp_loop():
    manager = mcp_client.ClientManager({"mcpServers": {}})
    calls = []

    async def call_tool(name, args):
        calls.append((name, args, asyncio.get_running_loop()))
        return SimpleNamespace(content=[mcp_types.TextContent(type="text", text=f"{name}:{args}")])

    manager.tool_handlers["t"] = lambda **kwargs: "sync"
    manager.session_group = SimpleNamespace(call_tool=call_tool)

    # 独立 loop 里调用 = 模拟 agent 轮次的 loop（AgentRuntime.loop）
    result = asyncio.run(manager.tool_call_async("t", None, {"a": 1}))

    assert result == "t:{'a': 1}", "文本内容应被提取"
    assert calls == [("t", {"a": 1}, mcp_client._get_loop())], "call_tool 必须调度到 MCP loop 上执行"


def test_async_tool_call_propagates_cancel():
    """Esc 中断：等待中的回合被取消不能挂在 MCP 调用上。"""
    manager = mcp_client.ClientManager({"mcpServers": {}})
    manager.tool_handlers["t"] = lambda **kwargs: "sync"

    async def call_tool(name, args):  # 永不返回（MCP server 无响应）
        await asyncio.Event().wait()

    manager.session_group = SimpleNamespace(call_tool=call_tool)

    async def main():
        task = asyncio.ensure_future(manager.tool_call_async("t", None, {}))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"

    assert asyncio.run(asyncio.wait_for(main(), 5)) == "cancelled"
