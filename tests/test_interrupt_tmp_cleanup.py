"""中断落点若在 mkstemp 之后，edit/write 必须删掉临时文件再抛。

AgentInterrupted 继承 CancelledError（BaseException），`except Exception` 抓不到它——
清理分支写窄了就会在仓库里留下 tmpxxxx 残骸。"""
import asyncio

from core.runtime_context import AgentRunContext, AgentInterrupted, ToolContext
from core.tools.base_tools.edit import EditFile
from core.tools.base_tools.write import WriteFile


def test_interrupt_after_mkstemp_removes_tmp(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    async def run(call):
        ctx = AgentRunContext()
        asyncio.get_running_loop().call_soon(ctx.cancel)  # 首个检查点放行，第二个检查点前中断
        try:
            await call(ctx)
            raise AssertionError("已取消的 ctx 必须抛 AgentInterrupted")
        except AgentInterrupted:
            pass

    asyncio.run(run(lambda ctx: EditFile(path="a.txt", old_text="hello", new_text="hi").arun(
        ToolContext(agent_run=ctx, cwd=tmp_path))))
    asyncio.run(run(lambda ctx: WriteFile(path="a.txt", content="hi").arun(
        ToolContext(agent_run=ctx, cwd=tmp_path))))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
