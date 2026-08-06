"""
UI Rendering Module for Nano-Harness
Provides Rich-based visual components with color-coded background panels and prompt_toolkit input handling.
"""

import json
import shutil
import time
from contextlib import contextmanager
from typing import Optional, Any, List

from prompt_toolkit import PromptSession, Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window, FormattedTextControl, BufferControl, Layout, FloatContainer, HSplit, Float, \
    CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style as PtStyle
from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from textual.document import _history

# 使用 force_terminal 与 legacy_windows=False 防止 Windows 控制台 ANSI/UTF-8 字符宽度错位导致边框撕裂
console = Console(force_terminal=True, legacy_windows=False)
# 单例历史记录，保持跨次输入的历史
_history = InMemoryHistory()

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
_current_stream_live: Optional[Live] = None


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


def get_user_input(prompt_str: str = ">> ", commands: Optional[List[str]] = None) -> str:
    """获取用户输入（上下紧贴实线，高度锁定 3 行，绝不掉落到屏幕底端）"""
    width = console.width or shutil.get_terminal_size().columns

    # 1. 上下边框 (严格锁定 height=1)
    border = Window(FormattedTextControl([('class:border', '─' * width)]), height=1)

    # 2. 输入框 Buffer 及窗口 (严格锁定 height=1，防止被拉伸到屏幕底部)
    buffer = Buffer(history=_history, completer=WordCompleter(commands or DEFAULT_COMMANDS, ignore_case=True),
                    complete_while_typing=True)
    input_win = Window(BufferControl(buffer=buffer, input_processors=[BeforeInput(prompt_str, style='class:prompt')]),
                       height=1)

    # 3. 组合为 3 行高 (1+1+1=3) 的紧凑布局 + 补全菜单浮窗
    layout = Layout(FloatContainer(
        content=HSplit([border, input_win, border]),
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=6))]
    ))

    # 4. 快捷键绑定 (Enter 提交, Ctrl+C 退出)
    kb = KeyBindings()

    @kb.add('enter')
    def _(event): event.app.exit(result=buffer.text)

    @kb.add('c-c')
    def _(event): event.app.exit(exception=KeyboardInterrupt)

    # 5. 原生内联应用
    app = Application(layout=layout, key_bindings=kb, style=pt_style, erase_when_done=True, full_screen=False)

    with patch_stdout(raw=True):
        return (app.run() or "").strip()


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


@contextmanager
def render_scope():
    """控制每轮对话 Live 实例的开启和销毁"""
    global _current_stream_live
    with Live(
            Markdown(""), refresh_per_second=15, vertical_overflow="visible"
    ) as live:
        _current_stream_live = live
        try:
            yield
        finally:
            _current_stream_live = None


def stream_assistant_response(accumulated_text: str = ""):
    if _current_stream_live:
        panel = Panel(
            Markdown(accumulated_text),
            title="[bold #c084fc] 🤖 Assistant Response [/bold #c084fc]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#c084fc",  # 紫色实线竖条
            style="on #1e1b2e",  # 暗紫背景色
        )
        _current_stream_live.update(panel)


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


def render_tool_calling_status(message: str):
    """返回 Rich Status Spinner 上下文管理器"""
    return console.status(f"[bold dim magenta]{message}[/bold dim magenta]")


def render_thinking_status(message: str = "Thinking..."):
    """返回 Rich Status Spinner 上下文管理器"""
    return console.status(f"[bold dim magenta]{message}[/bold dim magenta]", spinner="arc")


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


if __name__ == '__main__':
    def stream_message(prompt: str):
        """【要求的方法】：获取 chunk、累加文本，并调用 stream_assistant_response"""
        print(f"\n[User]: {prompt}")

        # 模拟 LLM API 返回的数据 chunk
        chunks = [f"## 这是针对【{prompt}】的回答：\n", "### 1. 模块化成功\n", "### 2. 解耦完成",
                  "\n```python\nprint('Hello word')\n```"]
        accumulated_text = ""

        # 使用 render_scope 包裹，自动管理当前轮次的 Live 声明周期
        with render_scope():
            for chunk in chunks:
                time.sleep(0.3)  # 模拟 API 延迟
                accumulated_text += chunk

                # 调用渲染函数，只传累加文本
                stream_assistant_response(accumulated_text)


    stream_message("第一轮问题")
    stream_message("第二轮问题")
