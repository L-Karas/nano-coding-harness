"""终端命令的 shell 探测：git-bash > wsl-bash > pwsh7 > windows powershell。

terminal 工具与 build_guidelines 提示共用，保证「提示用哪个 shell」与「实际用哪个 shell」一致。
"""
import re
import shutil
from functools import cache
from pathlib import Path
from typing import Literal

_WSL_BASH_RE = re.compile(r"^[a-z]:\\windows\\(?:system32|sysnative)\\bash\.exe$", re.IGNORECASE)


def is_wsl_bash(path: str) -> bool:
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
    if bash and not is_wsl_bash(bash):
        return bash

    # bash 不在 PATH 时，按 git 安装位找 git 自带的 msys bash（优先于 WSL：环境一致）
    if git := shutil.which("git"):
        git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if git_bash.exists():
            return str(git_bash)

    if bash:  # WSL 启动器也是 bash，只是命令跑在 Linux 侧（/mnt/... 路径、另一套依赖）
        return bash
    if pwsh := shutil.which("pwsh"):
        return pwsh
    if powershell := shutil.which("powershell"):
        return powershell
    return None
