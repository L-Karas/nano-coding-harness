"""TUI 实用工具（与渲染无关）：环境信息（cwd / git 分支 / 模型状态）与 / 指令元数据。"""

import os
import subprocess

# / 指令元数据（顺序即候选列表顺序，增删指令只改这一处）：(规范值, 匹配别名, 简短说明)
_SLASH_COMMAND_META: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("/new", (), "Start a fresh session"),
    ("/sessions", (), "Open the session picker"),
    ("/compact", (), "Compact the conversation history"),
    ("/fork", (), "Fork session from a user message"),
    ("/skills", (), "List available skills"),
    ("/mcp", (), "List configured MCP servers"),
    ("/provider", (), "Configure API providers"),
    ("/model", (), "Switch the active model"),
    ("/effort", (), "Set the thinking effort"),
    ("/settings", (), "Edit agent settings"),
    ("/login", ("/logout",), "Register/unregister custom provider or model"),
    ("/exit", ("/quit",), "Quit the app"),
)

SLASH_COMMANDS = [cmd for cmd, _aliases, _desc in _SLASH_COMMAND_META]
SLASH_COMMAND_ALIASES = {cmd: aliases for cmd, aliases, _desc in _SLASH_COMMAND_META}


def slash_command_rows() -> list[tuple[str, str]]:
    """/ 候选行显示文本 (指令名列, 说明)：指令名左对齐定宽（含别名括注），便于说明段对齐。"""
    rows = [(cmd + (f" ({', '.join(a.lstrip('/') for a in aliases)})" if aliases else ""), desc)
            for cmd, aliases, desc in _SLASH_COMMAND_META]
    width = max(len(label) for label, _desc in rows)
    return [(label.ljust(width), desc) for label, desc in rows]


# 遍历时整棵剪掉的目录名（点开头目录由 dot 规则覆盖）
_SKIP_DIR_NAMES = frozenset({"venv", "__pycache__", "build", "dist", "node_modules"})
_TEMP_SUFFIXES = (".pyc", ".pyo", ".tmp", ".temp", ".bak", ".swp", ".swo", ".log")


def working_directory() -> str:
    """当前工作目录；目录被删除等极端情况下返回空串。"""
    try:
        return os.getcwd()
    except OSError:
        return ""


def list_project_files(root: str | None = None) -> list[str]:
    """项目根下全部文件的相对路径（'/' 分隔、已排序）；剪掉隐藏文件/目录、venv、
    缓存构建目录与临时文件。"""
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
    """当前 git 分支名；非仓库 / 无 git / 分离头指针时返回空串。"""
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


def current_context_length() -> int:
    """当前模型上下文长度；不可用 / 模型不在注册表时返回 0。"""
    try:
        from core.client import shared_model_client
        return int(shared_model_client().load_context_length())
    except Exception:
        return 0


def format_context_length(value: int) -> str:
    """上下文长度显示：K / M 单位保留一位小数（1000000 → '1.0M'，128000 → '128.0K'）"""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)
