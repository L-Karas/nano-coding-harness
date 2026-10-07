"""MCP 配置表单 → server 配置负载：stdio 用 command + args（每行一个元素），
sse / streamable_http 用 url + headers（每行 KEY=VALUE）；缺失必填项或非法行拒绝。"""
import pytest

from core.mcp.mcp_client import _validate_config
from core.tui.screens.mcp import _build_mcp_config


def test_stdio_args_one_per_line():
    payload = _build_mcp_config("fs", "stdio", "npx",
                                "-y\n@modelcontextprotocol/server-filesystem\n.",
                                "", "")
    assert payload == {"mcpServers": {"fs": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}}}


def test_stdio_arg_lines_are_stripped_and_blank_lines_skipped():
    payload = _build_mcp_config("fs", "stdio", "npx", "\n  -y  \n\n  a b \n", "", "")
    assert payload["mcpServers"]["fs"] == {"command": "npx", "args": ["-y", "a b"]}


def test_stdio_empty_args_omitted():
    payload = _build_mcp_config("fs", "stdio", "npx", "\n\n", "", "")
    assert payload["mcpServers"]["fs"] == {"command": "npx"}


def test_url_transports_headers_one_per_line():
    for transport in ("sse", "streamable_http"):
        payload = _build_mcp_config("remote", transport, "", "",
                                    "https://api.test/mcp",
                                    "Authorization=Bearer t\nX-Trace=a=b\n")
        assert payload["mcpServers"]["remote"] == {
            "type": transport,
            "url": "https://api.test/mcp",
            "headers": {"Authorization": "Bearer t", "X-Trace": "a=b"}}


def test_url_transport_empty_headers_omitted():
    payload = _build_mcp_config("remote", "sse", "", "", "https://api.test", "\n")
    assert payload["mcpServers"]["remote"] == {"type": "sse", "url": "https://api.test"}


def test_missing_required_fields_rejected():
    with pytest.raises(ValueError, match="Server name"):
        _build_mcp_config("", "stdio", "npx", "", "", "")
    with pytest.raises(ValueError, match="Transport type"):
        _build_mcp_config("srv", None, "npx", "", "", "")
    with pytest.raises(ValueError, match="Command"):
        _build_mcp_config("srv", "stdio", "", "", "", "")
    with pytest.raises(ValueError, match="URL"):
        _build_mcp_config("srv", "sse", "", "", "", "")
    with pytest.raises(ValueError, match="URL"):
        _build_mcp_config("srv", "streamable_http", "", "", "", "")


def test_malformed_header_lines_rejected_with_line_number():
    with pytest.raises(ValueError, match="line 2"):
        _build_mcp_config("srv", "sse", "", "", "https://x", "ok=1\nbroken\n")
    with pytest.raises(ValueError, match="line 1"):
        _build_mcp_config("srv", "sse", "", "", "https://x", "=no-key\n")


def test_built_payload_passes_mcp_validation():
    """表单产物必须落在 _TRANSPORT_KEYS 允许的形状内（同 configure_mcp_server 的校验）。"""
    for payload in (
            _build_mcp_config("fs", "stdio", "npx", "-y\npkg", "", ""),
            _build_mcp_config("sse", "sse", "", "", "https://x", "A=1"),
            _build_mcp_config("http", "streamable_http", "", "", "https://x", "")):
        _validate_config(payload)  # 不抛即通过
