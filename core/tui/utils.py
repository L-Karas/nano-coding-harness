"""
TUI 实用工具（与终端渲染无关，从 ui_textual.py 拆出）
供 Rich/prompt_toolkit、Textual 等各 UI 实现复用的环境信息与指令表。
"""

import os
import subprocess

# / 指令元数据（顺序即候选列表顺序，增删指令只改这一处）：
# (规范值, 匹配别名, 简短说明)。规范值用于候选匹配、补全与提交解析；
# 候选行的别名括注（如 "/login (logout)"）由 slash_command_rows 从别名生成。
_SLASH_COMMAND_META: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("/new", (), "Start a fresh session"),
    ("/sessions", (), "Open the session picker"),
    ("/compact", (), "Compact the conversation history"),
    ("/skills", (), "List available skills"),
    ("/mcp", (), "List configured MCP servers"),
    ("/provider", (), "Configure API providers"),
    ("/model", (), "Switch the active model"),
    ("/effort", (), "Set the thinking effort"),
    ("/login", ("/logout",), "Register/unregister custom provider or model"),
    ("/exit", ("/quit",), "Quit the app"),
)

# 候选匹配/补全/提交解析的规范值表（顺序即候选列表顺序）
SLASH_COMMANDS = [cmd for cmd, _aliases, _desc in _SLASH_COMMAND_META]
# 指令别名：键入别名前缀同样命中该指令行
SLASH_COMMAND_ALIASES = {cmd: aliases for cmd, aliases, _desc in _SLASH_COMMAND_META}


def slash_command_rows() -> list[tuple[str, str]]:
    """/ 候选列表行显示文本（分两段返回）：(指令名列, 简短说明)。
    指令名（含别名括注）左对齐定宽（宽 = 最长指令名），说明经调用方接固定空距后
    与指令名保持距离，且各行说明左端对齐于同一列；分段供各 UI 实现独立着色
    （如 Textual 界面将说明段显示为较淡的颜色）"""
    rows = [(cmd + (f" ({', '.join(a.lstrip('/') for a in aliases)})" if aliases else ""), desc)
            for cmd, aliases, desc in _SLASH_COMMAND_META]
    width = max(len(label) for label, _desc in rows)
    return [(label.ljust(width), desc) for label, desc in rows]

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


def current_model_state() -> tuple[str, str, str]:
    """当前模型 (provider, model, thinking_level)；core.client 不可用 / 未配置时各段为空串。"""
    try:
        from core.client import shared_model_client
        client = shared_model_client()
    except Exception:
        return "", "", ""
    return (getattr(client, "current_provider", "") or "",
            getattr(client, "current_model", "") or "",
            getattr(client, "current_thinking_level", "") or "")