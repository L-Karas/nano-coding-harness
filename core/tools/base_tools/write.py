from pathlib import Path

import aiofiles
from pydantic import Field

from core.diff import _resolve
from core.file_io import atomic_write, match_eol, normalize_newlines
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool

DESCRIPTION = (
    "Write a whole text file, creating parent directories as needed. `content` replaces the file entirely, "
    "so use `edit_file` for a small change to an existing file. Content is UTF-8; `\\r\\n` is normalized to "
    "the target's line-ending style (an existing file keeps its own style, a new file gets LF) and the "
    "existing file's permissions are preserved."
)


class WriteFile(BaseTool):
    __doc__ = DESCRIPTION
    path: str = Field(description="File path; relative paths resolve against the working directory.")
    content: str = Field(description="Complete file content (UTF-8); it replaces the file.")

    agent_type: set = {"main", "sub-agent"}

    # 不覆写 run：BaseTool.run 即 asyncio.run(arun)，同步与异步入口只有一套语义。

    async def arun(self, tctx: ToolContext | None = None) -> str:
        path = _resolve(self.path, tctx.cwd if tctx else None)
        if path.is_dir():
            raise IsADirectoryError(f"is a directory: {self.path}")
        if tctx:
            tctx.raise_if_cancelled()

        # 先归一再比较：read_file 给出的也是归一后的内容，否则每个 CRLF 文件都会被重写一遍。
        text = normalize_newlines(self.content)
        old = await _read_existing(path)
        if old is not None and normalize_newlines(old) == text:
            return f"No changes to {self.path}."

        body = match_eol(text, old)
        path.parent.mkdir(parents=True, exist_ok=True)
        await atomic_write(path, body, tctx)
        return f"Wrote {len(body.encode('utf-8'))} bytes to {self.path}."


async def _read_existing(path: Path) -> str | None:
    """原样读取已存在的文件（保留 \\r\\n，用于判断无变化与沿用换行风格）；读不到就按新文件处理。"""
    try:
        async with aiofiles.open(path, mode="r", encoding="utf-8", newline="") as f:
            return await f.read()
    except (OSError, UnicodeDecodeError):  # 不存在 / 是目录 / 不可读 / 非 UTF-8
        return None
