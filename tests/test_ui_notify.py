"""notify 适配：域层端口与 TUI 直连共用同一 toast 约定（纯文本正文、severity 透传）。

TUI 直连路径（`ChatApp._notify`）的行为见 `tests/test_ui_extensions.py`；
这里只验证 `core.interaction.notify` → `ChatApp._notify` 这层桥接。
"""
import asyncio

from core import interaction
from core.tui.ui_textual import ChatApp
from tests.support import render_bridge


def test_interaction_notify_renders_plain_text_toast():
    async def main():
        app = ChatApp(handle_query=lambda query: None)
        with render_bridge(app):
            async with app.run_test() as pilot:
                interaction.notify("[Error] boom", title="⚠️ Test", severity="error")
                await pilot.pause()
                note = next(n for n in app._notifications if n.message == "[Error] boom")
                assert note.title == "⚠️ Test"
                assert note.severity == "error"
                assert note.markup is False, "正文不能按 Rich 标记解析"
    asyncio.run(main())
