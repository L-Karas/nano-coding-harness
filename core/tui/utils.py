"""
TUI 实用工具（与终端渲染无关，从 ui_textual.py 拆出）
供 Rich/prompt_toolkit、Textual 等各 UI 实现复用的环境信息与指令表。
"""

import os
import subprocess

# Tab 补全的指令表（/quit 是 /exit 的别名，不列入以免循环重复）
SLASH_COMMANDS = ["/exit", "/clear", "/new", "/sessions"]

# 遍历时整棵剪掉的目录名（.venv 等点开头目录由下方 dot 规则覆盖，不重复列）
_SKIP_DIR_NAMES = frozenset({"venv", "__pycache__", "build", "dist", "node_modules"})
_TEMP_SUFFIXES = (".pyc", ".pyo", ".tmp", ".temp", ".bak", ".swp", ".swo", ".log")


def working_directory() -> str:
    """当前工作目录；目录被删除等极端情况下返回空串"""
    try:
        return os.getcwd()
    except OSError:
        return ""


def list_project_files(root: str | None = None) -> list[str]:
    """列出项目根下所有文件的相对路径（递归，含子目录），以 '/' 分隔且已排序。

    剪枝规则（被剪目录连同其中全部文件跳过）：
    - 以 '.' 开头的文件或目录（.git/.idea/.env/…）及其内容；
    - 虚拟环境目录（venv/.venv，.venv 由点开头规则覆盖）；
    - 缓存/构建产物目录（__pycache__/build/dist/node_modules）；
    - 临时文件（以 ~ 结尾，或 .pyc/.pyo/.tmp/.temp/.bak/.swp/.swo/.log 后缀）。
    """
    files: list[str] = []
    root = root or working_directory()
    if not root:
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in _SKIP_DIR_NAMES]
        for name in filenames:
            if (name.startswith(".")
                    or name.endswith("~")
                    or name.lower().endswith(_TEMP_SUFFIXES)):
                continue
            files.append(os.path.relpath(os.path.join(dirpath, name), root)
                         .replace(os.sep, "/"))
    return sorted(files)


def current_git_branch() -> str:
    """当前 git 分支名；非 git 仓库 / 无 git 可执行文件 / 分离头指针时返回空串"""
    try:
        proc = subprocess.run(["git", "branch", "--show-current"], capture_output=True,
                              text=True, timeout=3)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""