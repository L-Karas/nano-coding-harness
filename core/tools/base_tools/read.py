import base64
from pathlib import Path
from typing import Optional, get_args
from urllib.parse import urlsplit

import aiofiles
import anydoc
from pydantic import Field

from core.config import WORKDIR
from core.context.truncate import MAX_OUTPUT_BYTES, MAX_OUTPUT_LINES, truncate_lines
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool
from core.tools.tool_result import ImageAttachment, ToolResult

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

DESCRIPTION = (
    "Read the contents of a file. "
    f"Text files are read as-is; document formats {', '.join(get_args(anydoc.Format))} are converted to Markdown. "
    f"Image files ({', '.join(sorted(IMAGE_MIME_TYPES))}) up to {MAX_IMAGE_BYTES // (1024 * 1024)} MB are read "
    "and attached for vision models; http(s) image URLs are passed through without downloading. "
    "Use this instead of `terminal` (cat/head/sed) to inspect files. "
    f"Text output is capped at {MAX_OUTPUT_LINES} lines by default and at {MAX_OUTPUT_BYTES // 1024} KB, whichever comes first; when truncated, the "
    f"final line reports the omitted amount and the `offset` to continue from. A line over the byte cap is cut — use "
    f"`terminal` to read its remainder. `limit` / `offset` only apply to text."
)


class ReadFile(BaseTool):
    __doc__ = DESCRIPTION
    path: str = Field(description="Path to the file to read, or an http(s) image URL. Relative paths resolve against the working directory; "
                                  "absolute paths are used as-is.")
    limit: int = Field(default=MAX_OUTPUT_LINES,
                       description=f"Maximum number of lines to return (default: {MAX_OUTPUT_LINES}).")
    offset: int = Field(default=1, description="1-indexed line number to start reading from (default: 1); pass the "
                                               "offset printed in the truncation notice to continue reading.")

    agent_type: set = {"main", "sub-agent"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str | ToolResult:
        if _is_http_url(self.path):
            return _http_image_result(self.path, _supports_vision(tctx))

        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        fp = (base / self.path).resolve()
        mime = _image_mime(self.path)
        if mime is not None:
            size = fp.stat().st_size
            if not _supports_vision(tctx):
                return _text_only_image_result(str(fp), mime, f"{size} bytes")
            if size > MAX_IMAGE_BYTES:
                return _too_large(str(fp), size)
            return _image_result(str(fp), mime, f"{size} bytes", _data_url(mime, fp.read_bytes()))

        lines = fp.read_text(encoding="utf-8").splitlines()
        return _slice_content(lines, self.limit, self.offset) or "(Empty file)"

    async def arun(self, tctx: ToolContext | None = None) -> str | ToolResult:
        if _is_http_url(self.path):
            return _http_image_result(self.path, _supports_vision(tctx))

        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        fp = (base / self.path).resolve()
        mime = _image_mime(self.path)
        if mime is not None:
            size = fp.stat().st_size
            if not _supports_vision(tctx):
                return _text_only_image_result(str(fp), mime, f"{size} bytes")
            if size > MAX_IMAGE_BYTES:
                return _too_large(str(fp), size)
            async with aiofiles.open(fp, "rb") as f:
                data = await f.read()
            return _image_result(str(fp), mime, f"{size} bytes", _data_url(mime, data))

        if anydoc.format_from_path(self.path):
            content = anydoc.to_markdown(fp)
        else:
            async with aiofiles.open(fp, "r", encoding="utf-8") as f:
                content = await f.read()
        return _slice_content(content.splitlines(), self.limit, self.offset) or "(Empty file)"


def _supports_vision(tctx: ToolContext | None) -> bool:
    """无上下文（直接调用 / 测试）时按支持视觉处理。"""
    return tctx.vision if tctx is not None else True


def _is_http_url(path: str) -> bool:
    return urlsplit(path).scheme.lower() in ("http", "https")


def _image_mime(path: str) -> str | None:
    # URL 只看 path 部分（去掉 query / fragment）；本地路径原样取后缀，避免 # / ? 截断
    candidate = urlsplit(path).path if _is_http_url(path) else path
    return IMAGE_MIME_TYPES.get(Path(candidate).suffix.lower())


def _data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _image_result(source: str, mime: str, size: str, url: str) -> ToolResult:
    return ToolResult(
        content=f"Image read: {source} ({mime}, {size}).",
        images=(ImageAttachment(source=source, mime=mime, url=url),),
    )


def _too_large(source: str, size: int) -> ToolResult:
    return ToolResult.error(
        f"Image too large: {source} is {size} bytes (limit {MAX_IMAGE_BYTES} bytes).")


VISION_UNSUPPORTED_MESSAGE = "Current model does not support vision input; image not attached."


def _text_only_image_result(source: str, mime: str, size: str) -> ToolResult:
    return ToolResult(
        content=f"Image read: {source} ({mime}, {size}). {VISION_UNSUPPORTED_MESSAGE}")


def _http_image_result(path: str, vision: bool) -> ToolResult:
    mime = _image_mime(path)
    if mime is None:
        raise ValueError(f"Unsupported image URL: {path}")
    if not vision:
        return _text_only_image_result(path, mime, "remote URL")
    return _image_result(path, mime, "remote URL", path)


def _slice_content(lines: list[str], limit: Optional[int], offset: Optional[int]) -> str:
    """薄包装：保留模块级 MAX_OUTPUT_BYTES 供测试 / 调用方调整，逻辑在 core.context.truncate。"""
    text, _ = truncate_lines(
        lines,
        limit,
        offset,
        MAX_OUTPUT_BYTES,
    )
    return text
