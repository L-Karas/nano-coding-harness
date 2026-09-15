import asyncio
from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text


class Glob(BaseTool):
    """Find files matching a glob pattern."""
    pattern: str = Field(description="The glob pattern to match files against (e.g. '**/*.py').")

    agent_type: set = {"main", "sub-agent", "teammate"}


def has_ripgrep() -> bool:
    import shutil
    return bool(shutil.which("ripgrep"))


def run_glob(pattern: str, cwd: Optional[Path] = None) -> str:
    import glob as g
    root = cwd or WORKDIR
    results = ["[Matches]\n"]
    for match in g.glob(pattern, root_dir=root):
        if (root / match).resolve().is_relative_to(root):
            results.append(match)

    return "\n".join(results) if results else "(No matches)"


async def run_glob_async(pattern: str, cwd: Optional[Path] = None, ctx=None, use_ignore: bool = True) -> str:
    if ctx:
        ctx.raise_if_cancelled()

    if not has_ripgrep():
        return run_glob(pattern, cwd)

    root = cwd or WORKDIR
    command = ["rg", "--files", "-0"]
    if not use_ignore:
        command += ["--no-ignore", "--hidden"]
    command += ["-g", pattern, root]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    outputs = ["[Matches]\n"]
    try:
        stdout, stderr = await process.communicate()
        if ctx:
            ctx.raise_if_cancelled()
        if process.returncode not in (0, 1):
            raise RuntimeError(_to_text(stderr))

        paths = [_to_text(line) for line in stdout.split(b"\0") if p]
        outputs.extend(paths)
    except asyncio.CancelledError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        raise
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    return "\n".join(outputs) if outputs else "(No matches)"
