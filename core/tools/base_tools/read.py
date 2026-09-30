from pathlib import Path
from typing import Optional

import aiofiles
import anydoc
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
            f"[Truncated ({len(lines) - limit}) more lines. Use 'offset={offset + limit + 1}' to continue.]"]
    return "\n".join(lines)


async def run_read_file_async(
        path: str,
        limit: Optional[int] = 2000,
        offset: Optional[int] = 1,
        cwd: Optional[Path] = None,
        ctx=None
) -> str:
    base = cwd or WORKDIR
    fp = (base / path).resolve()

    if anydoc.format_from_path(path):
        content = anydoc.to_markdown(fp)
    else:
        async with aiofiles.open(fp, "r", encoding="utf-8") as f:
            content = await f.read()
    lines = content.splitlines()
    offset = max(int(offset or 1) - 1, 0)
    lines = lines[offset:]
    if limit is not None and limit < len(lines):
        lines = lines[:limit] + [
            f"[Truncated ({len(lines) - limit}) more lines. Use 'offset={offset + limit + 1}' to continue.]"
        ]
    return "\n".join(lines)
