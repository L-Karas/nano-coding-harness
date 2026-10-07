"""MCP 弹窗：server 列表（/mcp）/ 某 server 的工具列表 / 表单配置窗。"""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Select, Static, TextArea
from textual.widgets.option_list import Option

from core.tui.screens.base import (
    _ArrowNav,
    _InlineConfirm,
    _ListPickerScreen,
    _NamedListScreen,
    _error_text,
    _field,
    _rebuild_options,
)
from core.tui.theme import _SPINNER_FRAMES

# server 行按连接状态着色：connected 绿 ●；connecting 橙色 spinner（_cursor 轮播）；failed 红 ○
_SERVER_STYLE = {"connected": "bold #4ade80", "connecting": "#fb923c", "failed": "#f87171"}
_SERVER_GLYPH = {"connected": "●", "failed": "○"}

_HTTP_TRANSPORTS = ("sse", "streamable_http")  # 走 url + headers 的传输类型


def _server_row(name: str, info: dict, cursor: int = 0) -> Text:
    """server 行 = 「状态字形 名称」；未识别状态按 connected 样式。"""
    status = info.get("status", "")
    glyph = (_SPINNER_FRAMES[cursor % len(_SPINNER_FRAMES)] if status == "connecting"
             else _SERVER_GLYPH.get(status, "●"))
    return Text(f"{glyph} {name}", style=_SERVER_STYLE.get(status, _SERVER_STYLE["connected"]))


class MCPServersScreen(_InlineConfirm, _ListPickerScreen):
    """MCP 服务器列表弹窗（/mcp）。

    数据由调用方从 get_mcp_server_list() 取值：{name: {"status": connecting|connected|failed,
    "tools": [...]}}；Enter/点击 → 推入工具列表，Insert → 表单配置窗（保存后本窗就地刷新），
    Delete → 原地确认后 unconfigure（删空也不关窗，空列表可继续 Insert），行按连接状态着色、
    connecting 轮播。"""

    TITLE = "MCP Servers"
    HINT = "  ↑/↓ browse    Enter tools    Insert configure    Delete remove    Esc close"
    LIST_ID = "mcp-list"
    PICKER_ID = "mcp-picker"
    SEARCH_PLACEHOLDER = "Search servers…"
    CONFIRM = True
    # delete 加 priority：搜索栏聚焦时 Delete 仍是窗级删除（Input 默认把 delete 当删字符）
    BINDINGS = [("insert", "configure", "Configure"),
                Binding("delete", "remove_selected", "Delete", priority=True)]

    def __init__(self, servers: dict[str, dict]) -> None:
        super().__init__()
        self._servers = servers  # 快照；状态经 1s 轮询重取，删除只改本地副本
        self._cursor = 0  # connecting 行 spinner 帧下标
        self._anim = None  # spinner interval（有 connecting 行时惰性启动）

    def on_mount(self) -> None:
        # 不调 super().on_mount()：Textual 按 MRO 自动派发各级 on_mount（_ListPickerScreen 的
        # _reload/focus 也会被调到），显式再调会 _reload 两次（空列表时双 Toast）
        self.set_interval(1.0, self._poll_status)

    def _refresh(self) -> None:
        """重取快照并就地重建；取数失败保留旧列表（空快照也照常重建，空窗仍可 Insert）。"""
        try:
            from core.mcp.mcp_client import get_mcp_server_list
            servers = get_mcp_server_list()
        except Exception:
            return
        if servers != self._servers:
            self._servers = servers
            self._reload()

    def _poll_status(self) -> None:
        """后台建连/断开是异步的，定期刷新；原地确认中不重建（重建会撤销确认）。"""
        if self._pending is None:
            self._refresh()

    def _reload(self) -> None:
        rows = [Option(_server_row(server, info, self._cursor), id=server)
                for server, info in self._servers.items()
                if self._match(server)]
        _rebuild_options(self._list(), rows)
        if not self._servers:  # 无 server（而非搜索无匹配）：Toast 提示去 Insert 配
            self.app.notify("No MCP servers configured: press Insert to add one", title="⚠️ MCP Servers")
        self._sync_anim()

    def _sync_anim(self) -> None:
        """有 connecting 行时惰性启 0.1s 轮播；全部落定即停并复位帧（空闲不空转重绘）。"""
        connecting = any(info.get("status") == "connecting" for info in self._servers.values())
        if connecting and self._anim is None:
            self._anim = self.set_interval(0.1, self._tick)
        elif not connecting and self._anim is not None:
            self._anim.stop()
            self._anim = None
            self._cursor = 0

    def _tick(self) -> None:
        """轮播一帧：只就地替换 connecting 行 prompt（不重建列表，高亮 / 原地确认不受影响）；
        被搜索过滤掉的行不在列表里，跳过以免 replace_option_prompt 抛 OptionDoesNotExist。"""
        self._cursor += 1
        olist = self._list()
        visible = {str(option.id) for option in olist.options}
        for server, info in self._servers.items():
            if info.get("status") == "connecting" and server in visible:
                olist.replace_option_prompt(server, _server_row(server, info, self._cursor))

    def _selected(self, option_id: str | None) -> None:
        """Enter/点击 server：打开其工具列表；原地确认中由 _InlineConfirm 先行拦截。"""
        if option_id:
            tools = self._servers.get(option_id, {}).get("tools", [])
            self.app.push_screen(MCPToolsScreen(option_id, tools))

    def action_remove_selected(self) -> None:
        """Delete：窗内红字原地确认；Enter 才真删（确认目标 = 按下 Delete 时高亮的 server）。"""
        olist = self._list()
        if olist.highlighted is None:
            return
        server = str(olist.get_option_at_index(olist.highlighted).id)
        self._ask_confirm(f'Remove "{server}"?', lambda: self._remove_server(server))

    def _remove_server(self, server: str) -> None:
        """unconfigure 立即返回（连接后台收敛），本地删行重建；删空也不关窗（空态可继续 Insert）。"""
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
        self._reload()  # 删空也不关窗：空列表保留 Insert 入口（空态提示由 _reload 给出）

    def action_configure(self) -> None:
        """Insert：弹表单配置窗；保存成功后本窗保持打开并就地刷新。"""
        self.app.push_screen(MCPConfigScreen(), callback=self._on_config_saved)

    def _on_config_saved(self, saved: bool) -> None:
        if saved:
            self._refresh()


class MCPToolsScreen(_NamedListScreen):
    """某 MCP server 的工具列表（行格式同 /skills）；Enter/点击 / Esc 关本层窗。"""

    HINT = "  ↑/↓ browse    Enter/Esc close"
    LIST_ID = "mcp-tools-list"
    SEARCH_PLACEHOLDER = "Search tools…"

    def __init__(self, server: str, tools: list[dict]) -> None:
        super().__init__([(tool["tool_name"], tool.get("tool_description", "")) for tool in tools])
        self.TITLE = f"{server} tools"


def _parse_headers(text: str) -> dict[str, str]:
    """headers 输入区解析：每行 KEY=VALUE（按首个 = 切分，值可含 =）；空行跳过，非法行抛
    ValueError（带行号，消息直接用于窗内提示）。"""
    headers: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"Header line {lineno} must be KEY=VALUE")
        headers[key.strip()] = value.strip()
    return headers


def _build_mcp_config(name: str, transport: str | None, command: str, args_text: str,
                      url: str, headers_text: str) -> dict:
    """把配置表单值组装成 configure_mcp_server 接受的 {"mcpServers": {name: config}}。

    表单单行值已由调用方 strip；args / headers 多行文本在此解析。必填缺失或 headers
    非法时抛 ValueError。
    """
    if not name:
        raise ValueError("Server name is required")
    config: dict
    if transport == "stdio":
        if not command:
            raise ValueError("Command is required for stdio")
        config = {"command": command}
        args = [line.strip() for line in args_text.splitlines() if line.strip()]
        if args:
            config["args"] = args
    elif transport in _HTTP_TRANSPORTS:
        if not url:
            raise ValueError("URL is required")
        config = {"type": transport, "url": url}
        headers = _parse_headers(headers_text)
        if headers:
            config["headers"] = headers
    else:
        raise ValueError("Transport type is required")
    return {"mcpServers": {name: config}}


class MCPConfigScreen(_ArrowNav, ModalScreen[bool]):
    """表单配置窗（MCPServersScreen 内 Insert）：server name + type 下拉；条件字段随 type 切换
    —— stdio 显示 command + args（每行一个 arg），sse / streamable_http 显示 url + headers
    （每行 KEY=VALUE）。Ctrl+S 把组装负载交 configure_mcp_server 校验并落盘，成功关窗返回
    True，失败留在窗内可继续改；Esc 取消返回 False。"""

    BINDINGS = [
        # 不加 priority：type 下拉浮层展开时 Esc 先关浮层（同注册模型窗），再按 Esc 才关窗
        Binding("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Save"),
    ]

    def compose(self) -> ComposeResult:
        picker = Vertical(
            _field(Input(placeholder="Server name", id="mcp-server-name")),
            _field(Select((("stdio", "stdio"),
                           ("streamable http", "streamable_http"),
                           ("sse", "sse")),
                          prompt="Select a transport type", id="mcp-server-type")),
            Vertical(
                _field(Input(placeholder="Command (e.g. npx)", id="mcp-stdio-command")),
                TextArea(placeholder="Args (one per line)", id="mcp-stdio-args"),
                id="mcp-stdio-fields",
            ),
            Vertical(
                _field(Input(placeholder="URL (e.g. https://example.com/mcp)", id="mcp-http-url")),
                TextArea(placeholder="Headers (one KEY=VALUE per line)", id="mcp-http-headers"),
                id="mcp-http-fields",
            ),
            Static("  Tab next field    Ctrl+S save    Esc cancel", classes="picker-hint"),
            classes="picker",
            id="mcp-config-picker",
        )
        picker.border_title = "Configure MCP server"
        yield picker

    def on_mount(self) -> None:
        self.query_one("#mcp-server-name", Input).focus()
        self._sync_transport_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        """type 变化：只显示对应的条件字段组（未选时两组都隐藏）。"""
        event.stop()
        self._sync_transport_fields()

    def _sync_transport_fields(self) -> None:
        transport = self.query_one("#mcp-server-type", Select).selection
        self.query_one("#mcp-stdio-fields").display = transport == "stdio"
        self.query_one("#mcp-http-fields").display = transport in _HTTP_TRANSPORTS

    def action_submit(self) -> None:
        """Ctrl+S：组装表单为配置负载交 configure_mcp_server；成功关窗、失败留在窗内。"""
        try:
            payload = _build_mcp_config(
                self.query_one("#mcp-server-name", Input).value.strip(),
                self.query_one("#mcp-server-type", Select).selection,
                self.query_one("#mcp-stdio-command", Input).value.strip(),
                self.query_one("#mcp-stdio-args", TextArea).text,
                self.query_one("#mcp-http-url", Input).value.strip(),
                self.query_one("#mcp-http-headers", TextArea).text,
            )
        except ValueError as exc:
            self.app.notify(str(exc), title="⚠️ MCP", severity="error")
            return
        try:
            from core.mcp.mcp_client import configure_mcp_server
            ok, message = configure_mcp_server(json.dumps(payload, ensure_ascii=False))
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
