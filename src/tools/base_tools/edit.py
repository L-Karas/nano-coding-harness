from pathlib import Path
from typing import Optional

from pydantic import Field

from src.tools.base_tools.git import _resolve, _read_old
from src.tools.tool_base import BaseTool


class EditFile(BaseTool):
    """Find and replace text in a file."""
    path: str = Field(description="Path to the file to edit.")
    old_text: str = Field(description="The exact text to find and replace.")
    new_text: str = Field(description="The replacement text.")

    agent_level: set = {"main", "sub-agent", "teammate"}


def run_edit_file(path: str, old_text: str, new_text: str, cwd: Optional[Path] = None) -> str:
    try:
        fp = _resolve(path, cwd)
        text = _read_old(fp)
        if old_text not in text:
            return f"Error: text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return "Edited successfully."
    except Exception as e:
        return f"Error: {e}"
