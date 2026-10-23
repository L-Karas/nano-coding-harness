"""rg 子进程封装：`glob` 与 `grep` 共用，负责可用性探测、输出解码与取消清理。

rg 的输出固定是 UTF-8，所以这里直接按 UTF-8 有损解码，不跟随本机代码页——否则 cp1252 这类
单字节 locale 会把非 UTF-8 文件的内容解成 `é` 而不是 `U+FFFD`，与回退分支分叉。
"""
import asyncio
import shutil

from core.runtime_context import ToolContext


def has_ripgrep() -> bool:
    return bool(shutil.which("rg"))


async def run_rg(args: list[str], tctx: ToolContext | None = None) -> tuple[str, str, int]:
    """跑 `rg <args>`，返回 `(stdout, stderr, returncode)`；取消时先 terminate，超时再 kill。"""
    process = await asyncio.create_subprocess_exec(
        "rg", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await process.communicate()
        if tctx:
            tctx.raise_if_cancelled()
    except asyncio.CancelledError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        raise
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    assert process.returncode is not None  # communicate / finally 之后必有值
    return (stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            process.returncode)
