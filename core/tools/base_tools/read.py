from typing import Optional, get_args

import aiofiles
import anydoc
from pydantic import Field

from core.config import WORKDIR
from core.context.truncate import MAX_OUTPUT_BYTES, MAX_OUTPUT_LINES, truncate_lines
from core.runtime_context import ToolContext
from core.tools import vision
from core.tools.tool_base import BaseTool
from core.tools.tool_result import ToolResult

DESCRIPTION = (
    "Read a file. Text is returned as-is; documents "
    f"({', '.join(get_args(anydoc.Format))}) are converted to Markdown; images "
    f"({', '.join(sorted(ext.lstrip('.') for ext in vision.IMAGE_MIME_TYPES))}) are attached for vision models. "
    "Use this instead of `terminal` (cat/head/sed) to inspect files. "
    f"Output is capped at {MAX_OUTPUT_LINES} lines / {MAX_OUTPUT_BYTES // 1024} KB, whichever comes first; the "
    "final line reports how to continue with `offset`."
)


class ReadFile(BaseTool):
    __doc__ = DESCRIPTION
    path: str = Field(
        description="File path or http(s) image URL; relative paths resolve against the working directory.")
    limit: int = Field(default=MAX_OUTPUT_LINES,
                       description=f"Max lines to return (default: {MAX_OUTPUT_LINES}).")
    offset: int = Field(default=1, description="1-indexed line to start from (default: 1); continue with the "
                                               "offset from the truncation notice.")

    agent_type: set = {"main", "sub-agent"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str | ToolResult:
        vision_enabled = vision.supports_vision(tctx)
        if vision.is_http_url(self.path):
            return vision.url_image_result(self.path, vision=vision_enabled)

        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        fp = (base / self.path).resolve()
        if vision.image_mime(self.path) is not None:
            return vision.local_image_result(fp, vision=vision_enabled)

        if anydoc.format_from_path(self.path):
            content = anydoc.to_markdown(fp)
        else:
            content = fp.read_text(encoding="utf-8")

        return _slice_content(content.splitlines(), self.limit, self.offset) or "(Empty file)"

    async def arun(self, tctx: ToolContext | None = None) -> str | ToolResult:
        vision_enabled = vision.supports_vision(tctx)
        if vision.is_http_url(self.path):
            return vision.url_image_result(self.path, vision=vision_enabled)

        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        fp = (base / self.path).resolve()
        if vision.image_mime(self.path) is not None:
            return await vision.alocal_image_result(fp, vision=vision_enabled)

        if anydoc.format_from_path(self.path):
            content = anydoc.to_markdown(fp)
        else:
            async with aiofiles.open(fp, "r", encoding="utf-8") as f:
                content = await f.read()
        return _slice_content(content.splitlines(), self.limit, self.offset) or "(Empty file)"


def _slice_content(lines: list[str], limit: Optional[int], offset: Optional[int]) -> str:
    """薄包装：保留模块级 MAX_OUTPUT_BYTES 供测试 / 调用方调整，逻辑在 core.context.truncate。"""
    text, _ = truncate_lines(
        lines,
        limit,
        offset,
        MAX_OUTPUT_BYTES,
    )
    return text
