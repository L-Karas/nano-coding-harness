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
    "Read the contents of a file. "
    f"Text files are read as-is; document formats {', '.join(get_args(anydoc.Format))} are converted to Markdown. "
    f"Image files ({', '.join(sorted(vision.IMAGE_MIME_TYPES))}) are attached for vision models "
    f"(over {vision.MAX_IMAGE_BYTES // (1024 * 1024)} MB they are downscaled to fit); "
    "http(s) image URLs are passed through without downloading. "
    "Use this instead of `terminal` (cat/head/sed) to inspect files. "
    f"Text output is capped at {MAX_OUTPUT_LINES} lines by default and at {MAX_OUTPUT_BYTES // 1024} KB, whichever comes first; when truncated, the "
    f"final line reports the omitted amount and the `offset` to continue from. A line over the byte cap is cut — use "
    f"`terminal` to read its remainder. `limit` / `offset` only apply to text."
)


class ReadFile(BaseTool):
    __doc__ = DESCRIPTION
    path: str = Field(
        description="Path to the file to read, or an http(s) image URL. "
                    "Relative paths resolve against the working directory; absolute paths are used as-is.")
    limit: int = Field(default=MAX_OUTPUT_LINES,
                       description=f"Maximum number of lines to return (default: {MAX_OUTPUT_LINES}).")
    offset: int = Field(default=1, description="1-indexed line number to start reading from (default: 1); pass the "
                                               "offset printed in the truncation notice to continue reading.")

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
