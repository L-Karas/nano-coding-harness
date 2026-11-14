"""包级 Interface 与部件取件门面：`core.tui` 只暴露 run / ChatApp；`Parts` 收口 `#id`。"""
import asyncio
import subprocess
import sys

import pytest
from textual.containers import Vertical
from textual.widgets import OptionList, Static

import core.tui as tui
from concurrent.futures import Future

from core.tui.ask import AskKind, AskRequest
from core.tui.footer import _FooterBar
from core.tui.info_panel import _InfoPanel
from core.tui.panels import _ChatBoard
from core.tui.status import _StatusLine
from core.tui.widgets import _ClarifyList, _CommandInput
from tests.support import chat_app


def test_package_exports_only_run_and_chat_app():
    assert tui.__all__ == ["run", "ChatApp"]
    assert callable(tui.run)
    assert tui.ChatApp.__name__ == "ChatApp"
    with pytest.raises(AttributeError):
        getattr(tui, "no_such_symbol")  # noqa: B009 - __getattr__ 只解析声明符号


def test_importing_package_does_not_load_textual():
    """包级惰性导入：`import core.tui` 不拉起 Textual（子进程隔离，conftest 已导入）。"""
    code = "import sys, core.tui; assert 'textual' not in sys.modules, sorted(sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_parts_returns_typed_widgets():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            parts = app.parts
            assert isinstance(parts.board, _ChatBoard)
            assert isinstance(parts.prompt, _CommandInput)
            assert isinstance(parts.status, _StatusLine)
            assert isinstance(parts.footer, _FooterBar)
            assert isinstance(parts.info, _InfoPanel)
            assert isinstance(parts.perm, OptionList)
            assert isinstance(parts.clarify, _ClarifyList)
            assert isinstance(parts.left, Vertical) and isinstance(parts.right, Vertical)
            assert isinstance(parts.info_tab, Static)

    asyncio.run(main())


def test_attach_is_exclusive_and_unattached_calls_raise():
    from core.tui import render

    async def main() -> None:
        async with chat_app(size=(80, 24)) as (app, _pilot):
            with render.attach(app):
                with pytest.raises(RuntimeError, match="already attached"):
                    with render.attach(app):
                        pass
                pending: Future[str] = Future()
                app.asks.open(AskRequest(AskKind.PERMISSION), pending)
            assert pending.result(timeout=0) == "no", "卸载时挂起询问必须落地，等待线程不悬挂"
            with pytest.raises(RuntimeError, match="not running"):
                render.render_user_input("x")

    asyncio.run(main())
