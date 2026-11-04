"""write / edit 的 diff 与工具结果同卡渲染：diff 在前且可折叠，结果固定收尾，不拆成两张卡片。"""
import asyncio
from contextlib import contextmanager

import core.tui.render as render
from core.context.message import Message
from core.context.session.session import Session
from tests.support import chat_app

_RESULT = "Wrote 42 bytes to a.py."
_DIFF = [(" ", 1, "def main():"), ("-", 2, "    print('old')"), ("+", 2, "    print('new')")]


@contextmanager
def _bridge(app):
    """run_test 不经 run()：手动挂渲染桥接（同 smoke / 权限桥接测试）。"""
    render._APP = app
    try:
        yield
    finally:
        render._APP = None


def test_tool_result_with_diff_uses_one_card_with_result_last():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with _bridge(app):
                render.render_tool_result(_RESULT, diff=_DIFF)
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 1, f"diff 与结果应同卡渲染: {len(cards)} 张卡"
                card = cards[0]
                assert card.has_class("result") and not card.has_class("diff")
                bodies = list(card.query(".card-body"))
                assert len(bodies) == 2, "卡片应由 diff 正文 + 结果尾段组成"
                assert "+2 │     print('new')" in str(bodies[0].render()), "diff 应排在前面"
                assert _RESULT in str(bodies[-1].render()), "工具结果应排在最后"

    asyncio.run(main())


def test_long_diff_collapses_without_hiding_result():
    """diff 超长只折叠 diff 段；结果尾段独立成段，始终可见。"""
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with _bridge(app):
                render.render_tool_result(
                    _RESULT,
                    diff=[("+", i, f"add {i}") for i in range(1, 16)],
                )
                await pilot.pause(0.1)
                bodies = list(app.query("#chat .card .card-body"))
                assert len(bodies) == 2
                assert bodies[0].has_class("-expandable"), "长 diff 应可折叠"
                assert bodies[0].size.height == render.DEFAULT_MAX_LINES + 1, \
                    "diff 折叠为 max_lines 行 + 1 行提示"
                assert not bodies[-1].has_class("-expandable"), "结果尾段不应参与折叠"
                assert _RESULT in str(bodies[-1].render()), "diff 折叠不应吞掉结果尾段"

    asyncio.run(main())


def test_long_result_tail_is_not_collapsed():
    """结果尾段是普通 Static：超过 max_lines 也完整显示，不出现折叠提示。"""
    long_result = "\n".join(f"result {i}" for i in range(1, 16))

    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with _bridge(app):
                render.render_tool_result(long_result, diff=_DIFF)
                await pilot.pause(0.1)
                bodies = list(app.query("#chat .card .card-body"))
                assert len(bodies) == 2
                assert not bodies[-1].has_class("-expandable")
                assert bodies[-1].size.height == 15, "结果尾段不应被折叠截断"
                assert "result 15" in str(bodies[-1].render())

    asyncio.run(main())


def test_session_replay_with_diff_uses_one_card():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with _bridge(app):
                render.render_session_history(Session(messages=[
                    Message(role="assistant", tool_calls=[
                        {"id": "a", "function": {"name": "write_file", "arguments": "{}"}},
                    ]),
                    Message(role="tool", tool_call_id="a", content=_RESULT, payload=_DIFF),
                ]))
                await pilot.pause(0.1)
                cards = list(app.query("#chat .card"))
                assert len(cards) == 2, "回放应只有工具调用卡与同卡结果卡"
                bodies = list(cards[-1].query(".card-body"))
                assert len(bodies) == 2
                assert _RESULT in str(bodies[-1].render()), "回放的 diff 与结果应同卡"

    asyncio.run(main())


def test_tool_result_without_diff_keeps_single_body():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            with _bridge(app):
                render.render_tool_result("plain output")
                await pilot.pause(0.1)
                card = app.query("#chat .card").last()
                assert len(list(card.query(".card-body"))) == 1

    asyncio.run(main())
