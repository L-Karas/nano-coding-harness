"""线程安全渲染 API：把任意线程的渲染调用桥接进 ChatApp 事件循环。

模块级函数与 ChatApp 内部方法一一对应（签名对齐原 core/tui/ui.py）：
卡片渲染 / 流式回复 / 状态行上下文 / 会话历史回放 / 权限询问。
App 线程内直接执行，其它线程经 app.call_from_thread 桥接；
run()（core.tui.ui_textual）启动期间持有本模块的 _APP 全局，
未启动时调用渲染函数抛 RuntimeError。
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich.markup import escape
from rich.text import Text

from core.template import INJECTION_MESSAGES_PREFIX, INJECTION_MESSAGES_SUFFIX
from core.tui.theme import _markup

if TYPE_CHECKING:  # 仅类型标注：运行时经 duck-typing 访问 ChatApp，避免与 ui_textual 循环导入
    from core.tui.ui_textual import ChatApp

# 模块级桥接：渲染函数可在任意线程调用（App 线程内直连，其余桥接进事件循环）

_APP: Optional[ChatApp] = None


def _exec(fn: Callable[[ChatApp], Any]) -> Any:
    """在 App 线程中执行 fn(app)；调用方线程为 App 线程时直接执行。"""
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI is not running: call run() (or run ChatApp().run() yourself) before rendering")
    if threading.get_ident() == app._thread_id:
        return fn(app)
    try:
        return app.call_from_thread(fn, app)
    except RuntimeError:
        return None  # App 退出竞态


def _dim_body(text: str) -> Text:
    """暗灰正文卡片体。先 escape 再嵌标记：方括号会被 Rich 解析成样式标签，
    不转义会导致渲染期 MissingStyle（如样式 'truncated 8 lines'）崩溃。"""
    return _markup(f"[dim #e2e8f0]{escape(text)}[/dim #e2e8f0]")


DEFAULT_MAX_LINES = 10  # 工具卡片正文默认上限行数：超出折叠为额外 1 行提示


def _capped_lines(text: str, max_lines: int) -> tuple[str, int]:
    """正文行数限制：返回 (最多前 max_lines 行的展示文本, 被折叠行数)，未超限原样返回。"""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def _truncated_hint(hidden: int) -> Text:
    """折叠提示行（暗黄，可点击展开）。提示自身含方括号需 escape（见 _dim_body）。"""
    message = escape(f"... [truncated {hidden} lines] · click to expand")
    return _markup(f"\n[dim yellow]{message}[/dim yellow]")


def _collapse_hint() -> Text:
    """展开态末尾的收回提示行（点击卡片折叠）。"""
    return _markup("\n[dim yellow]· click to collapse[/dim yellow]")


def _require_worker_thread() -> None:
    app = _APP
    if app is not None and threading.get_ident() == app._thread_id:
        raise RuntimeError("ask_permission must be called from a non-App thread (e.g. inside handle_query)")


def render_user_input(user_text: str) -> None:
    _exec(lambda app: app._add_card("user", _markup(f"[bold #f9fafb]{escape(user_text)}[/bold #f9fafb]")))


def render_tool_call(tool_name: str, tool_args: Any, max_lines: int = DEFAULT_MAX_LINES) -> None:
    """工具调用卡片：参数默认最多渲染 max_lines 行，超出折叠为额外 1 行提示；
    点击卡片在截断与完整参数间切换。"""
    try:
        args_str = json.dumps(tool_args, ensure_ascii=False, indent=2) if isinstance(tool_args, (dict, list)) \
            else str(tool_args)
    except Exception:
        args_str = str(tool_args)
    shown, hidden = _capped_lines(args_str, max_lines)
    head = _markup(f"[bold #fde68a]Tool Call:[/bold #fde68a] [bold white]{escape(tool_name)}[/bold white]\n")
    body = _dim_body(shown)
    expand = None
    if hidden:
        body += _truncated_hint(hidden)
        expand = lambda: head + _dim_body(args_str) + _collapse_hint()
    _exec(lambda app: app._add_card("tool", head + body, expand=expand))


def render_tool_result(output: Any, max_lines: int = DEFAULT_MAX_LINES) -> None:
    """工具输出卡片：默认最多渲染 max_lines 行，超出折叠为额外 1 行提示；
    点击卡片在截断与完整输出间切换。失败输出（TOOL_ERROR_PREFIXES 前缀）自动以暗红 error 卡渲染。"""
    from core.tools import TOOL_ERROR_PREFIXES  # 懒导入：core.tools 链会经 hook_permission 回导本模块，顶层导入成环
    output_str = str(output)
    shown, hidden = _capped_lines(output_str, max_lines)
    body = _dim_body(shown)
    expand = None
    if hidden:
        body += _truncated_hint(hidden)
        expand = lambda: _dim_body(output_str) + _collapse_hint()
    kind = "error" if output_str.startswith(TOOL_ERROR_PREFIXES) else "result"
    _exec(lambda app: app._add_card(kind, body, expand=expand))


def render_tool_result_diff(rows: list[tuple[str, int, str]], max_lines: int = DEFAULT_MAX_LINES) -> None:
    """diff 预览卡片（暗橄榄底，区别于普通 result 绿卡）：默认最多渲染 max_lines 行，
    超出折叠为额外 1 行提示；点击卡片在截断与完整 diff 间切换。"""
    width = max(len(str(n)) for _, n, _ in rows)

    def _styled(part: list[tuple[str, int, str]]) -> Text:
        return Text("\n").join(Text(f"{kind}{n:>{width}} │ {line}",
                                     style={"+": "#b5bd68", "-": "#f87171", " ": "dim"}.get(kind, "dim"))  # + 绿 / - 淡红
                                    for kind, n, line in part)

    if len(rows) <= max_lines:
        _exec(lambda app: app._add_card("diff", _styled(rows)))
        return
    body = _styled(rows[:max_lines]) + _truncated_hint(len(rows) - max_lines)
    expand = lambda: _styled(rows) + _collapse_hint()
    _exec(lambda app: app._add_card("diff", body, expand=expand))


def render_background_notification(message: str, title: str = "🔔 Background Task") -> None:
    """后台任务通知卡片：加粗标题行 + 暗灰正文（对齐原 ui.py 的面板标题渲染）。"""
    body = _markup(f"[bold #f8fafc]{escape(title)}[/bold #f8fafc]\n") + _dim_body(message)
    _exec(lambda app: app._add_card("notice", body))


def render_sessions() -> None:
    """空会话提示卡片（非空列表的展示与选择在 SessionPickerScreen）"""
    _exec(lambda app: app._add_card("sessions", _dim_body("No sessions yet")))


def _is_injected_message(content: Any) -> bool:
    """消息是否为内部注入（后台任务结果 / 定时任务 / 续写提示）：以 prompt_template.py 的
    <injection_messages> 前后缀包裹识别。这类消息只喂给模型作上下文，不是用户输入，
    重放会话时应跳过，不能冒充用户消息渲染。"""
    return (isinstance(content, str)
            and content.startswith(INJECTION_MESSAGES_PREFIX)
            and content.endswith(INJECTION_MESSAGES_SUFFIX))


def render_session_history(session) -> None:
    """按消息顺序重放会话历史：用户 / 工具调用 / 工具结果 / 助手回复。
    跳过内部注入消息（agent_loop 以 <injection_messages> 前后缀包裹写入的后台通知、
    定时任务与续写提示，见 core/template/prompt_template.py）——它们不是用户说的话。"""
    for message in session.messages:
        if message.role == "user":
            if _is_injected_message(message.content):
                continue
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
            if message.payload:
                render_tool_result_diff(message.payload)
            render_tool_result(message.content)


@contextmanager
def render_scope():
    """标记一轮流式输出的结束：停掉当前 Markdown 流（App 内卡片常驻，无需二次静态打印）"""
    try:
        yield
    finally:
        _exec(lambda app: app._stop_stream())


def stream_assistant_response(chunk: str = "") -> None:
    """流式增量更新当前 Assistant 卡片（首次调用自动建卡）：chunk 为本次新增片段，勿传累计全量文本"""
    if not chunk:
        return
    _exec(lambda app: app._stream_update(chunk))


def render_assistant_response(content: str) -> None:
    """渲染一张静态 Assistant Markdown 卡片（链接可点击：系统默认浏览器打开）"""
    if not content:
        return
    _exec(lambda app: app._add_markdown_card(content))


@contextmanager
def _status_context(text: str):
    """状态行加载动画上下文：进入后 text 前轮播 spinner 帧（由 App 内 interval 驱动），
    退出后恢复进入前的状态（文本 + 是否动画），busy「处理中…」动画因此无缝续播。"""
    previous = _exec(lambda app: app._status_swap(text, spin=True))
    try:
        yield
    finally:
        if previous is not None:
            _exec(lambda app: app._set_status_text(*previous))


def render_thinking_status(message: str = "Thinking..."):
    """返回状态行上下文管理器：spinner 加载动画 · 思考中"""
    return _status_context(message)


def render_tool_calling_status(message: str):
    """返回状态行上下文管理器：spinner 加载动画 · 工具执行中"""
    return _status_context(message)


def ask_permission(message: str) -> str:
    """渲染权限确认卡片并阻塞等待回答（只能在非 App 线程调用）；
    用户从停靠区 yes/no 列表作答，返回 "yes"/"no"（拒绝时也可能返回 ""：如 App 退出竞态）。"""
    _require_worker_thread()
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI is not running: call run() first")
    done = threading.Event()
    holder: dict[str, Any] = {"value": ""}

    def _ask(app: ChatApp) -> None:
        app._perm_holder = holder
        app._perm_done = done
        app._begin_permission(message)

    try:
        _exec(_ask)
        done.wait()
    except Exception:
        return ""
    return holder["value"]
