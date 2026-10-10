import os
import tempfile

import aiofiles
from pydantic import Field

from core.runtime_context import ToolContext
from core.tools.base_tools.diff import _resolve, _read_old
from core.tools.tool_base import BaseTool


class EditFile(BaseTool):
    """Find and replace text in a file."""
    path: str = Field(description="Path to the file to edit.")
    old_text: str = Field(description="The exact text to find and replace.")
    new_text: str = Field(description="The replacement text.")

    agent_type: set = {"main", "sub-agent", "teammate"}

    def run(self, tctx: ToolContext | None = None) -> str:
        fp = _resolve(self.path, tctx.cwd if tctx else None)
        text = _read_old(fp)
        if self.old_text not in text:
            raise Exception(f"text not found in {self.path}")
        fp.write_text(text.replace(self.old_text, self.new_text, 1), encoding="utf-8")
        return "Edited successfully."

    async def arun(self, tctx: ToolContext | None = None) -> str:
        path = _resolve(self.path, tctx.cwd if tctx else None)

        try:
            if tctx:
                tctx.raise_if_cancelled()
            async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
                original_content = await f.read()
        except FileNotFoundError as e:
            raise e

        count = original_content.count(self.old_text)
        if count == 0:
            raise Exception(f"the text not found in the {path}")
        if count > 1:
            raise Exception(f"The text appears multiple times in the {path}")

        updated_text = original_content.replace(self.old_text, self.new_text, 1)

        file_dir = path.parent
        fd, tmp = tempfile.mkstemp(dir=file_dir, text=True)
        os.close(fd)
        try:
            if tctx:
                tctx.raise_if_cancelled()
            async with aiofiles.open(tmp, mode="w", encoding="utf-8") as f:
                await f.write(updated_text)
                await f.flush()
                os.fsync(f.fileno())

            if tctx:
                tctx.raise_if_cancelled()
            os.replace(tmp, path)
            return "Edited successfully."
        except BaseException:
            # AgentInterrupted 是 CancelledError，不是 Exception；漏了会残留临时文件
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
