"""工具快照：MCP 工具须按配置里的 server 名归组，名字含空格 / 工具名含 "__" 也要对上，
且 list_tools / tool_handlers / get_mcp_server_list 共用同一份快照。"""
import json
from types import SimpleNamespace

import core.mcp.mcp_client as mcp_client


def test_tool_snapshot_groups_by_config_server(monkeypatch, tmp_path):
    config_file = tmp_path / ".mcp.json"
    config_file.write_text(json.dumps({"mcpServers": {"my server": {"command": "x", "args": []}}}),
                           encoding="utf-8")
    monkeypatch.setattr(mcp_client, "MCP_CONFIG_FILE", config_file)

    manager = mcp_client.ClientManager({"mcpServers": {}})
    manager.session_group = SimpleNamespace(tools={
        "mcp__my_server__echo__now": SimpleNamespace(description="echo", input_schema={"type": "object"}),
        "mcp__other__x": SimpleNamespace(description="x", input_schema={}),
    })
    manager._register_session("my server", SimpleNamespace(server_info=SimpleNamespace(name="my server")))

    rows = [{"tool_name": "echo__now", "tool_description": "echo"}]
    assert manager.tools_by_server == {"my server": rows}
    assert set(manager.tool_handlers) == {"mcp__my_server__echo__now"}
    assert [t["function"]["name"] for t in manager.list_tools()] == ["mcp__my_server__echo__now"]

    monkeypatch.setattr(mcp_client, "_ready_manager", manager)
    assert mcp_client.get_mcp_server_list() == {
        "my server": {"status": "connected", "tools": rows}}
