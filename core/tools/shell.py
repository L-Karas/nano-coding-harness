"""终端命令的 shell 探测与启动：git-bash > wsl-bash > pwsh7 > windows powershell。

terminal 工具、web_extract 的 defuddle 抓取与 build_guidelines 提示共用，保证「提示用哪个 shell」
与「实际用哪个 shell」一致。
"""
import asyncio
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

_WSL_BASH_RE = re.compile(r"^[a-z]:\\windows\\(?:system32|sysnative)\\bash\.exe$", re.IGNORECASE)
_PS_UTF8_PREFIX = "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}\n"


def _is_wsl_bash(path: str) -> bool:
    """System32/Sysnative 下的 bash.exe 是 WSL 启动器：命令实际跑在 Linux 环境里。"""
    return bool(_WSL_BASH_RE.match(path.replace("/", "\\")))


ShellKind = Literal["bash", "pwsh", "powershell"]


def shell_kind(shell: str | None) -> ShellKind | None:
    """shell 类别：bash / pwsh / powershell；未知或 None 返回 None，调用方回退系统默认。"""
    if not shell:
        return None
    name = Path(shell).stem.lower()
    if name.startswith("bash"):
        return "bash"
    if name == "pwsh":
        return "pwsh"
    if name.startswith("powershell"):
        return "powershell"
    return None


@cache
def find_shell() -> str | None:
    """返回优先 shell 的可执行文件路径；None 表示无优先 shell，由 subprocess 走系统默认（cmd / sh）。

    结果进程内缓存：shell 探测是运行期不变量，terminal 与 build_guidelines 共享同一次探测结果。
    """
    bash = shutil.which("bash")
    # PATH 上的非 WSL bash（Git/MSYS2/Cygwin）与 Windows 工具链同环境，最优先
    if bash and not _is_wsl_bash(bash):
        return bash

    # bash 不在 PATH 时，按 git 安装位找 git 自带的 msys bash（优先于 WSL：环境一致）
    if git := shutil.which("git"):
        git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if git_bash.exists():
            return str(git_bash)

    # 兜底顺序：WSL 启动器（也是 bash，只是命令跑在 Linux 侧）> pwsh7 > Windows PowerShell
    return bash or shutil.which("pwsh") or shutil.which("powershell")


@dataclass
class ShellInvocation:
    """已解析的启动方式；use_shell=True 时 argv 为单元素命令串，交给系统默认 shell 解释。"""
    use_shell: bool
    argv: list[str]
    stdin_script: str | None


def build_command_invocation(command: str) -> ShellInvocation:
    """按 find_shell() 的优先 shell 显式调用；无优先 shell 时交给系统默认（Windows cmd / POSIX sh）。"""
    return _command_invocation(command, find_shell())


def _command_invocation(command: str, shell: str | None) -> ShellInvocation:
    kind = shell_kind(shell)
    if kind == "bash":
        # WSL 启动器对 `-c` 的参数转发有历史坑，改用 `-s` 从 stdin 传脚本
        if _is_wsl_bash(shell):
            return ShellInvocation(False, [shell, "-s"], command + "\n")
        return ShellInvocation(False, [shell, "-c", command], None)
    if kind in ("pwsh", "powershell"):
        # 前置 UTF-8 输出编码，避免默认代码页把中文/特殊字符输出成乱码
        return ShellInvocation(False, [shell, "-NoProfile", "-Command", _PS_UTF8_PREFIX + command], None)
    return ShellInvocation(True, [command], None)


def _quote_argument(argument: str, shell: str | None) -> str:
    """按目标 shell 的语法引用单个参数：bash/sh 用 POSIX 单引号，PowerShell 单引号内双写。"""
    if shell_kind(shell) in ("pwsh", "powershell"):
        return "'" + argument.replace("'", "''") + "'"
    return shlex.quote(argument)


def build_argv_invocation(argv: Sequence[str], executable_path: str | None = None) -> ShellInvocation:
    """argv 交给优先 shell 执行：逐参数按方言引用，URL 里的 shell 元字符无法注入/截断。

    无优先 shell 时不经 cmd 拼接（cmd 缺少可靠的单参数引用方案），保持 argv 分离直接启动；
    Windows 下 CreateProcess 需要完整路径，executable_path 用于替换 argv[0]。
    """
    shell = find_shell()
    if shell is None:
        argv = list(argv)
        if executable_path:
            argv[0] = executable_path
        return ShellInvocation(False, argv, None)
    command = " ".join(_quote_argument(argument, shell) for argument in argv)
    if shell_kind(shell) in ("pwsh", "powershell"):
        # PowerShell 中带引号的命令字符串需要 `&` 调用符才会执行
        command = "& " + command
    return _command_invocation(command, shell)


def hidden_console_kwargs() -> dict[str, int]:
    """Windows 下让子进程在独立隐藏控制台启动，防止其 SetConsoleTitle 覆盖 TUI 的 OSC 2 标题。

    npm / npx / node / cmd 等启动时会把控制台标题改成 "npm" / "cmd.exe"（web_extract 经 npx 触发，
    terminal 工具同理），与 _set_terminal_title 写的标题抢同一个窗口；CREATE_NO_WINDOW 让子进程
    持有自己的隐藏控制台，标题互不干扰。stdin / stdout / stderr 已全部重定向，不需要共享父控制台；
    mcp SDK 的 stdio 传输同样以 CREATE_NO_WINDOW 启动 server。
    """
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


async def start_process(invocation: ShellInvocation, cwd: str | Path | None = None) -> asyncio.subprocess.Process:
    """按 ShellInvocation 启动进程，WSL 的脚本经 stdin 传入。"""
    # stdin=DEVNULL：子进程不得继承终端 stdin，否则交互命令会提示并抢读控制台，
    # 把鼠标转义序列回显到输入栏、吞掉 Esc（Textual 收不到按键）；WSL bash 用 stdin 传脚本。
    kwargs = dict(
        stdin=asyncio.subprocess.PIPE if invocation.stdin_script is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        **hidden_console_kwargs(),
    )
    if invocation.use_shell:
        process = await asyncio.create_subprocess_shell(invocation.argv[0], **kwargs)
    else:
        process = await asyncio.create_subprocess_exec(*invocation.argv, **kwargs)
    if invocation.stdin_script is not None:
        process.stdin.write(invocation.stdin_script.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
    return process
