import asyncio
import re
import shutil
from pathlib import Path
from typing import Optional, Literal

from pydantic import Field

from core.config import WORKDIR
from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text


class Grep(BaseTool):
    """Search for a pattern in files within a directory, returning matching lines with file path, line number (1-indexed), and content."""
    pattern: str = Field(description="The regex pattern to search for in file contents.")
    path: str = Field(description="The directory to search in. Defaults to the current working directory.")
    file_pattern: str = Field(default="*", description="Glob pattern to filter file names (e.g., '*.txt', '*.py'). "
                                                       "Default is '*' (all files).")

    agent_type: set = {"main", "sub-agent", "teammate"}


_MATCH_RE = re.compile(r"^(.*?):(\d+):(.*)$")
_MATCH_LIMIT = 50


def _find_grep_tool() -> Literal["grep", "ripgrep", "python"]:
    if shutil.which("rg"):
        return "ripgrep"
    if shutil.which("grep"):
        return "grep"
    return "python"


def _validate_path(path: str = "", cwd: Optional[Path] = None) -> Path:
    base = cwd or WORKDIR
    path = Path(path)

    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        raise Exception(f"path '{path}' does not exist.")
    if not path.is_dir():
        path = path.parent

    if not path.is_relative_to(base):
        raise Exception(f"path '{path}' escapes work directory '{base}'.")

    return path


def _parse_match(lines: list[str]) -> list[tuple[str, str, str]]:
    matches = []
    for line in lines:
        m = _MATCH_RE.match(line)
        if m:
            matches.append(m.groups())
    return matches


def _format_matches(output: list[str]) -> str:
    if len(output) > _MATCH_LIMIT:
        output = output[:_MATCH_LIMIT] + ["Results truncated. More than 50 matches found. "
                                          "Consider a more specific path or pattern if needed."]
    return "\n".join(output)


def run_grep(pattern: str, path: str = "", file_pattern: str = "*", cwd: Optional[Path] = None) -> str:
    path = _validate_path(path, cwd)
    regex = re.compile(pattern)

    output = ["[Matches]\n"]
    for file_path in path.rglob(file_pattern):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if regex.search(line):
                        output.append(f"file path: \"{file_path}\", line: [{line_no}], content: \"{line}\"")
        except Exception:
            continue

    return _format_matches(output)


async def run_grep_async(pattern: str, path: str = "", file_pattern: str = "*", cwd: Optional[Path] = None,
                         ctx=None) -> str:
    if ctx:
        ctx.raise_if_cancelled()

    path = _validate_path(path, cwd)

    grep_tool = _find_grep_tool()
    if grep_tool == "ripgrep":
        command = ["rg", "--no-heading", "-n", "--glob", file_pattern, pattern, str(path)]
    elif grep_tool == "grep":
        command = ["grep", "-r", "-n", "--include", file_pattern, pattern, str(path)]
    else:
        return await asyncio.to_thread(run_grep, pattern, path, file_pattern, cwd)

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    output = ["[Matches]\n"]
    try:
        stdout, stderr = await process.communicate()
        if ctx:
            ctx.raise_if_cancelled()
        if process.returncode not in (0, 1):
            raise RuntimeError(_to_text(stderr))

        for file_path, line_no, content in _parse_match(_to_text(stdout).splitlines()):
            output.append(
                f"file path: \"{file_path}\", line: [{line_no}], content: \"{content}\""
            )
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

    return _format_matches(output)
