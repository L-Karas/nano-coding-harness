import re
from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool


class Grep(BaseTool):
    """Search for a pattern in files within a directory, returning matching lines with file path, line number (1-indexed), and content."""
    pattern: str = Field(description="The regex pattern to search for in file contents.")
    path: str = Field(description="The directory to search in. Defaults to the current working directory.")
    file_pattern: str = Field(default="*", description="Glob pattern to filter file names (e.g., '*.txt', '*.py'). "
                                                       "Default is '*' (all files).")

    agent_type: set = {"main", "sub-agent", "teammate"}


def run_grep(pattern: str, path: str = "", file_pattern: str = "*", cwd: Optional[Path] = None) -> str:
    base = cwd or WORKDIR
    path = Path(path)
    regex = re.compile(pattern)

    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        raise Exception(f"path '{path}' does not exist.")
    if not path.is_dir():
        path = path.parent

    if not path.is_relative_to(base):
        raise Exception(f"path '{path}' escapes work directory '{base}'.")

    iterator = path.rglob(file_pattern)
    output = []
    for file_path in iterator:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if regex.search(line):
                        output.append(f"file path: \"{file_path}\", line: [{line_no}], content: \"{line}\"")
        except Exception:
            continue

    if len(output) > 50:
        output = output[:50] + ["Results truncated. More than 50 matches found. "
                                "Consider a more specific path or pattern if needed."]

    return "\n".join(output) if output else "(No matches found)"
