"""停靠区询问 Adapter（AskPanel）：视图 → 部件、动作 → 状态机、焦点改道。"""
import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

from core.tui.status import _StatusLine
from core.tui.widgets import _CommandInput
from tests.support import chat_app


def _status(app) -> _StatusLine:
    return app.query_one("#status", _StatusLine)


def _prompt(app) -> _CommandInput:
    return app.query_one("#prompt", _CommandInput)


def test_permission_sync_shows_list_with_default_no():
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            future: Future[str] = Future()
            app._begin_permission("Allow?", ["path: /tmp/a"], future)
            await pilot.pause()
            assert app.parts.perm.styles.display == "block"
            assert app.parts.perm.highlighted == 1, "默认高亮 No（同旧版 [y/N]）"
            assert _prompt(app).disabled
            assert "Confirm permission" in _status(app)._text

            app._answer("yes")
            await pilot.pause()
            assert app.parts.perm.styles.display == "none"
            assert future.result(timeout=0) == "yes"
            assert not _prompt(app).disabled, "回合外作答后就地恢复输入"

    asyncio.run(main())


def test_second_ask_is_fail_closed_instead_of_hanging():
    """已有挂起请求时第二次询问立即落地默认答案，等待线程不会悬挂。"""
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            first: Future[str] = Future()
            app._begin_permission("Allow?", [], first)
            await pilot.pause()
            second: Future[str] = Future()
            app._begin_permission("Again?", [], second)
            assert second.result(timeout=0) == "no", "权限确认并发时按拒绝落地"
            assert not first.done(), "先到的请求不受影响"

            app._answer("yes")
            await pilot.pause()
            third: Future[str] = Future()
            app._begin_clarify(["A"], False, third)
            fourth: Future[str] = Future()
            app._begin_clarify(["B"], False, fourth)
            assert fourth.result(timeout=0) == "[User cancelled]"

    asyncio.run(main())


def test_sync_is_idempotent_in_other_mode():
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            app._begin_clarify(["A"], False, Future())
            await pilot.pause()
            app._select_clarify(1)  # Other
            await pilot.pause()
            assert not _prompt(app).disabled

            app.ask_panel.sync()
            app.ask_panel.sync()
            assert not _prompt(app).disabled, "重复 sync 不得把 Other 输入条禁用"

    asyncio.run(main())


def test_clarify_other_transitions_prompt_and_back():
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            future: Future[str] = Future()
            app._begin_clarify(["A", "B"], False, future)
            await pilot.pause()
            assert app.parts.clarify.styles.display == "block"
            assert app.parts.clarify.highlighted == 0
            assert app.parts.clarify.option_count == 3, "选项 + Other 行"

            app._select_clarify(2)  # Other
            await pilot.pause()
            assert app.asks.view().other_mode
            assert not _prompt(app).disabled and _prompt(app).has_focus
            assert _prompt(app).placeholder == "Type your own answer…"

            app.on_input_submitted(SimpleNamespace(value="  ", input=app.parts.prompt))
            await pilot.pause()
            assert not future.done(), "空白提交只退回列表，不作答"
            assert not app.asks.view().other_mode
            assert app.parts.clarify.has_focus

            app._select_clarify(2)
            await pilot.pause()
            app.on_input_submitted(SimpleNamespace(value="custom", input=app.parts.prompt))
            await pilot.pause()
            assert future.result(timeout=0) == "custom"

    asyncio.run(main())


def test_multi_enter_on_other_row_enters_input_mode():
    """多选时 Enter 停在 Other 行只进入输入模式，不把已勾选项直接提交。"""
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            future: Future[str] = Future()
            app._begin_clarify(["A", "B"], True, future)
            await pilot.pause()
            app.parts.clarify.highlighted = 0
            app.ask_panel.toggle()  # 勾选 A
            app.parts.clarify.highlighted = 2  # Other 行
            app.on_clarify_confirmed(SimpleNamespace())
            await pilot.pause()
            assert app.asks.view().other_mode, "Enter 在 Other 行应进入输入模式"
            assert not future.done()

            app.on_input_submitted(SimpleNamespace(value="mine", input=app.parts.prompt))
            await pilot.pause()
            assert future.result(timeout=0) == "- A\n- mine"

    asyncio.run(main())


def test_clarify_multi_confirm_composes_selection():
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            future: Future[str] = Future()
            app._begin_clarify(["A", "B", "C"], True, future)
            await pilot.pause()
            app.ask_panel.toggle()
            await pilot.press("down")
            app.ask_panel.toggle()
            await pilot.pause()
            assert app.asks.view().chosen == frozenset({0, 1})
            assert app.ask_panel.confirm()
            assert future.result(timeout=0) == "- A\n- B"

    asyncio.run(main())


def test_focus_target_routes_by_pending_state():
    async def main() -> None:
        async with chat_app(size=(100, 32)) as (app, pilot):
            prompt = _prompt(app)
            assert app.ask_panel.focus_target(prompt) is prompt, "空闲时焦点归输入条"

            app._begin_permission("Allow?", [], Future())
            await pilot.pause()
            assert app.ask_panel.focus_target(prompt) is app.parts.perm
            app._answer("no")

            app._begin_clarify(["A"], False, Future())
            await pilot.pause()
            assert app.ask_panel.focus_target(prompt) is app.parts.clarify
            app._select_clarify(1)  # Other：焦点回输入条
            await pilot.pause()
            assert app.ask_panel.focus_target(prompt) is prompt

    asyncio.run(main())
