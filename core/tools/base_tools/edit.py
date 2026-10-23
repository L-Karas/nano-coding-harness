import asyncio
import os
import stat
import tempfile
from contextlib import suppress

import aiofiles
from pydantic import Field

from core.diff import _resolve
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool

DESCRIPTION = (
    "Replace one exact, unique occurrence of old_text with new_text in an existing text file. "
    "old_text must match exactly (including whitespace / indentation) and must occur only once; "
    "include enough surrounding context to make it unique. An empty new_text deletes the match."
)


class EditFile(BaseTool):
    __doc__ = DESCRIPTION

    path: str = Field(description="Path to the file to edit.")
    old_text: str = Field(description="Exact non-empty text to replace; it must occur exactly once.")
    new_text: str = Field(description="Replacement text; an empty string deletes old_text.")

    agent_type: set = {"main", "sub-agent"}

    # 不覆写 run：BaseTool.run 即 asyncio.run(arun)，同步与异步入口只有一套语义。

    async def arun(self, tctx: ToolContext | None = None) -> str:
        path = _resolve(self.path, tctx.cwd if tctx else None)
        if not self.old_text:
            raise ValueError("old_text must not be empty")
        if tctx:
            tctx.raise_if_cancelled()

        try:
            async with aiofiles.open(path, mode="r", encoding="utf-8", newline="") as f:
                original = await f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"file not found: {self.path}") from None

        # read_file 把 CRLF 归一成 \n，这里匹配前同样归一，写回时再还原原文件的换行风格。
        text = original.replace("\r\n", "\n")
        old = self.old_text.replace("\r\n", "\n")
        new = self.new_text.replace("\r\n", "\n")

        first = text.find(old)
        if first < 0:
            raise ValueError(f"old_text not found in {self.path}")
        second = text.find(old, first + 1)  # +1 而非 +len：重叠匹配同样算多义
        if second >= 0:
            first_line = text.count("\n", 0, first) + 1
            second_line = text.count("\n", 0, second) + 1
            raise ValueError(
                f"old_text occurs multiple times in {self.path} "
                f"(lines {first_line}, {second_line}); include more surrounding context to make it unique"
            )

        updated = text[:first] + new + text[first + len(old):]
        if updated == text:
            return f"No changes to {self.path}."

        crlf = original.count("\r\n")
        # 按多数行选择整文件 EOL：纯 LF / 纯 CRLF 原样保留，混合换行统一为多数风格。
        eol = "\r\n" if crlf > original.count("\n") - crlf else "\n"
        if eol != "\n":
            updated = updated.replace("\n", eol)

        # Windows 只有只读位，mkstemp 的权限已足够；POSIX 需恢复 mode，避免 0600 覆盖原权限。
        mode = stat.S_IMODE(path.stat().st_mode) if os.name == "posix" else None
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            os.close(fd)
            if tctx:
                tctx.raise_if_cancelled()
            async with aiofiles.open(tmp, mode="w", encoding="utf-8", newline="") as f:
                await f.write(updated)
                await f.flush()
                if mode is not None:
                    os.chmod(tmp, mode)
                await asyncio.to_thread(os.fsync, f.fileno())

            if tctx:
                tctx.raise_if_cancelled()
            os.replace(tmp, path)
            tmp = None
            return f"Edited {self.path}."
        finally:
            if tmp is not None:
                with suppress(OSError):
                    os.unlink(tmp)
