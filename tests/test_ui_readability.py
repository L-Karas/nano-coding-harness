"""输入占位单行可容纳 + 次级文字可读性。"""
import asyncio

from rich.cells import cell_len
from textual.widgets import Static

from core.tui.footer import _token_bar_text
from core.tui.theme import _PLACEHOLDER
from core.tui.ui_textual import ChatApp

_SECONDARY = "#94a3b8"


def test_placeholder_fits_input_line():
    assert "\n" not in _PLACEHOLDER
    assert cell_len(_PLACEHOLDER) <= 60

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            prompt = app._prompt()
            assert cell_len(_PLACEHOLDER) <= prompt.content_region.width

    asyncio.run(main())


def test_secondary_text_color_raised():
    assert _token_bar_text(0, 0).spans[0].style == _SECONDARY

    async def main() -> None:
        app = ChatApp(handle_query=lambda _q: None)
        async with app.run_test():
            assert app.query_one("#footer", Static).styles.color.hex.lower() == _SECONDARY
            assert app.query_one("#info-hint", Static).styles.color.hex.lower() == _SECONDARY
            empty = app.query_one("#todos-list .info-row", Static)
            assert empty.styles.color.hex.lower() == _SECONDARY

    asyncio.run(main())
