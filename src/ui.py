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
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window, FormattedTextControl, BufferControl, Layout, FloatContainer, HSplit, Float, \
    CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style as PtStyle
from rich import box
from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment
from rich.text import Text

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
DEFAULT_COMMANDS = ['/new', '/sessions', '/clear', '/model', '/compact', '/quit']


class _SlashOnlyCompleter(Completer):
    """斜杠指令补全器：仅当输入框内容恰好为 "/"（且光标位于末尾）时才给出补全候选。

    避免在普通文本中间键入空格或 "/" 时误弹出指令补全菜单。
    """

    def __init__(self, commands: List[str]) -> None:
        self._commands = commands

    def get_completions(self, document, complete_event):
        if document.text == "/" and document.cursor_position == 1:
            # start_position=-1：用完整指令文本替换输入框中的 "/" 本身
            for cmd in self._commands:
                yield Completion(cmd, start_position=-1)


# Prompt 输入样式（淡灰色/中灰色背景，淡灰文字）
pt_style = PtStyle.from_dict({
    'prompt': 'bold #f3f4f6',
})

_session: Optional[PromptSession] = None
_current_stream_live: Optional[Live] = None
# 当前流式轮次已累计的完整文本，供 Live 销毁后静态打印完整回复
_last_stream_text: str = ""


class _TailCrop:
    """底部锚定裁剪渲染器：渲染结果超出终端高度时仅保留末尾若干行。

    Live 原地重绘依赖"光标上移 N 行"；若渲染结果比终端还高，光标上移会在
    屏幕顶部被钳制，每次刷新都会把面板首行（标题行）刷进滚动历史，产生一列
    重复的 "Assistant Response" 标题。将帧高裁剪到终端高度内可根除该问题。
    """

    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = console.render_lines(self.renderable, options, pad=False)
        height = options.size.height
        if len(lines) > height:
            lines = lines[len(lines) - height:]
        new_line = Segment.line()
        for index, line in enumerate(lines):
            yield from line
            if index < len(lines) - 1:
                yield new_line


def _print_panel(panel: Panel) -> None:
    """打印面板,先输出空行与其他渲染部分间隔"""
    console.print()
    console.print(panel)


def clear_screen() -> None:
    """清空终端屏幕，删除先前会话的所有 TUI 渲染内容"""
    console.clear()


def get_prompt_session(commands: Optional[List[str]] = None) -> PromptSession:
    """获取单例 PromptSession 交互输入会话（提交后自动擦除原始输入行）"""
    global _session
    if _session is None:
        cmd_list = DEFAULT_COMMANDS if commands is None else commands
        _session = PromptSession(
            history=InMemoryHistory(),
            completer=_SlashOnlyCompleter(cmd_list),
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
    cmd_list = DEFAULT_COMMANDS if commands is None else commands
    buffer = Buffer(history=_history, completer=_SlashOnlyCompleter(cmd_list),
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
    _print_panel(
        Panel(
            banner_text,
            box=box.HORIZONTALS,
            border_style="cyan",
            title="[bold white] Harness UI [/bold white]",
            expand=False
        )
    )


def _session_label(session) -> str:
    """会话显示名：标题为空时回退为 session id"""
    return session.title if session.title else session.id


def _sort_sessions(sessions: list) -> list:
    """按时间戳从新到旧排序（YYYY-MM-DD HH:MM:SS 可直接按字典序比较）"""
    return sorted(sessions, key=lambda s: s.timestamp, reverse=True)


def render_sessions(sessions: list) -> None:
    """静态渲染会话列表卡片：显示时间戳与标题，按时间戳从新到旧排列；空列表显示提示"""
    lines = [f"[bold #f8fafc]{_session_label(s)}[/bold #f8fafc]  [dim #94a3b8]{s.timestamp}[/dim #94a3b8]"
             for s in _sort_sessions(sessions)]
    _print_panel(
        Panel(
            "\n".join(lines) if lines else "[dim #e2e8f0]暂无会话[/dim #e2e8f0]",
            title="[bold #38bdf8] 📂 Sessions [/bold #38bdf8]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#38bdf8",  # 天蓝色实线竖条
            style="on #0c1a2e"  # 暗蓝背景色
        )
    )


def select_session(sessions: list, on_delete: Optional[Any] = None) -> tuple:
    """交互式会话选择器：↑/↓ 移动，Enter 切换，Delete 删除，Esc/q 取消。
    on_delete 为删除回调（接收 session id，如 SessionManager.delete_session）。
    返回 (选中的 Session 或 None, 结束时剩余的会话列表)。"""
    if not sessions:
        return None, []

    sorted_sessions = _sort_sessions(sessions)
    selected = 0
    width = console.width or shutil.get_terminal_size().columns

    def _fragments() -> list:
        frags = []
        for i, s in enumerate(sorted_sessions):
            sel = i == selected
            frags.append(("class:sel" if sel else "class:item", f"{'▶' if sel else ' '} {_session_label(s)}"))
            frags.append(("class:dim", f"  {s.timestamp}"))
            frags.append(("", "\n"))
        return frags

    control = FormattedTextControl(text=_fragments())

    def _redraw() -> None:
        # 事件循环在按键后自动重绘，文本缓存按 render_counter 失效
        control.text = _fragments()

    def _move(delta: int) -> None:
        nonlocal selected
        selected = (selected + delta) % len(sorted_sessions)
        _redraw()

    def _delete(event) -> None:
        """删除选中会话并重绘；删空后退出选择器"""
        nonlocal selected
        session = sorted_sessions.pop(selected)
        if on_delete:
            on_delete(session.id)
        if not sorted_sessions:
            event.app.exit(result=(None, sorted_sessions))
            return
        selected = min(selected, len(sorted_sessions) - 1)
        _redraw()

    def _border() -> Window:
        return Window(FormattedTextControl([('class:border', '─' * width)]), height=1)

    hint = Window(FormattedTextControl([('class:hint', '  ↑/↓ 选择    Enter 切换    Delete 删除    Esc/q 取消')]), height=1)
    list_height = min(len(sorted_sessions), max(1, (console.height or shutil.get_terminal_size().lines) - 4))
    layout = Layout(HSplit([_border(), Window(control, height=list_height, wrap_lines=False), hint, _border()]))

    kb = KeyBindings()

    @kb.add('up')
    def _(event): _move(-1)

    @kb.add('down')
    def _(event): _move(1)

    @kb.add('enter')
    def _(event): event.app.exit(result=(sorted_sessions[selected], sorted_sessions))

    @kb.add('delete')
    def _(event): _delete(event)

    @kb.add('escape')
    @kb.add('q')
    def _(event): event.app.exit(result=(None, sorted_sessions))

    @kb.add('c-c')
    def _(event): event.app.exit(exception=KeyboardInterrupt)

    app = Application(layout=layout, key_bindings=kb, full_screen=False,
                      style=PtStyle.from_dict({
                          'border': 'dim',
                          'item': 'bold #f8fafc',
                          'sel': 'bold #38bdf8',
                          'dim': 'dim #94a3b8',
                          'hint': 'dim #94a3b8',
                      }))

    with patch_stdout(raw=True):
        return app.run()


def render_session_history(session) -> None:
    """按消息顺序重放会话历史：用户输入 / 工具调用 / 工具结果 / 助手回复"""
    for message in session.messages:
        if message.role == "user":
            render_user_input(message.content)
        elif message.role == "assistant":
            for tool_call in (message.tool_calls or []):
                fn = tool_call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", ""))
                except (TypeError, ValueError):
                    args = fn.get("arguments", "")
                render_tool_call(fn.get("name", "tool"), args)
            if message.content:
                render_assistant_response(message.content)
        elif message.role == "tool":
            render_tool_result(message.content)


def render_user_input(user_text: str):
    """渲染淡灰色背景的用户输入卡片（左侧实线竖条纯色块展示）"""
    _print_panel(
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
    _print_panel(
        Panel(
            tool_content,
            title="[bold #f59e0b] 🛠️ Tool Action [/bold #f59e0b]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#f59e0b",  # 暖黄/暗金实线竖条
            style="on #261f0d"  # 暖黄/暗金背景色
        )
    )


def _result_panel(content: RenderableType, title: str) -> Panel:
    """绿色工具结果面板，render_tool_result 与 render_tool_result_diff 共用"""
    return Panel(content, title=title, title_align="center", box=LEFT_BAR_BOX,
                 border_style="#10b981",  # 绿色实线竖条
                 style="on #11221b")  # 暗绿背景色


def render_tool_result(output: Any, max_lines: int = 12):
    """渲染带暗绿背景的 Tool 执行结果面板（左侧实线竖条纯色块展示）"""
    output_str = str(output)
    lines = output_str.splitlines()
    if len(lines) > max_lines:
        display_text = "\n".join(
            lines[:max_lines]) + f"\n[dim yellow]... [truncated {len(lines) - max_lines} lines][/dim yellow]"
    else:
        display_text = output_str

    _print_panel(
        _result_panel(f"[dim #e2e8f0]{display_text}[/dim #e2e8f0]",
                      "[bold #10b981] 📄 Tool Result [/bold #10b981]")
    )


DIFF_STYLE = {"+": "green", "-": "red", " ": "dim"}


def render_tool_result_diff(rows: list[tuple[str, int, str]]):
    """渲染变化行预览：' ' 上下文(淡) / '-' 删除(红) / '+' 新增(绿)，均带行号"""
    width = max(len(str(n)) for _, n, _ in rows)
    styled = []
    for kind, n, line in rows:
        styled.append(Text(f"{kind}{n:>{width}} │ {line}", style=DIFF_STYLE.get(kind, "dim")))
    content = Text("\n").join(styled)

    _print_panel(
        _result_panel(content, "[bold #10b981] 📄 Tool Call (git diff) [/bold #10b981]")
    )


@contextmanager
def render_scope():
    """控制每轮对话 Live 实例的开启和销毁；退出后静态打印一次完整回复"""
    global _current_stream_live, _last_stream_text
    _last_stream_text = ""
    try:
        with Live(
                Markdown(""), console=console, refresh_per_second=15, transient=True
        ) as live:
            _current_stream_live = live
            try:
                yield
            finally:
                _current_stream_live = None
    finally:
        # Live 帧已擦除，将完整回复静态打印一次，保留在终端滚动历史中
        if _last_stream_text:
            render_assistant_response(_last_stream_text)
            _last_stream_text = ""


def _assistant_panel(content: str) -> Panel:
    """构建 Assistant Markdown 回复面板（流式与静态渲染共用）"""
    return Panel(
        Markdown(content),
        title="[bold #c084fc] 🤖 Assistant Response [/bold #c084fc]",
        title_align="center",
        box=LEFT_BAR_BOX,
        border_style="#c084fc",  # 紫色实线竖条
        style="on #1e1b2e",  # 暗紫背景色
    )


def stream_assistant_response(accumulated_text: str = ""):
    global _last_stream_text
    if _current_stream_live:
        _last_stream_text = accumulated_text
        # 裁剪到终端高度，防止 Live 重绘区域超高导致标题行重复刷屏
        _current_stream_live.update(_TailCrop(_assistant_panel(accumulated_text)))


def render_assistant_response(content: str):
    """渲染带暗紫背景的 Assistant Markdown 回复卡片（左侧实线竖条纯色块展示）"""
    if not content:
        return
    _print_panel(_assistant_panel(content))


def render_tool_calling_status(message: str):
    """返回 Rich Status Spinner 上下文管理器"""
    return console.status(f"[bold dim magenta]{message}[/bold dim magenta]")


def render_thinking_status(message: str = "Thinking..."):
    """返回 Rich Status Spinner 上下文管理器"""
    return console.status(f"[bold dim magenta]{message}[/bold dim magenta]", spinner="arc")


def render_background_notification(message: str, title: str = "🔔 Background Task"):
    """渲染多线程后台任务通知卡片（左侧实线竖条深青暗蓝纯色块展示）"""
    _print_panel(
        Panel(
            f"[dim #e2e8f0]{message}[/dim #e2e8f0]",
            title=f"[bold cyan] {title} [/bold cyan]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="cyan",
            style="on #0f172a"  # 暗青蓝背景色
        )
    )


def ask_permission(message: str, prompt_str: str = "  Allowed? [y/N] ") -> str:
    """渲染权限确认面板（暖黄警示色）并通过 UI 获取用户 y/N 输入，返回原始输入文本"""
    _print_panel(
        Panel(
            f"[bold #fef3c7]{message}[/bold #fef3c7]",
            title="[bold #f59e0b] 🔒 Permission Required [/bold #f59e0b]",
            title_align="center",
            box=LEFT_BAR_BOX,
            border_style="#f59e0b",  # 暖黄实线竖条
            style="on #3b2a10"  # 暗琥珀背景色
        )
    )
    return get_user_input(prompt_str, commands=[])


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
