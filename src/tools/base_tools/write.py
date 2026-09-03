from pathlib import Path
from typing import Optional

from pydantic import Field

from tools.base_tools.git import _resolve, _read_old
from tools.tool_base import BaseTool


class WriteFile(BaseTool):
    """Write content to a file."""
    path: str = Field(description="Path to the file to write.")
    content: str = Field(description="Content to write to the file.")

    agent_level: set = {"main", "sub-agent", "teammate"}


def run_write_file(path: str, content: str, cwd: Optional[Path] = None) -> str:
    try:
        fp = _resolve(path, cwd)
        if fp.exists() and _read_old(fp) == content:
            return f"No changes to {path}."
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}."
    except Exception as e:
        return f"Error: {e}"
