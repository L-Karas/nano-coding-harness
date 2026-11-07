"""TUI 实用工具（与渲染无关）：环境信息（cwd / git 分支 / 模型状态 / 审批模式）与 / 指令元数据。"""

import os
import subprocess

from core.config import DEFAULT_APPROVAL_MODE

# / 指令元数据（顺序即候选列表顺序，增删指令只改这一处）：(规范值, 匹配别名, 简短说明)
_SLASH_COMMAND_META: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("/new", (), "Start a fresh session"),
    ("/sessions", (), "Open the session picker"),
    ("/compact", (), "Compact the conversation history"),
    ("/fork", (), "Fork session from a user message"),
    ("/skills", (), "List available skills"),
    ("/extensions", (), "List loaded and disabled extensions"),
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
    """生成 / 候选行显示文本。

    Returns:
        (指令名列, 说明) 列表；指令名含别名括注并左对齐定宽，便于说明段对齐。
    """
    rows = [(cmd + (f" ({', '.join(a.lstrip('/') for a in aliases)})" if aliases else ""), desc)
            for cmd, aliases, desc in _SLASH_COMMAND_META]
    width = max(len(label) for label, _desc in rows)
    return [(label.ljust(width), desc) for label, desc in rows]


# 遍历时整棵剪掉的目录名（点开头目录由 dot 规则覆盖）
_SKIP_DIR_NAMES = frozenset({"venv", "__pycache__", "build", "dist", "node_modules"})
_TEMP_SUFFIXES = (".pyc", ".pyo", ".tmp", ".temp", ".bak", ".swp", ".swo", ".log")


def working_directory() -> str:
    """获取当前工作目录。

    Returns:
        绝对路径；目录被删除等极端情况下为空串。
    """
    try:
        return os.getcwd()
    except OSError:
        return ""


def list_project_files(root: str | None = None) -> list[str]:
    """列出项目根下的文件。

    Args:
        root: 项目根；省略时取当前工作目录。

    Returns:
        相对路径列表（'/' 分隔、已排序）；已剪掉隐藏文件/目录、venv、缓存构建目录与临时文件。
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
    """获取当前 git 分支名。

    Returns:
        分支名；非仓库 / 无 git / 分离头指针时为空串。
    """
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def current_model_state() -> tuple[str, str, str]:
    """获取当前模型状态。

    Returns:
        (provider, model, thinking_level)；core.client 不可用 / 未配置时各段为空串。
    """
    try:
        from core.client import shared_model_client
        client = shared_model_client()
    except Exception:
        return "", "", ""
    return (getattr(client, "current_provider", "") or "",
            getattr(client, "current_model", "") or "",
            getattr(client, "current_thinking_level", "") or "")


def current_context_length() -> int:
    """获取当前模型的上下文长度。

    Returns:
        注册表中的上下文长度；client 不可用 / 未选择模型时为 0。
    """
    try:
        from core.client import shared_model_client
        return int(shared_model_client().load_context_length())
    except Exception:
        return 0


def current_compact_trigger_tokens() -> int:
    """获取当前模型的有效压缩阈值。

    Returns:
        绝对 token 阈值；client 不可用 / 未选模型时为 0。
    """
    try:
        from core.client import shared_model_client
        return int(shared_model_client().compact_trigger_tokens())
    except Exception:
        return 0


def current_auto_compact() -> bool:
    """获取自动压缩开关。

    Returns:
        是否开启；配置不可用时按默认开启（与 config 默认值一致）。
    """
    try:
        from core.config import CONFIGMANAGER
        return bool(CONFIGMANAGER.config.auto_compact)
    except Exception:
        return True


_IMPLEMENTED_APPROVAL_MODES = ("Manual", "Yolo")


def current_approval_mode() -> str:
    """获取当前实际生效的审批模式。

    Returns:
        模式名；LLM / Decision Model 未实现，按 Manual 显示。
    """
    try:
        from core.config import CONFIGMANAGER
        mode = str(CONFIGMANAGER.config.approval_mode)
    except Exception:
        return DEFAULT_APPROVAL_MODE
    return mode if mode in _IMPLEMENTED_APPROVAL_MODES else DEFAULT_APPROVAL_MODE


def format_context_length(value: int) -> str:
    """格式化上下文长度。

    Args:
        value: token 数。

    Returns:
        K / M 单位、保留一位小数的文本（1000000 → '1.0M'，128000 → '128.0K'）；
        小于 1000 时原样返回整数字符串（999 → '999'）。
    """
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)
