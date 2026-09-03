import subprocess
from pathlib import Path
from typing import Optional

from pydantic import Field

from src.config import WORKDIR
from tools.tool_base import BaseTool


class Bash(BaseTool):
    """Execute a bash command."""
    command: str = Field(description="The bash command to execute.")
    run_in_background: bool = Field(default=False,
                                    description="Set to true to run the command in the background.")
    agent_level: set = {"main", "sub-agent", "teammate"}


def run_bash(command: str, cwd: Optional[Path] = None, run_in_background: bool = False) -> str:
    """
    run_in_background is consumed by the dispatcher; direct execution ignores it.
    """
    try:
        res = subprocess.run(command, shell=True, capture_output=True, cwd=cwd or WORKDIR, text=True, timeout=120)
        output = (res.stdout + res.stderr).strip()
        return output[:int(5e4)] if output else "(Tool no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout (120s)"
