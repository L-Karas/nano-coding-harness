"""线程安全渲染 API：任意线程调用，App 线程内直接执行、其它线程经 call_from_thread 桥接。

run()（core.tui.ui_textual）期间把 ChatApp 实例挂到 _APP；未启动时调用抛 RuntimeError。
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich.text import Text

from core.interaction import register_interaction
from core.template import (
    INJECTION_MESSAGES_PREFIX,
    INJECTION_MESSAGES_SUFFIX,
    TOOL_ERROR_PREFIX,
    UNKNOWN_TOOL_PREFIX,
)

if TYPE_CHECKING:  # 仅类型标注：运行时经 duck-typing 访问 ChatApp，避免循环导入
    from core.tui.ui_textual import ChatApp

_APP: Optional[ChatApp] = None

DEFAULT_MAX_LINES = 10  # 卡片正文默认行数上限（渲染宽度下换行的视觉行）

_DIM = "dim #e2e8f0"  # 卡片正文（暗灰）
_DIFF_STYLES = {"+": "#b5bd68", "-": "#f87171"}  # diff 行：+ 绿 / - 淡红，其余 dim


def _require_app() -> ChatApp:
    """取当前已挂载的 App；未启动（_APP 未设置）抛 RuntimeError。"""
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI is not running: call run() (or run ChatApp().run() yourself) before rendering")
    return app


def _exec(fn: Callable[[ChatApp], Any]) -> Any:
    """在 App 线程执行 fn(app)：已在 App 线程则直接执行，否则经事件循环桥接。"""
    app = _require_app()
    if threading.get_ident() == app._thread_id:
        return fn(app)
    try:
        return app.call_from_thread(fn, app)
    except RuntimeError:
        return None  # App 退出竞态


def _dim_body(text: str) -> Text:
    """暗灰正文（用 Text 而非 Rich 标记：正文里的方括号不会被解析成样式标签）。"""
    return Text(text, style=_DIM)


def _add_capped_card(kind: str, body: Text, max_lines: int = DEFAULT_MAX_LINES,
                     head: Optional[Text] = None) -> None:
    """追加卡片：正文超 max_lines 折叠为前 max_lines 行 + 提示行，点击切换展开；
    head 为固定首行，不计入行数与折叠。"""
    head = head if head is not None else Text()
    reserved = head.plain.count("\n")  # head 占用的固定行数
    _exec(lambda app: app._add_card(kind, head + body, cap=max_lines, reserved=reserved))


def render_user_input(user_text: str) -> None:
    """用户输入卡片（加粗亮白）"""
    _exec(lambda app: app._add_card("user", Text(user_text, style="bold #f9fafb")))


def _format_args(tool_args: Any) -> str:
    """工具参数转展示文本：dict / list 缩进 JSON，其余 str（JSON 失败回退 str）。"""
    if not isinstance(tool_args, (dict, list)):
        return str(tool_args)
    try:
        return json.dumps(tool_args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(tool_args)


def render_tool_call(tool_name: str, tool_args: Any, max_lines: int = DEFAULT_MAX_LINES) -> None:
    """工具调用卡片：参数缩进 JSON，超出折叠。"""
    head = Text.assemble(("Tool Call: ", "bold #fde68a"), (tool_name, "bold white"), "\n")
    _add_capped_card("tool", _dim_body(_format_args(tool_args)), max_lines, head=head)


def render_tool_result(output: Any, max_lines: int = DEFAULT_MAX_LINES) -> None:
    """工具输出卡片：失败输出（TOOL_ERROR_PREFIX / UNKNOWN_TOOL_PREFIX 前缀）为 error 卡，其余 result 卡。"""
    output_str = str(output)
    kind = "error" if output_str.startswith((TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX)) else "result"
    _add_capped_card(kind, _dim_body(output_str), max_lines)


def render_tool_result_diff(rows: list[tuple[str, int, str]], max_lines: int = DEFAULT_MAX_LINES) -> None:
    """diff 预览卡片：每行 "标记 行号 │ 内容"，超出折叠。"""
    width = max((len(str(n)) for _, n, _ in rows), default=1)
    body = Text("\n").join(Text(f"{kind}{n:>{width}} │ {line}", style=_DIFF_STYLES.get(kind, "dim"))
                           for kind, n, line in rows)
    _add_capped_card("diff", body, max_lines)


def render_background_notification(message: str, title: str = "🔔 Background Task") -> None:
    """后台任务通知卡片：加粗标题行固定常驻 + 暗灰正文，正文超出折叠。"""
    head = Text.assemble((title, "bold #f8fafc"), "\n")
    _add_capped_card("notice", _dim_body(message), head=head)


def render_sessions() -> None:
    """空会话提示卡片（非空列表见 SessionPickerScreen）。"""
    _exec(lambda app: app._add_card("sessions", _dim_body("No sessions yet")))


def _is_injected_message(content: Any) -> bool:
    """是否为内部注入消息（按 <injection_messages> 前后缀识别）：只喂模型，回放时跳过。"""
    return (isinstance(content, str)
            and content.startswith(INJECTION_MESSAGES_PREFIX)
            and content.endswith(INJECTION_MESSAGES_SUFFIX))


def _render_tool_message(message: Any) -> None:
    """工具结果卡片：有 diff payload 时先渲染 diff，再渲染结果正文。"""
    if message.payload:
        render_tool_result_diff(message.payload)
    render_tool_result(message.content)


def render_session_history(session: Any) -> None:
    """按消息顺序重放会话历史（跳过内部注入消息）；助手消息的多个 tool_call 与其结果按
    tool_call_id 配对：call、result、call、result…"""
    messages = session.messages
    pending: dict[str, Any] = {m.tool_call_id: m for m in messages
                               if m.role == "tool" and m.tool_call_id}  # 尚未渲染的 tool 消息

    for message in messages:
        if message.role == "user":
            if not _is_injected_message(message.content):
                render_user_input(message.content)
        elif message.role == "assistant":
            if message.content:  # 与流式实况一致：先正文，再其后的工具调用
                render_assistant_response(message.content)
            for tool_call in (message.tool_calls or []):
                fn = tool_call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", ""))
                except (TypeError, ValueError):
                    args = fn.get("arguments", "")
                render_tool_call(fn.get("name", "tool"), args)
                result = pending.pop(tool_call.get("id", ""), None)
                if result is not None:
                    _render_tool_message(result)
        elif message.role == "tool":
            if not message.tool_call_id:  # 无 id 无法配对，按原位置渲染
                _render_tool_message(message)
            elif pending.pop(message.tool_call_id, None) is message:  # 未被 tool_call 消费的孤儿结果
                _render_tool_message(message)


@contextmanager
def render_scope():
    """一轮流式输出的结束标记：停掉当前 Markdown 流。"""
    try:
        yield
    finally:
        _exec(lambda app: app._stop_stream())


def stream_assistant_response(chunk: str = "") -> None:
    """流式增量：chunk 为本次新增片段（首次调用自动建卡，勿传累计全量）。"""
    if chunk:
        _exec(lambda app: app._stream_update(chunk))


def render_assistant_response(content: str) -> None:
    """静态 Assistant Markdown 卡片（链接经系统默认浏览器打开）。"""
    if content:
        _exec(lambda app: app._add_markdown_card(content))


@contextmanager
def _status_context(text: str):
    """状态行 spinner 上下文：退出时恢复进入前的状态（busy「处理中…」动画无缝续播）。"""
    previous = _exec(lambda app: app._status_swap(text, spin=True))
    try:
        yield
    finally:
        if previous is not None:
            _exec(lambda app: app._set_status_text(*previous))


def render_thinking_status(message: str = "Thinking..."):
    """状态行上下文管理器：spinner · 思考中。"""
    return _status_context(message)


def render_working_status(message: str = "Working..."):
    """状态行上下文管理器：spinner · 工具执行中。"""
    return _status_context(message)


def _ask_blocking(begin: Callable[[ChatApp, Future[str]], None], thread_error: str) -> str:
    """权限 / 澄清询问共用的桥接骨架：须非 App 线程调用，阻塞等待作答；App 退出竞态返回 ""。"""
    app = _require_app()
    if threading.get_ident() == app._thread_id:
        raise RuntimeError(thread_error)

    future: Future[str] = Future()
    try:
        _exec(lambda a: begin(a, future))
        return future.result()
    except Exception:
        return ""


def ask_permission(message: str) -> str:
    """渲染权限确认并阻塞等待回答（仅非 App 线程，如 handle_query 内）：返回 "yes"/"no"。"""
    return _ask_blocking(lambda app, future: app._begin_permission(message, future),
                         "ask_permission must be called from a non-App thread (e.g. inside handle_query)")


def ask_clarify(questions: list[str], multi_select: bool = False) -> str:
    """渲染澄清选项并阻塞等待回答（仅非 App 线程，如工具执行线程）：单选 / 多选（Space 勾选、
    Enter 确认）/ Other 输入；Esc 返回 "[User cancelled]"，App 退出竞态可能返回 ""。"""
    return _ask_blocking(lambda app, future: app._begin_clarify(questions, multi_select, future),
                         "ask_clarify must be called from a non-App thread (e.g. inside a tool handler)")


# 渲染桥接入域层交互端口：工具（clarify）/ hook（permission）经 core.interaction 提问，
# 不再反向 import TUI；未导入本模块的场景（子代理 / 测试）走端口默认（拒绝 / 取消）。
register_interaction(ask_permission=ask_permission, ask_clarify=ask_clarify)
