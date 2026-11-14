"""聊天画板（Board）Interface：卡片挂载 / 并回 / 流式 / 清板的不变式。"""
import asyncio

from rich.text import Text

from core.tui import blocks
from tests.support import chat_app


def _cards(app):
    return list(app.query("#chat .card"))


def _welcome_shown(app) -> bool:
    return app.parts.left.has_class("-welcome")


def test_add_mounts_block_and_hides_welcome():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, _pilot):
            assert _welcome_shown(app), "初始为空聊板"
            app.board.add(blocks.Block("user", Text("hello")))
            assert len(_cards(app)) == 1
            assert not _welcome_shown(app), "有内容后收起欢迎态"

    asyncio.run(main())


def test_clear_removes_cards_and_restores_welcome():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            app.board.add(blocks.Block("user", Text("hello")))
            await pilot.pause(0.05)  # 等挂载消息落地再清板
            app.board.clear()
            await pilot.pause(0.05)  # remove_children 返回 AwaitRemove，等删除落地
            assert _cards(app) == []
            assert _welcome_shown(app)

    asyncio.run(main())


def test_merge_without_registered_call_creates_standalone_card():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, _pilot):
            app.board.merge(blocks.tool_result("ghost", "late"))
            cards = _cards(app)
            assert len(cards) == 1 and cards[0].has_class("result")

    asyncio.run(main())


def test_merge_consumes_registration_once():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, _pilot):
            app.board.add(blocks.tool_call("c1", "terminal", {"command": "ls"}))
            app.board.merge(blocks.tool_result("c1", "first"))
            assert len(_cards(app)) == 1, "同一 id 的结果并回调用卡"
            app.board.merge(blocks.tool_result("c1", "second"))
            assert len(_cards(app)) == 2, "登记项只消费一次"

    asyncio.run(main())


def test_end_stream_is_idempotent_and_empty_stream_adds_nothing():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, _pilot):
            app.board.stream("")
            app.board.end_stream()
            app.board.end_stream()
            assert _cards(app) == [], "空增量不建卡，停流可重复调用"

    asyncio.run(main())


def test_assistant_block_mounts_markdown_widget():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            app.board.add(blocks.assistant_markdown("## title"))
            await pilot.pause(0.05)
            card = _cards(app)[0]
            assert card.has_class("assistant")
            assert card.query("Markdown").first() is not None

    asyncio.run(main())
