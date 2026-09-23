import os
import tempfile
from pathlib import Path
from typing import Optional

import aiofiles
from pydantic import Field

from core.tools.base_tools.git import _resolve, _read_old
from core.tools.tool_base import BaseTool


class WriteFile(BaseTool):
    """Write content to a file."""
    path: str = Field(description="Path to the file to write.")
    content: str = Field(description="Content to write to the file.")

    agent_type: set = {"main", "sub-agent", "teammate"}


def run_write_file(path: str, content: str, cwd: Optional[Path] = None) -> str:
    fp = _resolve(path, cwd)
    if fp.exists() and _read_old(fp) == content:
        return f"No changes to {path}."
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}."


async def run_write_file_async(path: str, content: str, cwd: Optional[Path] = None, ctx=None) -> str:
    path = _resolve(path, cwd)
    file_dir = path.parent
    file_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file_dir, text=True)
    os.close(fd)

    try:
        if ctx:
            ctx.raise_if_cancelled()
        async with aiofiles.open(tmp, mode="w", encoding="utf-8") as f:
            await f.write(content)

        if ctx:
            ctx.raise_if_cancelled()
        os.replace(tmp, path)

        return f"Written successfully."
    except BaseException:  # AgentInterrupted 是 CancelledError，不是 Exception；漏了会残留临时文件
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
