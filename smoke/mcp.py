"""冒烟自检 · /mcp：server 列表 / 工具列表 / 表单配置窗（保存后就地刷新、窗口不关）/ 原地确认删除（删空不关窗）/ 滑块样式同 /sessions 与 /settings。"""
from __future__ import annotations

import json

from textual.widgets import Input, OptionList, Select, Static, TextArea

from core.tui.screens import MCPConfigScreen, MCPServersScreen, MCPToolsScreen
from smoke._util import prompt_of, settle


def _assert_scrollbar_style(widget, what: str) -> None:
    """滑块样式同 app.css 共用规则（/sessions 列表 / /settings 下拉同款）：1 列宽、透明轨道、
    滑块 #475569，hover #94a3b8，拖动 #38bdf8。"""
    styles = widget.styles
    assert (styles.scrollbar_size_vertical == 1
            and styles.scrollbar_background.is_transparent
            and styles.scrollbar_color.hex.lower() == "#475569"
            and styles.scrollbar_color_hover.hex.lower() == "#94a3b8"
            and styles.scrollbar_color_active.hex.lower() == "#38bdf8"), \
        f"{what} 滑块样式与 /sessions 不一致: {styles.scrollbar_size_vertical}, {styles.scrollbar_color}"


async def run(app, pilot) -> None:
    prompt = prompt_of(app)

    # /mcp：MCPServersScreen 列 server 名（● 绿色加粗）；Enter 再开 MCPToolsScreen 看该 server
    # 工具（行格式同 /skills）；Insert 弹表单配置窗；Delete 原地确认后删除；无 server 时开空窗+Insert 引导
    import core.mcp.mcp_client as _mcp_mod
    _mcp_orig = _mcp_mod.get_mcp_server_list  # 打桩取数，不真连 server
    _mcp_servers = {
        "smoke-server-a": {"status": "connected", "tools": [
            {"tool_name": "echo", "tool_description": "Echoes input"},
            {"tool_name": "add", "tool_description": "Adds numbers"}]},
        "smoke-server-b": {"status": "connected", "tools": [
            {"tool_name": "ping", "tool_description": "Ping server"}]},
    }
    _mcp_mod.get_mcp_server_list = lambda: dict(_mcp_servers)
    prompt.focus()
    prompt.text = "/mcp"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy, "/mcp 不应触发 agent 回合"
    assert app._exception is None, f"渲染异常: {app._exception}"
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "/mcp 未打开 MCP 弹窗"
    mcp_lst = app.screen_stack[-1].query_one("#mcp-list", OptionList)
    mcp_rows = [mcp_lst.get_option_at_index(i).prompt.plain for i in range(mcp_lst.option_count)]
    assert mcp_rows == ["● smoke-server-a", "● smoke-server-b"], mcp_rows
    # server 行整行绿色加粗：_server_row 用 Text(…, style=…) 基底色（非 spans，勿照搬 _entry_row 的检查）
    mcp_row0 = mcp_lst.get_option_at_index(0).prompt
    assert "#4ade80" in str(mcp_row0.style), f"server 名色错误: {mcp_row0.style}"
    # 顶部搜索栏：server 名子串过滤（忽略大小写）；清空恢复；仅下边框 + 默认聚焦
    m_search = app.screen_stack[-1].query_one("#search-input", Input)
    assert m_search.placeholder == "Search servers…" and m_search.has_focus, m_search.placeholder
    m_row = app.screen_stack[-1].query_one("#search-row")
    assert not m_row.styles.border_top[0] and m_row.styles.border_bottom[0] == "solid"
    m_search.value = "SERVER-A"
    await settle(pilot)
    mcp_rows = [mcp_lst.get_option_at_index(i).prompt.plain for i in range(mcp_lst.option_count)]
    assert mcp_rows == ["● smoke-server-a"], mcp_rows
    m_search.value = ""
    await settle(pilot)
    assert mcp_lst.option_count == 2, "清空搜索应恢复全部 server"
    # Enter：进该 server 的工具列表窗（行格式同 /skills：● 名 + 换行描述）
    await pilot.press("enter")
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], MCPToolsScreen), "Enter 未打开工具列表窗"
    assert app.screen_stack[-1].TITLE == "smoke-server-a tools", app.screen_stack[-1].TITLE
    tool_lst = app.screen_stack[-1].query_one("#mcp-tools-list", OptionList)
    tool_rows = [tool_lst.get_option_at_index(i).prompt.plain for i in range(tool_lst.option_count)]
    assert tool_rows == ["● echo\nEchoes input", "● add\nAdds numbers"], tool_rows
    tool_spans = tool_lst.get_option_at_index(0).prompt.spans
    assert "#4ade80" in str(tool_spans[0].style), f"工具名色错误: {tool_spans[0].style}"
    assert "#64748b" in str(tool_spans[1].style), f"描述色错误: {tool_spans[1].style}"
    # 工具子窗同款搜索栏：按工具名子串过滤（忽略大小写），清空恢复
    t_search = app.screen_stack[-1].query_one("#search-input", Input)
    assert t_search.placeholder == "Search tools…" and t_search.has_focus, t_search.placeholder
    t_row = app.screen_stack[-1].query_one("#search-row")
    assert not t_row.styles.border_top[0] and t_row.styles.border_bottom[0] == "solid"
    t_search.value = "ECHO"
    await settle(pilot)
    tool_rows = [tool_lst.get_option_at_index(i).prompt.plain for i in range(tool_lst.option_count)]
    assert tool_rows == ["● echo\nEchoes input"], tool_rows
    t_search.value = ""
    await settle(pilot)
    assert tool_lst.option_count == 2, "清空搜索应恢复全部工具"
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "工具窗 Enter 应回 server 列表"
    # Delete：窗内红字原地确认（Esc 撤销），Enter 才经 unconfigure_mcp_server 删除并刷新
    _mcp_rm_orig = _mcp_mod.unconfigure_mcp_server
    removed: list[str] = []
    _mcp_mod.unconfigure_mcp_server = (
        lambda name: (removed.append(name), _mcp_servers.pop(name, None),
                      (True, "Unconfigured MCP Servers"))[2])
    await pilot.press("delete")
    await settle(pilot, 10)
    confirm_hint = app.screen_stack[-1].query_one("#confirm-hint", Static)
    assert "smoke-server-a" in str(confirm_hint.render()) and removed == [], \
        "Delete 应先原地确认而非直接删"
    await pilot.press("escape")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], MCPServersScreen) and removed == [], \
        "Esc 应只撤销确认，不关窗、不删"
    await pilot.press("delete")
    await settle(pilot, 10)
    await pilot.press("enter")
    await settle(pilot, 10)
    assert removed == ["smoke-server-a"], removed
    mcp_rows = [mcp_lst.get_option_at_index(i).prompt.plain for i in range(mcp_lst.option_count)]
    assert mcp_rows == ["● smoke-server-b"], mcp_rows
    # 窗口高度随 server 数收缩（不是通用 .picker 的 60% 固定高）
    assert app.screen_stack[-1].query_one("#mcp-picker").size.height < 12, "server 列表窗应随内容收缩"
    # Insert：弹表单配置窗（字段行样式同注册模型窗）：server name + type 下拉（stdio /
    # streamable_http / sse）；选 stdio 显示 command + args（每行一个 arg），选
    # streamable_http / sse 显示 url + headers（每行 KEY=VALUE）；Ctrl+S 把组装负载交
    # configure_mcp_server（打桩）；成功后配置窗关闭、server 列表窗保持打开并就地刷新
    _mcp_cfg_orig = _mcp_mod.configure_mcp_server
    saved_payloads: list[dict] = []

    def _fake_configure(text: str):
        payload = json.loads(text)
        saved_payloads.append(payload)
        _mcp_servers.update({name: {"status": "connected", "tools": []}
                             for name in payload["mcpServers"]})
        return True, "Configured MCP Servers"

    _mcp_mod.configure_mcp_server = _fake_configure
    await pilot.press("insert")
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], MCPConfigScreen), "Insert 未打开配置弹窗"
    form = app.screen_stack[-1]
    name_input = form.query_one("#mcp-server-name", Input)
    type_select = form.query_one("#mcp-server-type", Select)
    assert name_input.has_focus, "配置表单应默认聚焦 server name"
    assert type_select.selection is None, f"type 应默认未选择: {type_select.value!r}"
    assert type_select.prompt == "Select a transport type", type_select.prompt
    assert [v for _p, v in type_select._options if v is not Select.NULL] == \
        ["stdio", "streamable_http", "sse"], "type 下拉选项错误"
    # 未选 type 时两组条件字段都隐藏；选后只显示对应组
    assert not form.query_one("#mcp-stdio-fields").display and \
        not form.query_one("#mcp-http-fields").display, "未选 type 时不应显示条件字段"
    # 字段行样式同注册模型窗：❯ 标记 + 行下边框；行内 Input/Select 无自身边框
    rows = list(form.query(".field-row"))
    assert len(rows) == 4, f"配置表单应有 4 个字段行（含隐藏组）: {len(rows)}"
    assert all(not r.styles.border_top[0] and r.styles.border_bottom[0] == "solid" for r in rows)
    assert [str(m.render()) for m in form.query(".field-mark")] == ["❯"] * 4
    assert not form.query_one("#mcp-server-type").query_one("SelectCurrent").styles.border_top[0]
    # Esc 先关 type 下拉浮层、再关窗（同注册模型窗：Esc 不穿透弹窗）
    type_select.focus()
    await pilot.press("enter")
    await settle(pilot)
    assert type_select.expanded, "Enter 应展开 type 下拉"
    # type 下拉滑块同 /settings default model 下拉（app.css 同一组共用规则）
    _assert_scrollbar_style(type_select.query_one("SelectOverlay"), "type 下拉浮层")
    await pilot.press("escape")
    await settle(pilot)
    assert not type_select.expanded and app.screen_stack[-1] is form, \
        "Esc 应只关 type 下拉浮层（窗不关）"
    # ↑/↓ 切字段（同注册窗）；args / headers 文本区内 ↑/↓ 归文本区（移光标不换焦点）
    name_input.focus()
    await pilot.press("down")
    await settle(pilot)
    assert form.focused is type_select, f"↓ 应从 name 切到 type: {form.focused.id}"
    await pilot.press("up")
    await settle(pilot)
    assert form.focused is name_input, f"↑ 应切回 name: {form.focused.id}"
    type_select.value = "stdio"
    await settle(pilot)
    assert form.query_one("#mcp-stdio-fields").display and \
        not form.query_one("#mcp-http-fields").display, "选 stdio 应只显示 stdio 组"
    args_area = form.query_one("#mcp-stdio-args", TextArea)
    assert args_area.styles.border_top[0] == "round", "args 输入区应有圆角边框"
    _assert_scrollbar_style(args_area, "args 输入区")
    args_area.focus()
    await pilot.press("up")
    await settle(pilot)
    assert form.focused is args_area, "args 文本区内 ↑ 不应切走焦点"
    name_input.value = "smoke-form"
    form.query_one("#mcp-stdio-command", Input).value = "npx"
    args_area.text = "-y\n@modelcontextprotocol/server-filesystem\n."
    await pilot.press("ctrl+s")
    await settle(pilot, 10)
    assert saved_payloads == [{"mcpServers": {"smoke-form": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}}}], saved_payloads
    assert not isinstance(app.screen_stack[-1], MCPConfigScreen), "保存成功应关闭配置窗"
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "配置成功后应保留 server 列表窗"
    mcp_rows = [mcp_lst.get_option_at_index(i).prompt.plain for i in range(mcp_lst.option_count)]
    assert mcp_rows == ["● smoke-server-b", "● smoke-form"], mcp_rows  # 就地刷新出新 server
    # 再配 streamable_http：url + headers（每行 KEY=VALUE）
    await pilot.press("insert")
    await settle(pilot, 10)
    form = app.screen_stack[-1]
    assert isinstance(form, MCPConfigScreen)
    form.query_one("#mcp-server-name", Input).value = "smoke-http"
    form.query_one("#mcp-server-type", Select).value = "streamable_http"
    await settle(pilot)
    assert not form.query_one("#mcp-stdio-fields").display and \
        form.query_one("#mcp-http-fields").display, "选 streamable_http 应只显示 http 组"
    form.query_one("#mcp-http-url", Input).value = "https://api.smoke.test/mcp"
    headers_area = form.query_one("#mcp-http-headers", TextArea)
    _assert_scrollbar_style(headers_area, "headers 输入区")
    headers_area.text = "Authorization=Bearer smoke"
    await pilot.press("ctrl+s")
    await settle(pilot, 10)
    assert saved_payloads[-1] == {"mcpServers": {"smoke-http": {
        "type": "streamable_http", "url": "https://api.smoke.test/mcp",
        "headers": {"Authorization": "Bearer smoke"}}}}, saved_payloads[-1]
    # 再配 sse：复用 url + headers 组；headers 留空则配置里不出现 headers 键
    await pilot.press("insert")
    await settle(pilot, 10)
    form = app.screen_stack[-1]
    assert isinstance(form, MCPConfigScreen)
    form.query_one("#mcp-server-name", Input).value = "smoke-sse"
    form.query_one("#mcp-server-type", Select).value = "sse"
    await settle(pilot)
    assert form.query_one("#mcp-http-fields").display and \
        not form.query_one("#mcp-stdio-fields").display, "选 sse 应只显示 http 组"
    form.query_one("#mcp-http-url", Input).value = "https://api.smoke.test/sse"
    await pilot.press("ctrl+s")
    await settle(pilot, 10)
    assert saved_payloads[-1] == {"mcpServers": {"smoke-sse": {
        "type": "sse", "url": "https://api.smoke.test/sse"}}}, saved_payloads[-1]
    mcp_rows = [mcp_lst.get_option_at_index(i).prompt.plain for i in range(mcp_lst.option_count)]
    assert mcp_rows == ["● smoke-server-b", "● smoke-form", "● smoke-http", "● smoke-sse"], mcp_rows
    # 表单校验：必填缺失 / headers 非法行都留在窗内提示，不调用 configure_mcp_server
    await pilot.press("insert")
    await settle(pilot, 10)
    form = app.screen_stack[-1]
    assert isinstance(form, MCPConfigScreen)
    before = len(saved_payloads)
    await pilot.press("ctrl+s")  # 全空：server name 缺失
    await settle(pilot)
    assert app.screen_stack[-1] is form, "空表单不应保存"
    assert any("Server name is required" in n.message for n in app._notifications), \
        [n.message for n in app._notifications]
    form.query_one("#mcp-server-name", Input).value = "smoke-bad"
    form.query_one("#mcp-server-type", Select).value = "sse"
    await settle(pilot)
    await pilot.press("ctrl+s")  # 缺 URL
    await settle(pilot)
    assert app.screen_stack[-1] is form, "缺 URL 不应保存"
    form.query_one("#mcp-http-url", Input).value = "https://api.smoke.test/bad"
    form.query_one("#mcp-http-headers", TextArea).text = "broken"
    await pilot.press("ctrl+s")  # 非法 header 行
    await settle(pilot)
    assert app.screen_stack[-1] is form, "非法 header 行不应保存"
    assert any("Header line 1" in n.message for n in app._notifications), \
        [n.message for n in app._notifications]
    assert len(saved_payloads) == before, "校验失败不应调用 configure_mcp_server"
    # 服务层保存失败：留在配置窗内重试；Esc 关配置窗后 server 列表窗仍在，再 Esc 关闭
    _mcp_mod.configure_mcp_server = lambda text: (False, "MCP server config invalid")
    form.query_one("#mcp-http-headers", TextArea).text = "ok=1"
    await pilot.press("ctrl+s")
    await settle(pilot, 10)
    assert app.screen_stack[-1] is form, "配置失败应留在配置窗内重试"
    await pilot.press("escape")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "Esc 关的应是配置窗"
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], MCPServersScreen), "Esc 未关闭 MCPServersScreen"
    _mcp_mod.configure_mcp_server = _mcp_cfg_orig  # 复原
    # 删空（所有 server 依次被删）→ 窗保持打开、空列表（注销不关窗，可继续 Insert），Esc 才关
    prompt.focus()
    prompt.text = "/mcp"
    await pilot.press("enter")
    await settle(pilot, 10)
    for _ in range(4):
        await pilot.press("delete")
        await settle(pilot, 10)
        await pilot.press("enter")
        await settle(pilot, 10)
    assert removed == ["smoke-server-a", "smoke-server-b", "smoke-form",
                       "smoke-http", "smoke-sse"], removed
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "删空后不应关窗（可连续注销）"
    empty_after_remove = app.screen_stack[-1].query_one("#mcp-list", OptionList)
    assert empty_after_remove.option_count == 0, "删空后列表应为空"
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], MCPServersScreen), "Esc 应关闭空 server 列表窗"
    _mcp_mod.unconfigure_mcp_server = _mcp_rm_orig  # 复原
    _mcp_mod.get_mcp_server_list = lambda: {}  # 无 server（未配置）
    no_server_toasts_before = sum("No MCP servers" in n.message for n in app._notifications)
    prompt.focus()
    prompt.text = "/mcp"
    await pilot.press("enter")
    await settle(pilot, 10)
    assert not app._busy, "/mcp 不应触发 agent 回合"
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "无 server 时应开空窗（可在窗内 Insert 配置）"
    empty_lst = app.screen_stack[-1].query_one("#mcp-list", OptionList)
    assert empty_lst.option_count == 0, "空 server 列表不应有行"
    assert any("No MCP servers" in n.message for n in app._notifications), \
        [n.message for n in app._notifications]  # 空态走 Toast，不挤提示行
    empty_toasts = [n.message for n in app._notifications if "No MCP servers" in n.message]
    # 本次开窗只弹一次（on_mount 的 MRO 派发 + 显式 super 曾重复；删空时的那次已计入基线）
    assert len(empty_toasts) - no_server_toasts_before == 1, empty_toasts
    # 空窗里 Insert 照样能配 server：弹配置窗，Esc 回列表，再 Esc 关闭
    await pilot.press("insert")
    await settle(pilot, 10)
    assert isinstance(app.screen_stack[-1], MCPConfigScreen), "空窗 Insert 未打开配置窗"
    await pilot.press("escape")
    await settle(pilot)
    assert isinstance(app.screen_stack[-1], MCPServersScreen), "Esc 关的应是配置窗"
    await pilot.press("escape")
    await settle(pilot)
    assert not isinstance(app.screen_stack[-1], MCPServersScreen), "Esc 未关闭 MCPServersScreen"
    _mcp_mod.get_mcp_server_list = _mcp_orig  # 复原
    print("[smoke] /mcp OK: server list (content-sized), tools list, config form (args/headers & type scrollbars match /sessions & /settings), Delete confirms then unconfigures (window stays open when emptied), empty list configurable window")
