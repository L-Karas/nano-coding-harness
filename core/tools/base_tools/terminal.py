import asyncio
import subprocess
from pathlib import Path

from pydantic import Field

from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.tools.shell import build_command_invocation, hidden_console_kwargs, start_process
from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text

DESCRIPTION = (
    "Run a shell command and return its combined stdout/stderr. "
    "Use it for terminal operations (git, builds, tests, scripts); do NOT use it for file work — "
    "`read_file` to read, `write_file` to create, `edit_file` to edit, `glob` to find files, "
    "`grep` to search contents. "
    "Each call runs in a **fresh shell** in the working directory: `cd`/env changes do not persist, so chain "
    "steps in one command (`cd sub && pytest`). stdin is not connected, so interactive commands "
    "(editors, prompts, `git commit` without `-m`) will not work — pass non-interactive flags. "
    "Commands that never exit block the turn until cancelled; set `should_run_in_background` for "
    "long-running processes."
)


class Terminal(BaseTool):
    __doc__ = DESCRIPTION
    command: str = Field(description="The shell command to execute.")
    should_run_in_background: bool = Field(default=False,
                                           description="Run the command without blocking the turn: returns a "
                                                       "`[Background task started]` placeholder immediately, and the "
                                                       "real output arrives later as a "
                                                       "`<background-task-notification>`. Use for long-running "
                                                       "processes (dev server, watch mode, slow build/test) whose "
                                                       "output is not needed for the next step; leave false when the "
                                                       "next step depends on the output.")
    agent_type: set = {"main", "sub-agent", "teammate"}

    def run(self, tctx: ToolContext | None = None) -> str:
        """
        should_run_in_background is consumed by the dispatcher; direct execution ignores it.
        """
        # text=True decodes in a reader thread: on Windows (gbk locale) git's UTF-8 output
        # kills that thread and communicate() returns stdout=None. Capture bytes, decode here.
        # stdin=DEVNULL：子进程不得继承终端 stdin，否则交互命令会提示并抢读控制台；WSL bash 用 stdin 传脚本。
        args = build_command_invocation(self.command)
        run_kwargs = dict(shell=args.use_shell, capture_output=True,
                          cwd=tctx.cwd if tctx and tctx.cwd else WORKDIR, timeout=120,
                          **hidden_console_kwargs())
        if args.stdin_script is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = args.stdin_script.encode("utf-8")
        res = subprocess.run(args.argv, **run_kwargs)
        output = (_to_text(res.stdout) + _to_text(res.stderr)).strip()
        return output[:int(5e4)] if output else "(Tool no output)"

    async def arun(self, tctx: ToolContext | None = None) -> str:
        process = await start_process(build_command_invocation(self.command),
                                      cwd=tctx.cwd if tctx and tctx.cwd else WORKDIR)
        try:
            out, error = await process.communicate()
            if tctx:
                tctx.raise_if_cancelled()
            output = (_to_text(out) + _to_text(error)).strip()
            # todo: tool output budget
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
