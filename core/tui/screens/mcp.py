"""MCP 弹窗：server 列表（/mcp）/ 某 server 的工具列表 / JSON 配置窗。"""

from __future__ import annotations

from rich.text import Text
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
    _error_text,
    _rebuild_options,
)
from core.tui.theme import _SPINNER_FRAMES

# /mcp 配置窗的占位提示（placeholder，空值时显示，非真实输入）：stdio 传输示例
_MCP_CONFIG_EXAMPLE = """{
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
        }
    }
}"""

# /mcp server 行按连接状态着色：connected 绿 ●；connecting 橙色 Braille spinner（_cursor 轮播）；failed 红 ○
_SERVER_STYLE = {"connected": "bold #4ade80", "connecting": "#fb923c", "failed": "#f87171"}
_SERVER_GLYPH = {"connected": "●", "failed": "○"}
_SERVER_DEFAULT_STYLE = "bold #4ade80"


def _server_row(name: str, info: dict, cursor: int = 0) -> Text:
    """server 行 = 「状态字形 名称」：绿 ●（connected）/ 橙色 spinner（connecting）/ 红 ○（failed）。"""
    status = info.get("status", "")
    glyph = (_SPINNER_FRAMES[cursor % len(_SPINNER_FRAMES)] if status == "connecting"
             else _SERVER_GLYPH.get(status, "●"))
    return Text(f"{glyph} {name}", style=_SERVER_STYLE.get(status, _SERVER_DEFAULT_STYLE))


class MCPServersScreen(_InlineConfirm, _ListPickerScreen):
    """MCP 服务器列表弹窗（/mcp）：OptionList 展示 core.mcp.mcp_client.get_mcp_server_list()
    返回的已配置 server 名（↑/↓ 原生首尾循环；行按连接状态着色：connected 绿 ●、connecting
    橙色 spinner 轮播、failed 红 ○）。

    Enter/点击选中 → 推入 MCPToolsScreen 展示该 server 的工具（行格式同 /skills 弹窗）；
    Insert 弹 MCPConfigScreen（JSON 配置，保存成功后就地刷新列表、本窗保持打开；
    servers 为空也可打开本窗，靠 Insert 配第一个 server）；
    Delete → 窗内底部红字原地确认后经
    core.mcp.mcp_client.unconfigure_mcp_server 删除（删空则关窗）；Esc 撤销确认 / 关闭。
    servers 由调用方从 get_mcp_server_list() 取值：
    {server_name: {"status": connecting|connected|failed, "tools": [{tool_name, tool_description}]}}。"""

    TITLE = "MCP Servers"
    HINT = "  ↑/↓ browse    Enter tools    Insert configure    Delete remove    Esc close"
    LIST_ID = "mcp-list"
    PICKER_ID = "mcp-picker"
    CONFIRM = True
    BINDINGS = [("insert", "configure", "Configure"),
                ("delete", "remove_selected", "Delete")]

    def __init__(self, servers: dict[str, dict]) -> None:
        super().__init__()
        # {server_name: {"status": ..., "tools": [...]}} 快照（1s 轮询状态；删除只改本地副本）
        self._servers = servers
        self._cursor = 0  # connecting 行 spinner 帧下标
        self._anim = None  # spinner interval（有 connecting 行时惰性启动）

    def on_mount(self) -> None:
        # 不调 super().on_mount()：Textual 按 MRO 自动派发各级 on_mount（_ListPickerScreen 的
        # _reload/focus 也会被调到），显式再调会 _reload 两次（空列表时双 Toast）
        # 连接在后台任务里推进：窗开着时轻量轮询，连接完成/失败自动反映到行
        self.set_interval(1.0, self._poll_status)

    def _refresh(self) -> None:
        """重取快照并就地重建；取数失败时保留旧列表，不留空窗。空快照（未配置 server）
        也照常重建，空窗仍可 Insert 配置（Toast 提示）。"""
        try:
            from core.mcp.mcp_client import get_mcp_server_list
            servers = get_mcp_server_list()
        except Exception:
            return
        if servers != self._servers:
            self._servers = servers
            self._reload()

    def _poll_status(self) -> None:
        """后台建连/断开是异步的，定期刷新状态；原地确认中不重建（重建会撤销确认）。"""
        if self._pending is None:
            self._refresh()

    def _reload(self) -> None:
        rows = [Option(_server_row(server, info, self._cursor), id=server)
                for server, info in self._servers.items()]
        _rebuild_options(self._list(), rows)
        if not rows:  # 空列表无行可看：走 Toast 提示去 Insert 配 server（提示行保持通用文案）
            self.app.notify("No MCP servers configured: press Insert to add one", title="⚠️ MCP Servers")
        self._sync_anim()

    def _sync_anim(self) -> None:
        """有 connecting 行时惰性启 0.1s spinner 轮播；全部落定即停并复位帧（空闲不空转重绘）。"""
        connecting = any(info.get("status") == "connecting" for info in self._servers.values())
        if connecting and self._anim is None:
            self._anim = self.set_interval(0.1, self._tick)
        elif not connecting and self._anim is not None:
            self._anim.stop()
            self._anim = None
            self._cursor = 0

    def _tick(self) -> None:
        """轮播一帧：只就地替换 connecting 行的 prompt（不重建列表，高亮 / 原地确认不受影响）。"""
        self._cursor += 1
        olist = self._list()
        for server, info in self._servers.items():
            if info.get("status") == "connecting":
                olist.replace_option_prompt(server, _server_row(server, info, self._cursor))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中 server：再开一层窗展示其工具列表（原地确认中 = 确认删除）。"""
        event.stop()
        if self._run_confirm():
            return
        if event.option_id:
            tools = self._servers.get(event.option_id, {}).get("tools", [])
            self.app.push_screen(MCPToolsScreen(event.option_id, tools))

    def action_remove_selected(self) -> None:
        """Delete：窗内底部红字原地确认；Enter 才真删（确认目标 = 按下 Delete 时高亮的 server）。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        server = str(olist.get_option_at_index(olist.highlighted).id)
        self._ask_confirm(f'Remove "{server}"?', lambda: self._remove_server(server))

    def _remove_server(self, server: str) -> None:
        """unconfigure_mcp_server 只校验落盘并让后台收敛（立即返回），本地删行重建；删空则关窗。"""
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
        """保存成功：仅配置已落盘（连接在后台进行），重取列表就地刷新；本窗不关，可接着配 / 删。"""
        if saved:
            self._refresh()


class MCPToolsScreen(_NamedListScreen):
    """某 MCP server 的工具列表弹窗（MCPServersScreen 选中 server 后打开）：行格式同 /skills
    弹窗（首行「● 工具名」绿色加粗、描述暗灰且自第二行起）；Enter/点击 / Esc 关本层窗回 server
    列表。tools 由调用方从 get_mcp_server_list() 取该 server 的值。"""

    HINT = "  ↑/↓ browse    Enter/Esc close"
    LIST_ID = "mcp-tools-list"

    def __init__(self, server: str, tools: list[dict]) -> None:
        super().__init__([(tool["tool_name"], tool.get("tool_description", "")) for tool in tools])
        self.TITLE = f"{server} tools"  # server 名进标题，行内只有工具


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
        picker = Vertical(
            TextArea(language="json", placeholder=_MCP_CONFIG_EXAMPLE, tab_behavior="indent",
                     id="mcp-config-input"),
            Static("  Ctrl+S save    Esc cancel", classes="picker-hint"),
            classes="picker",
            id="mcp-config-picker",
        )
        picker.border_title = "Configure MCP Servers"
        yield picker

    def on_mount(self) -> None:
        self.query_one("#mcp-config-input", TextArea).focus()

    def action_submit(self) -> None:
        """Ctrl+S：整段 JSON 交 configure_mcp_server（校验 + 落盘，后台收敛连接，立即返回）：
        成功关窗、失败留在窗内可继续改。"""
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
