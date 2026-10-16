"""权限确认卡：确认文本保持原样式（加粗琥珀），触发数据段落（details）按工具卡正文
（`core.tui.render._DIM`，dim #e2e8f0）展示，段间空一行。"""
import asyncio
import threading
from concurrent.futures import Future

from rich.text import Text
from textual.widgets import Static

import core.tui.render as _render
from core.tui.render import _DIM
from core.tui.ui_textual import ChatApp

_REASON = "Destructive command detected"
_DETAIL = "command: rm old.txt"


def _app() -> ChatApp:
    return ChatApp(handle_query=lambda _query: None)


def _body(app: ChatApp) -> Text:
    content = app.query_one(".card.perm .card-body", Static).content
    assert isinstance(content, Text), "perm 卡正文应是 rich Text（两段不同样式）"
    return content


def test_permission_card_renders_reason_and_command_detail():
    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._begin_permission(_REASON, [_DETAIL], Future())
            await pilot.pause()
            assert _body(app).plain == f"{_REASON}\n\n{_DETAIL}"
    asyncio.run(main())


def test_permission_card_detail_uses_tool_card_body_style():
    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._begin_permission(_REASON, [_DETAIL], Future())
            await pilot.pause()
            body, console = _body(app), app.console
            assert body.get_style_at_offset(console, 0) == console.get_style("bold #fef3c7")
            assert (body.get_style_at_offset(console, len(_REASON) + 2)
                    == console.get_style(_DIM)), "触发摘要颜色 / 暗度须与工具卡正文一致"
    asyncio.run(main())


def test_permission_card_without_detail_keeps_reason_only():
    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._begin_permission(_REASON, [], Future())
            await pilot.pause()
            assert _body(app).plain == _REASON
    asyncio.run(main())


def test_permission_card_styles_multiline_details_like_tool_card():
    """details 逐段渲染（如越界路径清单 + 命令）：整块均按工具卡正文样式上色。"""
    details = ["- ../secret.txt", "command: cat ../secret.txt"]

    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._begin_permission("Access outside working directory:", details, Future())
            await pilot.pause()
            body, console = _body(app), app.console
            message = "Access outside working directory:"
            assert body.plain == f"{message}\n\n" + "\n\n".join(details)
            offset = len(message) + 2
            for detail in details:
                assert body.get_style_at_offset(console, offset) == console.get_style(_DIM)
                offset += len(detail) + 2
    asyncio.run(main())


def test_render_bridge_passes_details_into_card():
    """render.ask_permission（后台线程）→ _begin_permission 的桥接不丢 details。"""
    async def main() -> None:
        app = _app()
        _render._APP = app  # run_test 不经 run()：照 smoke 手动挂渲染桥接
        try:
            async with app.run_test(size=(100, 32)):
                answer: Future[str] = Future()
                threading.Thread(
                    target=lambda: answer.set_result(
                        _render.ask_permission(_REASON, [_DETAIL]))).start()
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    if app._perm_pending:
                        break
                assert app._perm_pending, "桥接调用应挂起权限确认"
                assert _body(app).plain == f"{_REASON}\n\n{_DETAIL}"
                app._answer_permission("yes")
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    if answer.done():
                        break
                assert answer.result(timeout=0) == "yes"
        finally:
            _render._APP = None
    asyncio.run(main())
