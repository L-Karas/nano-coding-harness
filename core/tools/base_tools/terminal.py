import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import Field

from core.config import WORKDIR
from core.shell import find_shell, is_wsl_bash, shell_kind
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


_PS_UTF8_PREFIX = "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}\n"


@dataclass
class _ShellArgs:
    use_shell: bool
    argv: list[str]
    stdin_script: str | None


def _shell_args(command: str) -> _ShellArgs:
    """有优先 shell 就显式调用，否则交给系统默认（Windows cmd / POSIX sh）。"""
    shell = find_shell()
    kind = shell_kind(shell)
    if kind == "bash":
        # WSL 启动器对 `-c` 的参数转发有历史坑，改用 `-s` 从 stdin 传脚本
        if is_wsl_bash(shell):
            return _ShellArgs(False, [shell, "-s"], command + "\n")
        return _ShellArgs(False, [shell, "-c", command], None)
    if kind in ("pwsh", "powershell"):
        # 前置 UTF-8 输出编码，避免默认代码页把中文/特殊字符输出成乱码
        return _ShellArgs(False, [shell, "-NoProfile", "-Command", _PS_UTF8_PREFIX + command], None)
    return _ShellArgs(True, [command], None)


def run_terminal(command: str, cwd: Optional[Path] = None) -> str:
    """
    should_run_in_background is consumed by the dispatcher; direct execution ignores it.
    """
    # text=True decodes in a reader thread: on Windows (gbk locale) git's UTF-8 output
    # kills that thread and communicate() returns stdout=None. Capture bytes, decode here.
    # stdin=DEVNULL: 子进程不得继承终端 stdin，否则交互命令会提示并抢读控制台，
    # 把鼠标转义序列回显到输入栏、吞掉 Esc（Textual 收不到按键）；WSL bash 用 stdin 传脚本。
    args = _shell_args(command)
    run_kwargs = dict(shell=args.use_shell, capture_output=True, cwd=cwd or WORKDIR, timeout=120)
    if args.stdin_script is None:
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = args.stdin_script.encode("utf-8")
    res = subprocess.run(args.argv, **run_kwargs)
    output = (_to_text(res.stdout) + _to_text(res.stderr)).strip()
    return output[:int(5e4)] if output else "(Tool no output)"


async def run_terminal_async(command: str, cwd: Optional[Path] = None, ctx=None):
    args = _shell_args(command)
    kwargs = dict(
        # 交互命令不得抢终端 stdin，否则 Esc 无法中断；WSL bash 允许写脚本后关闭
        stdin=asyncio.subprocess.PIPE if args.stdin_script is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or WORKDIR,
    )
    if args.use_shell:
        process = await asyncio.create_subprocess_shell(args.argv[0], **kwargs)
    else:
        process = await asyncio.create_subprocess_exec(*args.argv, **kwargs)
    if args.stdin_script is not None:
        process.stdin.write(args.stdin_script.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

    try:
        out, error = await process.communicate()
        if ctx:
            ctx.raise_if_cancelled()
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
