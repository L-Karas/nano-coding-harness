"""`/mcp` 窗口 Ctrl+E：切换 server 启用状态；禁用 server Enter 不打开工具列表。"""
import asyncio

import pytest

import core.mcp.mcp_client as mcp_client
from core.tui.screens import MCPServersScreen, MCPToolsScreen
from tests.support import chat_app, option_texts


@pytest.fixture
def state(monkeypatch):
    servers = {"demo": {"status": "connected", "tools": [{"tool_name": "t"}]}}
    calls: list[tuple[str, bool]] = []

    def _set_enabled(name: str, value: bool) -> tuple[bool, str]:
        calls.append((name, value))
        servers[name] = {
            "status": "connected" if value else "disabled",
            "tools": [{"tool_name": "t"}] if value else [],
        }
        action = "Enabled" if value else "Disabled"
        return True, f"{action} MCP server '{name}'"

    def _snapshot():
        return {name: {"status": info["status"], "tools": list(info["tools"])}
                for name, info in servers.items()}

    monkeypatch.setattr(mcp_client, "set_mcp_server_enabled", _set_enabled)
    monkeypatch.setattr(mcp_client, "get_mcp_server_list", _snapshot)
    return servers, calls


def test_ctrl_e_toggles_and_disabled_enter_does_not_open_tools(state):
    servers, calls = state

    async def main():
        async with chat_app() as (app, pilot):
            app.push_screen(MCPServersScreen(dict(servers)))
            await pilot.pause()

            await pilot.press("ctrl+e")
            await pilot.pause()
            assert calls == [("demo", False)]
            assert app.screen_stack[-1]._servers["demo"]["status"] == "disabled"
            assert isinstance(app.screen_stack[-1], MCPServersScreen)
            olist = app.screen_stack[-1]._list()
            rows = option_texts(olist)
            assert rows == ["○ demo (disabled)"], rows

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], MCPServersScreen), \
                "禁用 server Enter 不应打开工具列表"

            await pilot.press("ctrl+e")
            await pilot.pause()
            assert calls[-1] == ("demo", True)
            assert app.screen_stack[-1]._servers["demo"]["status"] == "connected"
            rows = option_texts(olist)
            assert rows == ["● demo"], rows
    asyncio.run(main())


def test_failed_server_enter_does_not_open_tools(monkeypatch):
    servers = {"demo": {"status": "failed", "tools": [{"tool_name": "stale"}]}}
    monkeypatch.setattr(mcp_client, "get_mcp_server_list", lambda: servers)

    async def main():
        async with chat_app() as (app, pilot):
            app.push_screen(MCPServersScreen(dict(servers)))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], MCPServersScreen)
            assert not any(isinstance(screen, MCPToolsScreen)
                           for screen in app.screen_stack), "失败 server 不应打开工具列表"
            toasts = [n.message for n in app._notifications]
            assert any("failed to connect" in message for message in toasts), toasts
    asyncio.run(main())
