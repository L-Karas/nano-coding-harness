"""状态行 spinner：Rich Spinner + Textual auto_refresh 轮播，停止后恢复纯文本。"""
import asyncio

from tests.support import chat_app


def test_status_line_spins_until_stopped():
    async def main() -> None:
        async with chat_app(size=(100, 30)) as (app, pilot):
            status = app.query_one("#status")
            status.set_text("Thinking...", spin=True)
            await pilot.pause(0.05)
            first = status.render_line(0).text
            assert first.endswith(" Thinking...") and first != "Thinking...", first
            assert status.auto_refresh is not None

            await pilot.pause(0.3)
            assert status.render_line(0).text != first, "spinner 帧应持续轮播"

            status.set_text("Done", spin=False)
            await pilot.pause(0.1)
            assert status.render_line(0).text == "Done"
            assert status.auto_refresh is None, "停止后应关闭自动重绘"

    asyncio.run(main())
