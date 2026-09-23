"""线程安全渲染 API：把任意线程的渲染调用桥接进 ChatApp 事件循环。

模块级函数与 ChatApp 内部方法一一对应：卡片渲染 / 流式回复 / 状态行上下文 /
会话历史回放 / 权限询问。App 线程内直接执行，其它线程经 app.call_from_thread 桥接；
run()（core.tui.ui_textual）运行期间持有本模块的 _APP 全局，未启动时调用抛 RuntimeError。
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich.text import Text

from core.template import INJECTION_MESSAGES_PREFIX, INJECTION_MESSAGES_SUFFIX

if TYPE_CHECKING:  # 仅类型标注：运行时经 duck-typing 访问 ChatApp，避免与 ui_textual 循环导入
    from core.tui.ui_textual import ChatApp

_APP: Optional[ChatApp] = None  # run() 期间挂载的 ChatApp，渲染桥接目标

DEFAULT_MAX_LINES = 10  # 卡片正文默认上限行数（按渲染宽度换行后的视觉行计）：超出折叠为额外 1 行提示

_DIM = "dim #e2e8f0"  # 卡片正文（暗灰）
_DIFF_STYLES = {"+": "#b5bd68", "-": "#f87171"}  # diff 行：+ 绿 / - 淡红，其余 dim


def _exec(fn: Callable[[ChatApp], Any]) -> Any:
    """在 App 线程执行 fn(app)：调用方已在 App 线程时直接执行，否则经事件循环桥接。"""
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
    """暗灰正文。用 Text 而非 Rich 标记：正文里的方括号不会被解析成样式标签。"""
    return Text(text, style=_DIM)


def _add_capped_card(kind: str, body: Text, max_lines: int = DEFAULT_MAX_LINES,
                     head: Optional[Text] = None) -> None:
    """追加卡片：正文超过 max_lines 行（渲染宽度下换行后的视觉行，长行折出的行同样计入）
    时只显示前 max_lines 行 + 折叠提示行，点击卡片在截断与完整正文间切换
    （head 为固定首行，不计入行数与折叠）"""
    head = head if head is not None else Text()
    reserved = head.plain.count("\n")  # head 占用的固定行数
    _exec(lambda app: app._add_card(kind, head + body, cap=max_lines, reserved=reserved))


def render_user_input(user_text: str) -> None:
    """用户输入卡片（加粗亮白）"""
    _exec(lambda app: app._add_card("user", Text(user_text, style="bold #f9fafb")))


def _format_args(tool_args: Any) -> str:
    """工具参数转展示文本：dict / list 缩进 JSON，其它类型按 str（JSON 失败回退 str）。"""
    if not isinstance(tool_args, (dict, list)):
        return str(tool_args)
    try:
        return json.dumps(tool_args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(tool_args)


def render_tool_call(tool_name: str, tool_args: Any, max_lines: int = DEFAULT_MAX_LINES) -> None:
    """工具调用卡片：参数缩进 JSON，超出 max_lines 折叠。"""
    head = Text.assemble(("Tool Call: ", "bold #fde68a"), (tool_name, "bold white"), "\n")
    _add_capped_card("tool", _dim_body(_format_args(tool_args)), max_lines, head=head)


def render_tool_result(output: Any, max_lines: int = DEFAULT_MAX_LINES) -> None:
    """工具输出卡片：失败输出（TOOL_ERROR_PREFIXES 前缀）渲染为暗红 error 卡，其余为 result 卡。"""
    from core.tools import TOOL_ERROR_PREFIXES  # 懒导入：core.tools 链经 hook_permission 回导本模块，顶层导入成环
    output_str = str(output)
    kind = "error" if output_str.startswith(TOOL_ERROR_PREFIXES) else "result"
    _add_capped_card(kind, _dim_body(output_str), max_lines)


def render_tool_result_diff(rows: list[tuple[str, int, str]], max_lines: int = DEFAULT_MAX_LINES) -> None:
    """diff 预览卡片（暗橄榄底）：每行 "标记 行号 │ 内容"，超出 max_lines 折叠。"""
    width = max((len(str(n)) for _, n, _ in rows), default=1)
    body = Text("\n").join(Text(f"{kind}{n:>{width}} │ {line}", style=_DIFF_STYLES.get(kind, "dim"))
                           for kind, n, line in rows)
    _add_capped_card("diff", body, max_lines)


def render_background_notification(message: str, title: str = "🔔 Background Task") -> None:
    """后台任务通知卡片：加粗标题行 + 暗灰正文。"""
    body = Text.assemble((title, "bold #f8fafc"), "\n") + _dim_body(message)
    _exec(lambda app: app._add_card("notice", body))


def render_sessions() -> None:
    """空会话提示卡片（非空列表的展示与选择在 SessionPickerScreen）"""
    _exec(lambda app: app._add_card("sessions", _dim_body("No sessions yet")))


def _is_injected_message(content: Any) -> bool:
    """是否为内部注入消息（后台任务结果 / 定时任务 / 续写提示）：按 <injection_messages>
    前后缀识别。这类消息只喂给模型作上下文，不是用户输入，回放时应跳过。"""
    return (isinstance(content, str)
            and content.startswith(INJECTION_MESSAGES_PREFIX)
            and content.endswith(INJECTION_MESSAGES_SUFFIX))


def render_session_history(session: Any) -> None:
    """按消息顺序重放会话历史：用户 / 工具调用 / 工具结果 / 助手回复（跳过内部注入消息）。"""
    for message in session.messages:
        if message.role == "user":
            if not _is_injected_message(message.content):
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
    """标记一轮流式输出的结束：停掉当前 Markdown 流"""
    try:
        yield
    finally:
        _exec(lambda app: app._stop_stream())


def stream_assistant_response(chunk: str = "") -> None:
    """流式增量更新当前 Assistant 卡片（首次调用自动建卡）：chunk 为本次新增片段，勿传累计全量"""
    if chunk:
        _exec(lambda app: app._stream_update(chunk))


def render_assistant_response(content: str) -> None:
    """渲染一张静态 Assistant Markdown 卡片（链接可点击：系统默认浏览器打开）"""
    if content:
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
    """渲染权限确认卡片并阻塞等待回答（只能在非 App 线程调用，如 handle_query 内）；
    用户从停靠区 yes/no 列表作答，返回 "yes"/"no"（App 退出竞态下可能返回 ""）。"""
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI is not running: call run() first")
    if threading.get_ident() == app._thread_id:
        raise RuntimeError("ask_permission must be called from a non-App thread (e.g. inside handle_query)")

    future: Future[str] = Future()
    try:
        _exec(lambda app: app._begin_permission(message, future))
        return future.result()
    except Exception:
        return ""
