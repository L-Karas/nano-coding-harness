from pathlib import Path
from typing import Optional, get_args

import aiofiles
import anydoc
from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool

MAX_BYTES = 50 * 1024  # 50 KB 单次返回上限，防止超长行绕过 limit

DESCRIPTION = (
    "Read the contents of a file. "
    f"Text files are read as-is; document formats {', '.join(get_args(anydoc.Format))} are converted to Markdown. "
    "Use this instead of `terminal` (cat/head/sed) to inspect files. "
    f"Output is capped at 2000 lines by default and at {MAX_BYTES // 1024} KB, whichever comes first; when truncated, the "
    f"final line reports the omitted amount and the `offset` to continue from. A line over the byte cap is cut — use "
    f"`terminal` to read its remainder."
)


class ReadFile(BaseTool):
    __doc__ = DESCRIPTION
    path: str = Field(description="Path to the file to read. Relative paths resolve against the working directory; "
                                  "absolute paths are used as-is.")
    limit: int = Field(default=2000, description="Maximum number of lines to return (default: 2000).")
    offset: int = Field(default=1, description="1-indexed line number to start reading from (default: 1); pass the "
                                               "offset printed in the truncation notice to continue reading.")

    agent_type: set = {"main", "sub-agent", "teammate"}


def _slice_content(lines: list[str], limit: Optional[int], offset: Optional[int]) -> str:
    """行数 / 字节上限，任一先到即停；停处附截断提示，offset 指向下一条未完整返回的行。"""
    offset = max(int(offset or 1) - 1, 0)  # 1 起始转 0 起始（None/旧 0 起始调用方 → 0）
    limit = int(limit) if limit is not None else None
    rest = lines[offset:]

    out: list[str] = []
    used, cut = 0, None
    for line_no, line in enumerate(rest, start=offset + 1):
        if limit is not None and len(out) >= limit:
            break
        enc = line.encode("utf-8")
        if used + len(enc) + 1 > MAX_BYTES:  # +1 为换行
            if used < MAX_BYTES:  # 本行放不下：按剩余额度 UTF-8 安全截断
                shown = enc[:MAX_BYTES - used].decode("utf-8", "ignore")
                out.append(shown)
                cut = (line_no, len(enc) - len(shown.encode("utf-8")))
            break
        out.append(line)
        used += len(enc) + 1

    remaining = len(rest) - len(out)
    next_offset = offset + len(out) + 1
    # ponytail: 被切掉的行尾无法用 offset 续读（offset 是行粒度），需要时用 terminal 读
    if cut:
        line_no, omitted = cut
        note = f"[Truncated: line {line_no} cut at {MAX_BYTES // 1024} KB, {(omitted + 1023) // 1024} KB omitted"
        if remaining:
            note += f"; {remaining} more lines, continue with 'offset={next_offset}'"
        out.append(note + ".]")
    elif remaining:
        out.append(f"[Truncated ({remaining}) more lines. Use 'offset={next_offset}' to continue.]")
    return "\n".join(out)


def run_read_file(path: str, limit: Optional[int] = 2000, offset: Optional[int] = 1, cwd: Optional[Path] = None) -> str:
    base = cwd or WORKDIR
    fp = (base / path).resolve()
    lines = fp.read_text(encoding="utf-8").splitlines()
    return _slice_content(lines, limit, offset) or "(Empty file)"


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
    return _slice_content(content.splitlines(), limit, offset) or "(Empty file)"
