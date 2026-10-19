"""页脚第三行显示当前审批模式；模式变化后由页脚刷新即时反映。"""
import asyncio
from types import SimpleNamespace

from textual.widgets import Static

import core.config
import core.tui.utils as utils
from core.tui.ui_textual import ChatApp


def test_current_approval_mode_reads_config(monkeypatch):
    monkeypatch.setattr(core.config, "CONFIGMANAGER",
                        SimpleNamespace(config=SimpleNamespace(approval_mode="Yolo")))
    assert utils.current_approval_mode() == "Yolo"


def test_current_approval_mode_falls_back_to_manual(monkeypatch):
    monkeypatch.setattr(core.config, "CONFIGMANAGER", SimpleNamespace())
    assert utils.current_approval_mode() == "Manual"


def test_footer_shows_approval_mode_and_refreshes(monkeypatch):
    config = SimpleNamespace(approval_mode="Manual")
    monkeypatch.setattr(core.config, "CONFIGMANAGER", SimpleNamespace(config=config))

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            row = app.query_one("#approval-mode", Static)
            assert str(row.render()) == "Approval Mode: Manual"
            config.approval_mode = "Yolo"
            app._refresh_footer()
            assert str(row.render()) == "Approval Mode: Yolo"

    asyncio.run(main())
