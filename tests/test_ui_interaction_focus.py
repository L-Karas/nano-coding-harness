"""停靠区交互的焦点守卫：权限确认 / clarify / clarify-Other 挂起期间，
点击聊天区等其它区域不得夺走键盘焦点，↑/↓/Enter 仍须作用于当前交互
（对照 / 指令与 @ 文件候选：焦点始终归输入条，点击不会中断候选导航）。
"""
import asyncio
from concurrent.futures import Future

from textual.widgets import OptionList

from core.tui.ui_textual import ChatApp


def _app() -> ChatApp:
    return ChatApp(handle_query=lambda query: None)


def test_permission_click_elsewhere_keeps_list_focused():
    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._add_card("user", "hello")  # 让 #chat 可见（欢迎态下隐藏）
            await pilot.pause()
            future: Future[str] = Future()
            app._begin_permission("Allow?", future)
            await pilot.pause()
            perm = app.query_one("#perm-list", OptionList)
            assert app.focused is perm and perm.highlighted == 1, "初始聚焦权限列表，默认 No"

            await pilot.click("#chat")
            await pilot.pause()
            assert app.focused is perm, "点击其他区域后权限列表必须保持焦点"

            await pilot.press("up")  # 仍能用方向键切到 Yes
            await pilot.pause()
            assert perm.highlighted == 0, "点击其他区域后 ↑/↓ 仍须能切换选项"
            await pilot.press("enter")
            await pilot.pause()
            assert future.result(timeout=0) == "yes", "点击其他区域后 Enter 仍须能作答"
    asyncio.run(main())


def test_clarify_click_elsewhere_keeps_list_focused():
    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._add_card("user", "hello")
            await pilot.pause()
            app._begin_clarify(["A", "B", "C"], False, Future())
            await pilot.pause()
            clarify = app.query_one("#clarify-list")
            assert app.focused is clarify and clarify.highlighted == 0

            await pilot.click("#chat")
            await pilot.pause()
            assert app.focused is clarify, "点击其他区域后 clarify 列表必须保持焦点"

            await pilot.press("down")
            await pilot.pause()
            assert clarify.highlighted == 1, "点击其他区域后 ↑/↓ 仍须能切换选项"
    asyncio.run(main())


def test_clarify_other_click_elsewhere_keeps_prompt_focused():
    async def main() -> None:
        app = _app()
        async with app.run_test(size=(100, 32)) as pilot:
            app._add_card("user", "hello")
            await pilot.pause()
            app._begin_clarify(["A"], False, Future())
            await pilot.pause()
            await pilot.press("down")  # 高亮末行 Other
            await pilot.press("enter")  # 进入自定义输入模式
            await pilot.pause()
            prompt = app.query_one("#prompt")
            assert app.focused is prompt and not prompt.disabled

            await pilot.click("#chat")
            await pilot.pause()
            assert app.focused is prompt, "Other 输入模式点击其他区域后输入条必须保持焦点"

            await pilot.press("o", "k")
            await pilot.pause()
            assert prompt.text == "ok", "点击其他区域后仍须能键入自定义答案"
    asyncio.run(main())
