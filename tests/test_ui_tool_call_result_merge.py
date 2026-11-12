"""工具调用与工具结果合并渲染：结果按 id 并进调用卡，卡片样式由 tool 换成 result / error。"""
import asyncio

import core.tui.render as render
from core.context.message import Message
from core.context.session.session import Session
from tests.support import chat_app, render_bridge


def _card_text(card) -> str:
    """把一张卡的正文按挂载顺序拼成纯文本。"""
    return "\n".join(str(body.render()) for body in card.query(".card-body"))


def test_result_merges_into_call_card_and_swaps_style():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_tool_call("c1", "terminal", {"command": "ls -la"})
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 1
                assert cards[0].has_class("tool")
                assert cards[0].styles.background.hex.lower() == "#4c3e1a", "调用卡应为工具调用底色"

                render.render_tool_result("c1", "demo.txt")
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 1, "结果应并进调用卡，不新建卡"
                card = cards[0]
                assert card.has_class("result") and not card.has_class("tool"), \
                    "结果到达后卡片应换成结果样式，不再保留 tool 类"
                assert card.styles.background.hex.lower() == "#283228", "结果卡应换成结果底色"
                bodies = list(card.query(".card-body"))
                assert len(bodies) == 2, "参数段 + 结果段"
                assert "command: ls -la" in str(bodies[0].render())
                assert "Tool Result:" in str(bodies[-1].render()), "结果段应有 Tool Result: 标题行"
                assert "demo.txt" in str(bodies[-1].render())
                assert bodies[-1].has_class("-merged"), "追加的结果段应与参数段留一空白行"
                assert bodies[-1].styles.margin.top == 1
                assert app._tool_cards == {}, "合并后应移除登记项"

    asyncio.run(main())


def test_error_result_swaps_to_error_card():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_tool_call("c1", "terminal", {"command": "false"})
                render.render_tool_result("c1", "[Tool Error]: exit code 1", is_error=True)
                await pilot.pause(0.1)
                card = app.query("#chat .card").last()
                assert len(list(app.query("#chat .card"))) == 1
                assert card.has_class("error") and not card.has_class("tool")
                assert card.styles.background.hex.lower() == "#3c2828"
                assert "[Tool Error]: exit code 1" in _card_text(card)

    asyncio.run(main())


def test_results_pair_by_id_not_by_order():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_tool_call("a", "read_file", {"path": "a.py"})
                render.render_tool_call("b", "grep", {"pattern": "todo"})
                render.render_tool_result("b", "grep output")
                render.render_tool_result("a", "read output")
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 2, "两个调用各一张卡"
                assert "path: a.py" in _card_text(cards[0])
                assert "read output" in _card_text(cards[0])
                assert "pattern: todo" in _card_text(cards[1])
                assert "grep output" in _card_text(cards[1])
                assert "grep output" not in _card_text(cards[0]), "结果不得错配到别的调用卡"
                assert all(card.has_class("result") for card in cards)

    asyncio.run(main())


def test_orphan_result_keeps_standalone_card():
    """无对应调用（权限拦截 / 解析失败 / 已清屏）时结果独立成卡。"""
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_tool_result("missing", "blocked by permission")
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 1
                assert cards[0].has_class("result")
                assert "Tool Result:" in _card_text(cards[0])
                assert "blocked by permission" in _card_text(cards[0])

    asyncio.run(main())


def test_args_capped_at_four_lines():
    args = list(range(15))  # 参数 JSON 共 17 行

    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_tool_call("c1", "terminal", args)
                await pilot.pause(0.1)
                body = app.query("#chat .card.tool .card-body").first()
                assert render.TOOL_ARGS_MAX_LINES == 4
                assert body.has_class("-expandable")
                assert body.size.height == 1 + render.TOOL_ARGS_MAX_LINES + 1, \
                    "标题 1 行 + 参数 4 行 + 折叠提示 1 行"
                hint = body.render_line(body.size.height - 1).text
                assert "truncated 13 lines" in hint, hint

    asyncio.run(main())


def test_replay_merges_call_and_result_into_one_card():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_session_history(Session(messages=[
                    Message(role="assistant", tool_calls=[
                        {"id": "a", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
                    ]),
                    Message(role="tool", tool_call_id="a", content="file contents"),
                ]))
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 1, "回放也应是一张合并卡"
                assert cards[0].has_class("result") and not cards[0].has_class("tool")
                assert "path: a.py" in _card_text(cards[0])
                assert "file contents" in _card_text(cards[0])

    asyncio.run(main())


def test_clear_cards_drops_tool_card_registry():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with render_bridge(app):
                render.render_tool_call("c1", "terminal", {"command": "ls"})
                await pilot.pause(0.1)
                app._clear_cards()
                render.render_tool_result("c1", "late output")
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 1, "清屏后到达的结果应独立成卡"
                assert cards[0].has_class("result")
                assert "late output" in _card_text(cards[0])

    asyncio.run(main())
