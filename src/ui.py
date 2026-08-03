"""
UI Rendering Module for Nano-Harness
Provides Rich-based visual components with color-coded background panels and prompt_toolkit input handling.
"""

import json
from typing import Optional, Any, List

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style as PtStyle
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# 使用 force_terminal 与 legacy_windows=False 防止 Windows 控制台 ANSI/UTF-8 字符宽度错位导致边框撕裂
console = Console(force_terminal=True, legacy_windows=False)

# 无边框（无线条、仅保留背景色色块）Box 定义
EMPTY_BOX = box.Box(
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
)

# 左侧加粗实线竖条 Box 定义：仅最左侧显示全块字符 █ 实线，其余侧无线条
LEFT_BAR_BOX = box.Box(
    "█   \n"
    "█   \n"
    "█   \n"
    "█   \n"
    "█   \n"
    "█   \n"
    "█   \n"
    "█   \n"
)

# 默认斜杠命令补全列表
DEFAULT_COMMANDS = ['/help', '/clear', '/model', '/compact', '/tools', '/exit', '/quit']

# Prompt 输入样式（淡灰色/中灰色背景，淡灰文字）
pt_style = PtStyle.from_dict({
    'prompt': 'bold #f3f4f6',
})

_session: Optional[PromptSession] = None


def get_prompt_session(commands: Optional[List[str]] = None) -> PromptSession:
    """获取单例 PromptSession 交互输入会话（提交后自动擦除原始输入行）"""
    global _session
    if _session is None:
        cmd_list = commands or DEFAULT_COMMANDS
        _session = PromptSession(
            history=InMemoryHistory(),
            completer=WordCompleter(cmd_list, ignore_case=True),
            style=pt_style,
            erase_when_done=True  # 回车提交后自动擦除原始 Prompt 文本
        )
    return _session


def get_user_input(prompt_str: str = ">> ") -> str:
    """获取用户终端输入（配合 patch_stdout 防止后台多线程输出打乱当前输入）"""
    # session = get_prompt_session()
    # with patch_stdout(raw=True):
    #     return session.prompt(prompt_str).strip()
    import sys
    import os

    # 获取当前文件（main.py）所在目录的父目录（即项目根目录 my_project/）
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 将根目录添加到 Python 搜索路径
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

    # 现在可以像导入普通顶级包一样导入 tests
    from test import text_test  # 假设 tests 下有 test_utils.py
    # 或者 from tests import test_utils

    return text_test.get_user_input(prompt_str)


def render_banner(title: str = "🤖 Nano-Harness Agent Loop", subtitle: str = "Type /help for commands, /exit to quit"):
    """渲染 Header 顶部 Banner 卡片"""
    banner_text = f"[bold cyan]{title}[/bold cyan]\n[dim]{subtitle}[/dim]"
    console.print(
        Panel(
            banner_text,
            box=box.HORIZONTALS,
            border_style="cyan",
            title="[bold white] Harness UI [/bold white]",
            expand=False
        )
    )


def render_user_input(user_text: str):
    """渲染淡灰色背景的用户输入卡片（左侧实线竖条纯色块展示）"""
    console.print(
        Panel(
            f"[bold #f9fafb]{user_text}[/bold #f9fafb]",
            title="[bold #e5e7eb] 👤 User Input [/bold #e5e7eb]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#6b7280",  # 中灰色实线竖条
            style="on #374151"  # 淡灰色/石墨灰背景色
        )
    )


def render_tool_call(tool_name: str, tool_args: Any):
    """渲染带暖黄/暗金背景的 Tool Call 动作卡片（左侧实线竖条纯色块展示）"""
    if isinstance(tool_args, (dict, list)):
        args_str = json.dumps(tool_args, ensure_ascii=False, indent=2)
    else:
        args_str = str(tool_args)

    tool_content = f"[bold yellow]Tool:[bold white] {tool_name}[/bold white]\n[dim]Args:\n{args_str}[/dim]"
    console.print(
        Panel(
            tool_content,
            title="[bold #f59e0b] 🛠️ Tool Action [/bold #f59e0b]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#f59e0b",  # 暖黄/暗金实线竖条
            style="on #261f0d"  # 暖黄/暗金背景色
        )
    )


def render_tool_result(output: Any, max_lines: int = 12):
    """渲染带暗绿背景的 Tool 执行结果面板（左侧实线竖条纯色块展示）"""
    output_str = str(output)
    lines = output_str.splitlines()
    if len(lines) > max_lines:
        display_text = "\n".join(
            lines[:max_lines]) + f"\n[dim yellow]... [truncated {len(lines) - max_lines} lines][/dim yellow]"
    else:
        display_text = output_str

    console.print(
        Panel(
            f"[dim #e2e8f0]{display_text}[/dim #e2e8f0]",
            title="[bold #10b981] 📄 Tool Result [/bold #10b981]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#10b981",  # 绿色实线竖条
            style="on #11221b"  # 暗绿背景色
        )
    )


def render_assistant_response(content: str):
    """渲染带暗紫背景的 Assistant Markdown 回复卡片（左侧实线竖条纯色块展示）"""
    if not content:
        return
    console.print(
        Panel(
            Markdown(content),
            title="[bold #c084fc] 🤖 Assistant Response [/bold #c084fc]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#c084fc",  # 紫色实线竖条
            style="on #1e1b2e"  # 暗紫背景色
        )
    )


def render_thinking_status(message: str = "Thinking..."):
    """返回 Rich Status Spinner 上下文管理器"""
    return console.status(f"[bold dim magenta]{message}[/bold dim magenta]", spinner="bouncingBar")


def render_background_notification(message: str, title: str = "🔔 Background Task"):
    """渲染多线程后台任务通知卡片（左侧实线竖条深青暗蓝纯色块展示）"""
    console.print(
        Panel(
            f"[dim #e2e8f0]{message}[/dim #e2e8f0]",
            title=f"[bold cyan] {title} [/bold cyan]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="cyan",
            style="on #0f172a"  # 暗青蓝背景色
        )
    )
