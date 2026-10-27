"""回合外（子代理 / cron）询问作答后的停靠区复位。

权限确认 / clarify 从前台回合内发出时，由回合结束的 _set_idle 统一恢复输入条与状态行；
子代理 / 自动回合在 UI 回合外发出的询问没有收尾回合，必须作答当时就地恢复空闲界面，
否则界面会永久停在「Working… + 输入条禁用」，Esc 也无事可做。"""
import asyncio
from concurrent.futures import Future

from core.runtime_state import RuntimeState
from core.tui.ui_textual import ChatApp
from tests.support import chat_app


def _status_text(app: ChatApp) -> str:
    return app.query_one("#status")._text


def test_out_of_turn_permission_answer_restores_idle_ui():
    """子代理 / cron 的权限询问作答后：输入条恢复、状态行清空、可继续键入。"""
    async def main() -> None:
        async with chat_app(size=(100, 32), runtime_state=RuntimeState()) as (app, pilot):
            app._begin_permission("Allow?", [], Future())
            await pilot.pause()
            assert app.query_one("#prompt").disabled, "询问挂起时输入条禁用"

            app._answer_permission("yes")
            await pilot.pause()
            prompt = app.query_one("#prompt")
            assert not prompt.disabled, "回合外作答后输入条必须恢复"
            assert _status_text(app) == "", "回合外作答后状态行不得残留 Working…"
            await pilot.press("h", "i")
            assert prompt.text == "hi", "恢复后应能继续输入"

    asyncio.run(main())


def test_out_of_turn_clarify_answer_restores_idle_ui():
    """子代理的 clarify 作答后：输入条恢复、状态行清空。"""
    async def main() -> None:
        async with chat_app(size=(100, 32), runtime_state=RuntimeState()) as (app, pilot):
            app._begin_clarify(["Which one?"], False, Future())
            await pilot.pause()
            assert app.query_one("#prompt").disabled

            app._answer_clarify("Which one?")
            await pilot.pause()
            assert not app.query_one("#prompt").disabled, "回合外作答后输入条必须恢复"
            assert _status_text(app) == "", "回合外作答后状态行不得残留 Working…"

    asyncio.run(main())


def test_in_turn_answer_keeps_prompt_disabled_until_idle():
    """前台回合内的询问：作答后回合仍在跑，输入条由 _set_idle 收尾再恢复。"""
    async def main() -> None:
        async with chat_app(size=(100, 32), runtime_state=RuntimeState()) as (app, pilot):
            app._busy = True
            app._begin_permission("Allow?", [], Future())
            app._answer_permission("yes")
            await pilot.pause()
            assert app.query_one("#prompt").disabled, "回合仍在跑：先别放开输入"

            app._set_idle()
            await pilot.pause()
            assert not app.query_one("#prompt").disabled, "_set_idle 收尾后恢复输入"
            assert _status_text(app) == ""

    asyncio.run(main())


def test_turn_end_resolves_pending_ask_so_asker_is_not_left_blocked():
    """回合结束兜底清理挂起询问时，必须给等待线程一个回答（否则子代理 / 工具线程永久阻塞）。"""
    async def main() -> None:
        async with chat_app(size=(100, 32), runtime_state=RuntimeState()) as (app, _):
            app._busy = True
            perm_future: Future[str] = Future()
            app._begin_permission("Allow?", [], perm_future)
            app._set_idle()
            assert perm_future.done(), "回合收尾必须作答（拒绝），否则等待线程永久阻塞"
            assert perm_future.result(timeout=0) == "no"

            clarify_future: Future[str] = Future()
            app._begin_clarify(["Which one?"], False, clarify_future)
            app._set_idle()
            assert clarify_future.done(), "回合收尾必须取消询问，否则等待线程永久阻塞"
            assert clarify_future.result(timeout=0) == "[User cancelled]"

    asyncio.run(main())
