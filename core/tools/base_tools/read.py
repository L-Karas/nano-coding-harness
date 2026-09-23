import asyncio
from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool


class ReadFile(BaseTool):
    """Read a file from the filesystem."""
    path: str = Field(description="Path to the file to read.")
    limit: int = Field(default=2000, description="Max lines to read.")
    offset: int = Field(default=1, description="Line offset to start reading from (1-indexed).")

    agent_type: set = {"main", "sub-agent", "teammate"}


# todo: 可以为工具增加读取范围的功能
def run_read_file(path: str, limit: Optional[int] = 2000, offset: Optional[int] = 1, cwd: Optional[Path] = None) -> str:
    base = cwd or WORKDIR
    fp = (base / path).resolve()
    lines = fp.read_text(encoding="utf-8").splitlines()

    offset = max(int(offset or 1) - 1, 0)  # 1 起始转 0 起始（None/旧 0 起始调用方 → 0）
    limit = int(limit) if limit is not None else None
    lines = lines[offset:]
    if limit is not None and limit < len(lines):
        lines = lines[:limit] + [
            f"[Truncated ({len(lines) - limit}) more lines. Use 'offset={offset + limit - 1}' to continue.]"]
    return "\n".join(lines)


async def run_read_file_async(
        path: str,
        limit: Optional[int] = 2000,
        offset: Optional[int] = 1,
        cwd: Optional[Path] = None,
        ctx=None
) -> str:
    if ctx:
        ctx.raise_if_cancelled()
    # to_thread：读大文件不能卡住事件循环（卡住时 task.cancel() 也送不进来）
    result = await asyncio.to_thread(run_read_file, path, limit, offset, cwd)
    if ctx:
        ctx.raise_if_cancelled()
    return result
