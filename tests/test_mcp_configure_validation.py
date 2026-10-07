"""configure_mcp_server 校验须与 _server_params 支持的传输形态一致：
stdio（command，可选 args / env / cwd）与 url（type 为 sse / streamable_http，可选 headers）放行，
其余形态拒绝且不落盘。"""
import json

from mcp import StdioServerParameters
from mcp.client.session_group import SseServerParameters, StreamableHttpParameters

import core.mcp.mcp_client as mcp_client


def _configure(monkeypatch, tmp_path, name, config):
    config_file = tmp_path / ".mcp.json"
    config_file.write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch.setattr(mcp_client, "MCP_CONFIG_FILE", config_file)
    # 只测校验与落盘：别让 configure 真去建连（会 spawn npx 子进程）
    monkeypatch.setattr(mcp_client, "get_client_manager", lambda: None)
    ok, message = mcp_client.configure_mcp_server(json.dumps({"mcpServers": {name: config}}))
    return ok, message, json.loads(config_file.read_text(encoding="utf-8"))


def test_configure_accepts_stdio_and_url_transports(monkeypatch, tmp_path):
    for name, config in (("stdio", {"command": "npx", "args": ["-y", "pkg"]}),
                         ("sse", {"type": "sse", "url": "http://x"}),
                         ("http", {"type": "streamable_http", "url": "http://x"})):
        ok, message, written = _configure(monkeypatch, tmp_path, name, config)
        assert ok, (name, message)
        assert written["mcpServers"][name] == config


def test_configure_accepts_optional_transport_keys(monkeypatch, tmp_path):
    for name, config in (
            ("stdio-env-cwd", {"command": "npx", "args": ["-y", "pkg"],
                               "env": {"TOKEN": "t"}, "cwd": "/srv"}),
            ("stdio-bare", {"command": "npx"}),
            ("sse-headers", {"type": "sse", "url": "http://x",
                             "headers": {"Authorization": "Bearer t"}, "timeout": 7.5}),
            ("http-headers", {"type": "streamable_http", "url": "http://x",
                              "headers": {"Authorization": "Bearer t"}, "timeout": 12})):
        ok, message, written = _configure(monkeypatch, tmp_path, name, config)
        assert ok, (name, message)
        assert written["mcpServers"][name] == config


def test_server_params_carry_optional_transport_keys():
    stdio = mcp_client.ClientManager._server_params("s", {
        "command": "npx", "args": ["-y", "pkg"], "env": {"TOKEN": "t"}, "cwd": "/srv"})
    assert isinstance(stdio, StdioServerParameters)
    assert (stdio.command, stdio.args, stdio.env, stdio.cwd) == (
        "npx", ["-y", "pkg"], {"TOKEN": "t"}, "/srv")

    for config, expected, expected_timeout in (
            ({"type": "sse", "url": "http://x", "headers": {"Authorization": "Bearer t"},
              "timeout": 7.5}, SseServerParameters, 7.5),
            ({"type": "streamable_http", "url": "http://x", "headers": {"Authorization": "Bearer t"},
              "timeout": 12}, StreamableHttpParameters, 12)):
        params = mcp_client.ClientManager._server_params("s", config)
        assert isinstance(params, expected)
        assert (params.url, params.headers, params.timeout) == (
            "http://x", {"Authorization": "Bearer t"}, expected_timeout)


def test_configure_rejects_invalid_shape_without_writing(monkeypatch, tmp_path):
    for name, config in (("missing-command", {"args": ["-y"]}),
                         ("missing-url", {"type": "sse"}),
                         ("bad-type", {"type": "stdio", "url": "http://x"}),
                         ("unhashable-type", {"type": [], "url": "http://x"}),
                         ("extra-key", {"type": "sse", "url": "http://x", "x": 1})):
        ok, message, written = _configure(monkeypatch, tmp_path, name, config)
        assert not ok and message == "MCP server config invalid", (name, message)
        assert name not in written["mcpServers"], "非法配置不应落盘"
