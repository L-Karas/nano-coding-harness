"""MCP server 启用状态：落盘表示、/mcp 快照与 ClientManager 配置迁移。"""
import asyncio
import json
from types import SimpleNamespace

import pytest

import core.mcp.mcp_client as mcp_client


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {
        "on": {"command": "x"},
        "off": {"command": "y", "enabled": False},
    }}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "MCP_CONFIG_FILE", path)
    monkeypatch.setattr(mcp_client, "_reload_config_in_background", lambda: None)
    return path


def test_set_enabled_writes_false_and_enable_removes_key(config_file):
    ok, message = mcp_client.set_mcp_server_enabled("on", False)
    assert ok and "Disabled" in message
    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["mcpServers"]["on"] == {"command": "x", "enabled": False}

    ok, message = mcp_client.set_mcp_server_enabled("on", True)
    assert ok and "Enabled" in message
    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["mcpServers"]["on"] == {"command": "x"}


def test_set_enabled_rejects_unknown_server(config_file):
    ok, message = mcp_client.set_mcp_server_enabled("missing", False)
    assert not ok and "not found" in message


def test_get_server_list_reports_disabled(config_file, monkeypatch):
    manager = SimpleNamespace(
        status={"on": "connected"},
        tools_by_server={"on": [{"tool_name": "t", "tool_description": "d"}]},
    )
    monkeypatch.setattr(mcp_client, "peek_client_manager", lambda: manager)

    assert mcp_client.get_mcp_server_list() == {
        "on": {"status": "connected", "tools": [{"tool_name": "t", "tool_description": "d"}]},
        "off": {"status": "disabled", "tools": []},
    }


def test_apply_config_disables_then_enables(monkeypatch):
    manager = mcp_client.ClientManager({"mcpServers": {"s": {"command": "x"}}})
    commands = asyncio.Queue()
    manager._server_commands["s"] = commands
    manager.status["s"] = "connected"
    started = []
    monkeypatch.setattr(manager, "_start_server_task",
                        lambda name, config: started.append((name, config)) or SimpleNamespace())

    manager._apply_config({"mcpServers": {"s": {"command": "x", "enabled": False}}})

    assert commands.get_nowait() is mcp_client._STOP
    assert manager._server_commands["s"] is commands, "旧 task 还在退出，队列不能提前删"
    assert manager.status == {}
    assert manager.server_configs == {"mcpServers": {"s": {"command": "x", "enabled": False}}}

    manager._apply_config({"mcpServers": {"s": {"command": "y"}}})

    assert commands.get_nowait() == {"command": "y"}, "重新启用应复用同一个 task"
    assert started == [], "不应为同一个 server 启动第二个 task"


def test_manage_server_coalesces_stop_and_reenable(monkeypatch):
    """禁用后立刻启用：同一个常驻 task 先断旧、再按最新配置重连。"""
    manager = mcp_client.ClientManager({"mcpServers": {}})
    commands = asyncio.Queue()
    ready = asyncio.Event()
    connects, disconnects = [], []

    class _Group:
        tools = {}

        async def connect_to_server(self, params):
            connects.append(params.command)
            return SimpleNamespace(server_info=SimpleNamespace(name="s"))

        async def disconnect_from_server(self, session):
            disconnects.append(session)

    manager.session_group = _Group()
    monkeypatch.setattr(manager, "_server_params",
                        lambda name, cfg: SimpleNamespace(command=cfg["command"]))
    commands.put_nowait(mcp_client._STOP)
    commands.put_nowait({"command": "new"})

    async def main():
        task = asyncio.create_task(
            manager._manage_server("s", {"command": "old"}, commands, ready))
        for _ in range(100):
            if len(connects) >= 2:
                break
            await asyncio.sleep(0)
        commands.put_nowait(mcp_client._STOP)
        await task

    asyncio.run(main())

    assert connects == ["old", "new"], connects
    assert len(disconnects) == 2


def test_apply_config_does_not_start_disabled_server(monkeypatch):
    manager = mcp_client.ClientManager({"mcpServers": {}})
    started = []
    monkeypatch.setattr(manager, "_start_server_task",
                        lambda name, config: started.append((name, config)) or SimpleNamespace())

    manager._apply_config({"mcpServers": {"s": {"command": "x", "enabled": False}}})
    manager._apply_config({"mcpServers": {"s": {"command": "y", "enabled": False}}})

    assert started == []
    assert manager.server_configs == {"mcpServers": {"s": {"command": "y", "enabled": False}}}
