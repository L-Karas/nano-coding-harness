from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool


class ReadFile(BaseTool):
    """Read a file from the filesystem."""
    path: str = Field(description="Path to the file to read.")
    limit: int = Field(default=2000, description="Max lines to read.")
    offset: int = Field(default=0, description="Line offset to start reading from.")

    agent_type: set = {"main", "sub-agent", "teammate"}


def run_read_file(path: str, limit: Optional[int] = 2000, offset: Optional[int] = 0, cwd: Optional[Path] = None) -> str:
    base = cwd or WORKDIR
    fp = (base / path).resolve()
    lines = fp.read_text(encoding="utf-8").splitlines()
    offset = max(int(offset or 0), 0)
    limit = int(limit) if limit is not None else None
    lines = lines[offset:]
    if limit is not None and limit < len(lines):
        lines = lines[:limit] + [f"[Truncated ({len(lines) - limit}) more lines. Use 'offset={offset + limit - 1}' to continue.]"]
    return "\n".join(lines)
