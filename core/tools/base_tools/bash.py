import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text


class Bash(BaseTool):
    """Execute a bash command."""
    command: str = Field(description="The bash command to execute.")
    should_run_in_background: bool = Field(default=False,
                                           description="Set to true to run the command in the background.")
    agent_type: set = {"main", "sub-agent", "teammate"}


def run_bash(command: str, cwd: Optional[Path] = None, should_run_in_background: bool = False) -> str:
    """
    should_run_in_background is consumed by the dispatcher; direct execution ignores it.
    """
    # text=True decodes in a reader thread: on Windows (gbk locale) git's UTF-8 output
    # kills that thread and communicate() returns stdout=None. Capture bytes, decode here.
    res = subprocess.run(command, shell=True, capture_output=True, cwd=cwd or WORKDIR, timeout=120)
    output = (_to_text(res.stdout) + _to_text(res.stderr)).strip()
    return output[:int(5e4)] if output else "(Tool no output)"


async def run_bash_async(command: str, cwd: Optional[Path] = None, ctx=None):
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        shell=True,
        cwd=cwd or WORKDIR,
    )

    try:
        out, error = await process.communicate()
        if ctx:
            ctx.raise_if_cancelled()
        output = (_to_text(out) + _to_text(error)).strip()
        return output[:int(5e4)] if output else "(Tool no output)"
    except asyncio.CancelledError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=120)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        raise
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
