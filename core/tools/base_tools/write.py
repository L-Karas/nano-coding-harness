import os
import tempfile

import aiofiles
from pydantic import Field

from core.runtime_context import ToolContext
from core.tools.base_tools.diff import _resolve, _read_old
from core.tools.tool_base import BaseTool


class WriteFile(BaseTool):
    """Write content to a file."""
    path: str = Field(description="Path to the file to write.")
    content: str = Field(description="Content to write to the file.")

    agent_type: set = {"main", "sub-agent", "teammate"}

    def run(self, tctx: ToolContext | None = None) -> str:
        fp = _resolve(self.path, tctx.cwd if tctx else None)
        if fp.exists() and _read_old(fp) == self.content:
            return f"No changes to {self.path}."
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(self.content, encoding="utf-8")
        return f"Wrote {len(self.content)} bytes to {self.path}."

    async def arun(self, tctx: ToolContext | None = None) -> str:
        path = _resolve(self.path, tctx.cwd if tctx else None)
        file_dir = path.parent
        file_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=file_dir, text=True)
        os.close(fd)

        try:
            if tctx:
                tctx.raise_if_cancelled()
            async with aiofiles.open(tmp, mode="w", encoding="utf-8") as f:
                await f.write(self.content)

            if tctx:
                tctx.raise_if_cancelled()
            os.replace(tmp, path)

            return f"Written successfully."
        except BaseException:  # AgentInterrupted 是 CancelledError，不是 Exception；漏了会残留临时文件
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
