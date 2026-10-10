import asyncio
import glob as g
import shutil

from pydantic import Field

from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text

DESCRIPTION = (
    "Find files by glob pattern. Use it to locate files by name or extension; use `grep` to search "
    "file contents. Patterns are relative to the working directory and `**/` recurses into subdirectories."
)


class Glob(BaseTool):
    __doc__ = DESCRIPTION
    pattern: str = Field(description="Glob pattern relative to the working directory, e.g. '**/*.py', "
                                     "'src/**/*.ts'. Use `**/` to recurse.")

    agent_type: set = {"main", "sub-agent", "teammate"}

    def run(self, tctx: ToolContext | None = None) -> str:
        root = tctx.cwd if tctx and tctx.cwd else WORKDIR
        results = ["[Matches]\n"]
        for match in g.glob(self.pattern, root_dir=root, recursive=True):  # `**` 需 recursive=True 才跨多层目录
            if (root / match).resolve().is_relative_to(root):
                results.append(match)

        return "\n".join(results) if len(results) > 1 else "(No matches)"

    async def arun(self, tctx: ToolContext | None = None, use_ignore: bool = True) -> str:
        """use_ignore=False includes .gitignore's files; ripgrep path only, not exposed in the tool schema."""
        if tctx:
            tctx.raise_if_cancelled()

        if not has_ripgrep():
            return await asyncio.to_thread(self.run, tctx)

        root = tctx.cwd if tctx and tctx.cwd else WORKDIR
        command = ["rg", "--files", "-0"]
        if not use_ignore:
            command += ["--no-ignore", "--hidden"]
        command += ["-g", self.pattern, root]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        outputs = ["[Matches]\n"]
        try:
            stdout, stderr = await process.communicate()
            if tctx:
                tctx.raise_if_cancelled()
            if process.returncode not in (0, 1):
                raise RuntimeError(_to_text(stderr))

            paths = [_to_text(line) for line in stdout.split(b"\0") if line]
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

        return "\n".join(outputs) if len(outputs) > 1 else "(No matches)"


def has_ripgrep() -> bool:
    return bool(shutil.which("rg"))
