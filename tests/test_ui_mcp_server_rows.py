"""MCP server 列表行：按连接状态着色（connected 绿 ● / connecting 橙色 spinner 轮播 / failed 红 ○）。"""
from core.tui.screens.mcp import _server_row
from core.tui.theme import _SPINNER_FRAMES


def test_status_rows_glyph_and_color():
    connected = _server_row("fs", {"status": "connected"})
    assert connected.plain == "● fs" and "#4ade80" in str(connected.style)
    failed = _server_row("fs", {"status": "failed"})
    assert failed.plain == "○ fs" and "#f87171" in str(failed.style)
    connecting = _server_row("fs", {"status": "connecting"}, cursor=1)
    assert connecting.plain == f"{_SPINNER_FRAMES[1]} fs" and "#fb923c" in str(connecting.style)
    wrapped = _server_row("fs", {"status": "connecting"}, cursor=len(_SPINNER_FRAMES))
    assert wrapped.plain == f"{_SPINNER_FRAMES[0]} fs"  # 帧下标回绕
