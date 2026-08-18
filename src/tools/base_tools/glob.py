from pathlib import Path
from typing import Optional

from pydantic import Field

from config import WORKDIR
from tools.tool_base import BaseTool


class Glob(BaseTool):
    """Find files matching a glob pattern."""
    pattern: str = Field(description="The glob pattern to match files against (e.g. '**/*.py').")

    agent_level: str = "sub"


def run_glob(pattern: str, cwd: Optional[Path] = None) -> str:
    import glob as g
    try:
        base = cwd or WORKDIR
        results = []
        for match in g.glob(pattern, root_dir=base):
            if (base / match).resolve().is_relative_to(base):
                results.append(match)

        return "\n".join(results) if results else "(No matches)"
    except Exception as e:
        return f"Error: {e}"
