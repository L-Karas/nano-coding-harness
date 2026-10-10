from pathlib import Path
from typing import Optional, get_args

import aiofiles
import anydoc
from pydantic import Field

from core.config import WORKDIR
from core.context.truncate import MAX_OUTPUT_BYTES, MAX_OUTPUT_LINES, truncate_lines
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool

DESCRIPTION = (
    "Read the contents of a file. "
    f"Text files are read as-is; document formats {', '.join(get_args(anydoc.Format))} are converted to Markdown. "
    "Use this instead of `terminal` (cat/head/sed) to inspect files. "
    f"Output is capped at {MAX_OUTPUT_LINES} lines by default and at {MAX_OUTPUT_BYTES // 1024} KB, whichever comes first; when truncated, the "
    f"final line reports the omitted amount and the `offset` to continue from. A line over the byte cap is cut — use "
    f"`terminal` to read its remainder."
)


class ReadFile(BaseTool):
    __doc__ = DESCRIPTION
    path: str = Field(description="Path to the file to read. Relative paths resolve against the working directory; "
                                  "absolute paths are used as-is.")
    limit: int = Field(default=MAX_OUTPUT_LINES,
                       description=f"Maximum number of lines to return (default: {MAX_OUTPUT_LINES}).")
    offset: int = Field(default=1, description="1-indexed line number to start reading from (default: 1); pass the "
                                               "offset printed in the truncation notice to continue reading.")

    agent_type: set = {"main", "sub-agent", "teammate"}

    def run(self, tctx: ToolContext | None = None) -> str:
        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        fp = (base / self.path).resolve()
        lines = fp.read_text(encoding="utf-8").splitlines()
        return _slice_content(lines, self.limit, self.offset) or "(Empty file)"

    async def arun(self, tctx: ToolContext | None = None) -> str:
        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        fp = (base / self.path).resolve()

        if anydoc.format_from_path(self.path):
            content = anydoc.to_markdown(fp)
        else:
            async with aiofiles.open(fp, "r", encoding="utf-8") as f:
                content = await f.read()
        return _slice_content(content.splitlines(), self.limit, self.offset) or "(Empty file)"


def _slice_content(lines: list[str], limit: Optional[int], offset: Optional[int]) -> str:
    """薄包装：保留模块级 MAX_OUTPUT_BYTES 供测试 / 调用方调整，逻辑在 core.context.truncate。"""
    text, _ = truncate_lines(lines, limit, offset, MAX_OUTPUT_BYTES)
    return text
