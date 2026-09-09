import locale
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool


class Bash(BaseTool):
    """Execute a bash command."""
    command: str = Field(description="The bash command to execute.")
    run_in_background: bool = Field(default=False,
                                    description="Set to true to run the command in the background.")
    agent_type: set = {"main", "sub-agent", "teammate"}


def _to_text(data: bytes) -> str:
    """Decode tool output: git and most unix tools emit UTF-8, native Windows tools the locale codec."""
    for enc in ("utf-8", locale.getpreferredencoding()):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def run_bash(command: str, cwd: Optional[Path] = None, run_in_background: bool = False) -> str:
    """
    run_in_background is consumed by the dispatcher; direct execution ignores it.
    """
    # text=True decodes in a reader thread: on Windows (gbk locale) git's UTF-8 output
    # kills that thread and communicate() returns stdout=None. Capture bytes, decode here.
    res = subprocess.run(command, shell=True, capture_output=True, cwd=cwd or WORKDIR, timeout=120)
    output = (_to_text(res.stdout) + _to_text(res.stderr)).strip()
    return output[:int(5e4)] if output else "(Tool no output)"
