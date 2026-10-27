"""键帽 emoji 降级：Windows Terminal 缺组合圈字形时把 U+20E3 画成独立一格
（1️⃣ 终端走 3 格、Textual 只记 2 格），该行分隔线与滚动条会整体右移 1 列。
assistant Markdown 入 UI 前应把键帽序列降级为基字符，宽度口径与终端一致。"""
import asyncio

from textual.widgets import Markdown

from core.tui.surface import _strip_keycaps
from tests.support import chat_app


def test_strip_keycaps_helper():
    assert _strip_keycaps("## 1️⃣ 最新") == "## 1 最新"
    assert _strip_keycaps("1⃣ #️⃣ *️⃣") == "1 # *"
    # 非键帽字符不动：VS16 emoji、破折号、CJK 宽度口径与终端一致
    assert _strip_keycaps("❤️ ⚠️ — 中文 1.5") == "❤️ ⚠️ — 中文 1.5"


def test_dim_body_strips_keycaps():
    """工具卡 / 结果卡 / notice 正文共用 _dim_body，同样降级键帽。"""
    from core.tui.render import _dim_body

    assert _dim_body("run 1️⃣ now").plain == "run 1 now"
    assert _dim_body("❤️ 保留").plain == "❤️ 保留"


def test_info_panel_row_strips_keycaps():
    """右栏条目文本来自模型（todo / 后台任务 / 子代理），同样降级。"""
    from core.tui.info_panel import _InfoRow

    row = _InfoRow("bg", {"id": "bg-1", "status": "running", "text": "1️⃣ deploy",
                          "expand_text": "2️⃣ details", "cancellable": False}, 0)
    row._expanded = True  # 展开态才带 expand_text（避免无 App 下走 toggle_expand 的刷新）
    plain = row._text().plain
    assert "\u20e3" not in plain and "\ufe0f" not in plain, plain
    assert "1 deploy" in plain and "2 details" in plain, plain


def test_static_markdown_card_sanitized():
    async def main() -> None:
        async with chat_app() as (app, pilot):
            md = app._add_markdown_card("## 2️⃣ 资本\n")
            await pilot.pause(0.2)
            assert md.source == "## 2 资本\n", repr(md.source)

    asyncio.run(main())


def test_stream_sanitizes_inline_keycap():
    async def main() -> None:
        async with chat_app() as (app, pilot):
            app._stream_update("### 3️⃣ 竞品\n")
            await pilot.pause(0.2)
            app._stop_stream()
            await pilot.pause(0.2)
            md = app.query_one("#chat .card.assistant Markdown", Markdown)
            assert md.source == "### 3 竞品\n", repr(md.source)

    asyncio.run(main())


def test_stream_keycap_split_across_chunks_and_tail_digit_kept():
    async def main() -> None:
        async with chat_app() as (app, pilot):
            for chunk in ("## 1", "\ufe0f", "\u20e3 最新\n", "end 42"):
                app._stream_update(chunk)
                await pilot.pause(0.05)
            app._stop_stream()
            await pilot.pause(0.2)
            md = app.query_one("#chat .card.assistant Markdown", Markdown)
            assert md.source == "## 1 最新\nend 42", repr(md.source)
            assert "\u20e3" not in md.source and "\ufe0f" not in md.source

    asyncio.run(main())


def test_whole_turn_is_bare_digit_prefix_not_lost():
    """整条流只有半截键帽前缀（未建卡）：停流时也要补出正文，不能吞掉。"""

    async def main() -> None:
        async with chat_app() as (app, pilot):
            app._stream_update("1")
            await pilot.pause(0.1)
            app._stop_stream()
            await pilot.pause(0.2)
            md = app.query_one("#chat .card.assistant Markdown", Markdown)
            assert md.source == "1", repr(md.source)

    asyncio.run(main())


def test_stop_stream_drops_split_vs16_tail():
    """流在「数字 + VS16」处截断：补发时去掉半截 VS16，只留基字符（不是 1️）。"""

    async def main() -> None:
        async with chat_app() as (app, pilot):
            app._stream_update("count 1\ufe0f")
            await pilot.pause(0.1)
            app._stop_stream()
            await pilot.pause(0.2)
            md = app.query_one("#chat .card.assistant Markdown", Markdown)
            assert md.source == "count 1", repr(md.source)

    asyncio.run(main())
