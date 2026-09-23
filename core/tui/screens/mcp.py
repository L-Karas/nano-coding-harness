"""MCP 弹窗：server 列表（/mcp）/ 某 server 的工具列表 / JSON 配置窗。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from core.tui.screens.base import (
    _InlineConfirm,
    _ListPickerScreen,
    _NamedListScreen,
    _entry_row,
    _error_text,
    _rebuild_options,
)

# /mcp 配置窗的占位提示（placeholder，空值时显示，非真实输入）：stdio 传输示例
_MCP_CONFIG_EXAMPLE = """{
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
        }
    }
}"""


class MCPServersScreen(_InlineConfirm, _ListPickerScreen):
    """MCP 服务器列表弹窗（/mcp）：OptionList 只展示 core.mcp.mcp_client.get_mcp_server_list()
    返回的已配置 server 名（↑/↓ 原生首尾循环，行 = 「● server 名」绿色加粗）。

    Enter/点击选中 → 推入 MCPToolsScreen 展示该 server 的工具（行格式同 /skills 弹窗）；
    Insert 弹 MCPConfigScreen（JSON 配置，保存成功后就地刷新列表、本窗保持打开）；
    Delete → 窗内底部红字原地确认后经
    core.mcp.mcp_client.unconfigure_mcp_server 删除（删空则关窗）；Esc 撤销确认 / 关闭。
    servers 由调用方从 get_mcp_server_list() 取值：{server_name: [{tool_name, tool_description}]}。"""

    TITLE = "🔌 MCP Servers"
    HINT = "  ↑/↓ browse    Enter tools    Insert configure    Delete remove    Esc close"
    LIST_ID = "mcp-list"
    PICKER_ID = "mcp-picker"
    CONFIRM = True
    BINDINGS = [("insert", "configure", "Configure"),
                ("delete", "remove_selected", "Delete")]

    def __init__(self, servers: dict[str, list[dict]]) -> None:
        super().__init__()
        # {server_name: [{tool_name, tool_description}]} 快照（删除时只改本地副本）
        self._servers = dict(servers)

    def _reload(self) -> None:
        _rebuild_options(self._list(), [Option(_entry_row(server), id=server)
                                        for server in self._servers])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中 server：再开一层窗展示其工具列表（原地确认中 = 确认删除）。"""
        event.stop()
        if self._run_confirm():
            return
        if event.option_id:
            self.app.push_screen(MCPToolsScreen(event.option_id, self._servers.get(event.option_id, [])))

    def action_remove_selected(self) -> None:
        """Delete：窗内底部红字原地确认；Enter 才真删（确认目标 = 按下 Delete 时高亮的 server）。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        server = str(olist.get_option_at_index(olist.highlighted).id)
        self._ask_confirm(f'Remove "{server}"?', lambda: self._remove_server(server))

    def _remove_server(self, server: str) -> None:
        """unconfigure_mcp_server 落盘后本地删行重建；删空则关窗（/mcp 无空列表形态）。"""
        try:
            from core.mcp.mcp_client import unconfigure_mcp_server
            ok, message = unconfigure_mcp_server(server)
        except Exception as exc:
            self.app.notify(f"Failed to remove: {_error_text(exc)}", title="⚠️ MCP", severity="error")
            return
        if not ok:
            self.app.notify(message, title="⚠️ MCP", severity="error")
            return
        self.app.notify(message, title="✅ MCP")
        self._servers.pop(server, None)
        if self._servers:
            self._reload()
        else:
            self.dismiss(None)

    def action_configure(self) -> None:
        """Insert：弹 JSON 配置窗；保存成功后本窗保持打开并就地刷新 server 列表。"""
        self.app.push_screen(MCPConfigScreen(), callback=self._on_config_saved)

    def _on_config_saved(self, saved: bool) -> None:
        """保存成功：重取 server 列表就地重建（本窗不关，可接着配 / 删）；取数失败或返回空
        （重建期）时保留旧列表，不留空窗。"""
        if not saved:
            return
        try:
            from core.mcp.mcp_client import get_mcp_server_list
            servers = get_mcp_server_list()
        except Exception:
            return
        if servers:
            self._servers = servers
            self._reload()


class MCPToolsScreen(_NamedListScreen):
    """某 MCP server 的工具列表弹窗（MCPServersScreen 选中 server 后打开）：行格式同 /skills
    弹窗（首行「● 工具名」绿色加粗、描述暗灰且自第二行起）；Enter/点击 / Esc 关本层窗回 server
    列表。tools 由调用方从 get_mcp_server_list() 取该 server 的值。"""

    HINT = "  ↑/↓ browse    Enter/Esc close"
    LIST_ID = "mcp-tools-list"

    def __init__(self, server: str, tools: list[dict]) -> None:
        super().__init__([(tool["tool_name"], tool.get("tool_description", "")) for tool in tools])
        self.TITLE = f"🔧 {server} tools"  # server 名进标题，行内只有工具


class MCPConfigScreen(ModalScreen[bool]):
    """MCP server 配置弹窗（MCPServersScreen 内按 Insert 打开）：JSON TextArea（language="json"，
    空值 + 示例配置作占位提示、非真实输入，Tab 缩进、Enter 换行），Ctrl+S 把整段 JSON 交
    core.mcp.mcp_client.configure_mcp_server 校验并落盘：成功 notify + 关窗返回 True；
    失败 notify（含校验错误）留在窗内可继续改；Esc 取消返回 False。"""

    BINDINGS = [
        # priority：配置框（TextArea，tab_behavior="indent"）会吞掉 Esc 改焦点，
        # 必须在焦点部件拿到键之前拦截
        Binding("escape", "cancel", "Cancel", priority=True),
        ("ctrl+s", "submit", "Save"),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("🔌 Configure MCP Servers (JSON)", classes="picker-title"),
            TextArea(language="json", placeholder=_MCP_CONFIG_EXAMPLE, tab_behavior="indent",
                     id="mcp-config-input"),
            Static("  Ctrl+S save    Esc cancel", classes="picker-hint"),
            classes="picker",
            id="mcp-config-picker",
        )

    def on_mount(self) -> None:
        self.query_one("#mcp-config-input", TextArea).focus()

    def action_submit(self) -> None:
        """Ctrl+S：整段 JSON 交 configure_mcp_server；成功关窗，失败留在窗内可继续改。"""
        text = self.query_one("#mcp-config-input", TextArea).text
        try:
            from core.mcp.mcp_client import configure_mcp_server
            ok, message = configure_mcp_server(text)
        except Exception as exc:
            self.app.notify(f"Failed to configure: {_error_text(exc)}", title="⚠️ MCP", severity="error")
            return
        if not ok:
            self.app.notify(message, title="⚠️ MCP", severity="error")
            return
        self.app.notify(message, title="✅ MCP")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
