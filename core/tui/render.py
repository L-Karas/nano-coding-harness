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

from core.interaction import register_interaction, register_render
from core.context.message import content_text, is_injected_content
from core.template import (
    TOOL_ERROR_PREFIX,
    UNKNOWN_TOOL_PREFIX,
)
from core.tui.surface import _strip_keycaps

if TYPE_CHECKING:  # 仅类型标注：运行时经 duck-typing 访问 ChatApp，避免循环导入
    from core.tui.ui_textual import ChatApp

_APP: Optional[ChatApp] = None

DEFAULT_MAX_LINES = 10  # 可折叠正文默认行数上限（渲染宽度下换行的视觉行）
TOOL_ARGS_MAX_LINES = 4  # 工具调用卡参数段上限：结果到达后卡片仍保持紧凑

_DIM = "dim #e2e8f0"  # 卡片正文（暗灰）
_DIFF_STYLES = {"+": "#b5bd68", "-": "#f87171"}  # diff 行：+ 绿 / - 淡红，其余 dim


def _require_app() -> ChatApp:
    """取当前已挂载的 App。

    Returns:
        当前 ChatApp 实例。

    Raises:
        RuntimeError: UI 尚未启动（_APP 未设置）。
    """
    app = _APP
    if app is None:
        raise RuntimeError("Textual UI is not running: call run() (or run ChatApp().run() yourself) before rendering")
    return app


def _exec(fn: Callable[[ChatApp], Any]) -> Any:
    """在 App 线程执行回调。

    Args:
        fn: 接收 ChatApp 的回调。

    Returns:
        回调返回值；App 退出竞态时为 None。
    """
    app = _require_app()
    if threading.get_ident() == app._thread_id:
        return fn(app)
    try:
        return app.call_from_thread(fn, app)
    except RuntimeError:
        return None  # App 退出竞态


def _dim_body(text: str) -> Text:
    """构造暗灰正文。

    Args:
        text: 原始正文。

    Returns:
        暗灰 Text；用 Text 而非 Rich 标记，正文里的方括号不会被解析成样式标签。
    """
    return Text(_strip_keycaps(text), style=_DIM)


def _with_head_line(head: Text, body: Text) -> tuple[Text, int]:
    """把固定首行拼到正文前，并给出首行占用的固定行数。

    Args:
        head: 固定首行，通常以换行结尾；不参与折叠。
        body: 正文。

    Returns:
        (合并后的正文, 首行固定行数)。
    """
    return head + body, head.plain.count("\n")


def render_user_input(user_text: Any) -> None:
    """渲染用户输入卡片。

    Args:
        user_text: 用户消息内容；content 数组只取文本 part。
    """
    _exec(lambda app: app._add_card("user", Text(_strip_keycaps(content_text(user_text)), style="bold #f9fafb")))


def _format_arg_value(value: Any) -> str:
    """把单个工具参数值转成单行文本。

    Args:
        value: 参数值。

    Returns:
        单行文本；字符串换行转义为字面 \\n，bool / null 用小写字面量，
        嵌套 dict / list 用紧凑 JSON，序列化失败回退 str。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _format_args(tool_args: Any) -> str:
    """把工具参数转成展示文本。

    Args:
        tool_args: 工具参数。

    Returns:
        dict 每参一行 `key: value`；list 为缩进 JSON；其余为 str。
    """
    if isinstance(tool_args, dict):
        if not tool_args:
            return "{}"
        return "\n".join(f"{key}: {_format_arg_value(value)}" for key, value in tool_args.items())
    if not isinstance(tool_args, list):
        return str(tool_args)
    try:
        return json.dumps(tool_args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(tool_args)


def render_tool_call(
    tool_call_id: str,
    tool_name: str,
    tool_args: Any,
    max_lines: int = TOOL_ARGS_MAX_LINES,
) -> None:
    """渲染工具调用卡片；结果到达时按 id 并进同一张卡。

    Args:
        tool_call_id: 本次调用的 id，结果据此并回本卡；空串表示无法配对（不登记）。
        tool_name: 工具名。
        tool_args: 工具参数。
        max_lines: 参数段折叠前最多显示的正文行数。
    """
    head = Text.assemble(("Tool Call: ", "bold #fde68a"), (tool_name, "bold white"), "\n")
    body, reserved = _with_head_line(head, _dim_body(_format_args(tool_args)))
    _exec(lambda app: app._add_tool_card(tool_call_id, body, cap=max_lines, reserved=reserved))


def _diff_body(rows: list[tuple[str, int, str]]) -> Text:
    """构造 diff 正文。

    Args:
        rows: (标记, 行号, 内容) 列表。

    Returns:
        每行 "标记 行号 │ 内容" 的 Text（+ 绿 / - 淡红，其余 dim）。
    """
    width = max((len(str(n)) for _, n, _ in rows), default=1)
    return Text("\n").join(
        Text(f"{kind}{n:>{width}} │ {_strip_keycaps(line)}", style=_DIFF_STYLES.get(kind, "dim"))
        for kind, n, line in rows
    )


def render_tool_result(
    tool_call_id: str,
    output: Any,
    is_error: bool | None = None,
    diff: list[tuple[str, int, str]] | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> None:
    """渲染工具结果：并进对应调用的卡片，卡片样式改按结果（result / error）。

    Args:
        tool_call_id: 对应调用的 id；无对应卡（拦截 / 解析失败 / 已清屏）时独立成卡。
        output: 工具输出。
        is_error: 是否为错误；省略时按输出前缀识别。
        diff: write / edit 的 diff 行；提供时与结果同卡渲染。
        max_lines: 结果段（或 diff 段）折叠前最多显示的正文行数。

    带 diff 时 diff 在前、超长可折叠，结果固定收尾，不拆成两张卡片。结果段以固定首行
    `Tool Result:` 开头（样式同 `Tool Call:`），标题行不参与折叠。
    """
    output_str = str(output)
    failed = is_error if is_error is not None else output_str.startswith((TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX))
    kind = "error" if failed else "result"
    head = Text.assemble(("Tool Result: ", "bold #fde68a"), "\n")
    content = _diff_body(diff) if diff else _dim_body(output_str)
    body, reserved = _with_head_line(head, content)
    tail = _dim_body(output_str) if diff else None
    _exec(lambda app: app._append_tool_result(
        tool_call_id, kind, body, cap=max_lines, reserved=reserved, tail=tail))


def render_background_notification(message: str) -> None:
    """渲染后台任务结果通知卡片。

    Args:
        message: 通知正文。

    卡片为加粗固定标题行 + 暗灰正文，正文超出折叠。
    """
    head = Text.assemble(("🔔 Background Task", "bold #f8fafc"), "\n")
    body, reserved = _with_head_line(head, _dim_body(message))
    _exec(lambda app: app._add_card("notice", body, cap=DEFAULT_MAX_LINES, reserved=reserved))


def notify(message: str, title: str = "", severity: str = "information") -> None:
    """显示即时 toast 通知。

    Args:
        message: 通知正文。
        title: 通知标题。
        severity: 严重级别。

    正文按纯文本渲染（markup=False 见 `_RenderSurface._notify`），避免工具输出 /
    异常文本里的方括号被 Rich 当成标记解析。
    """
    _exec(lambda app: app._notify(message, title, severity))


def render_sessions() -> None:
    """空会话提示卡片（非空列表见 SessionPickerScreen）。"""
    _exec(lambda app: app._add_card("sessions", _dim_body("No sessions yet")))


def _is_injected_message(content: Any) -> bool:
    """判断是否为内部注入消息。

    Args:
        content: 消息内容。

    Returns:
        按 <injection_messages> 前后缀识别时为 True；这类消息只喂模型，回放时跳过。
    """
    return is_injected_content(content)


def _render_tool_message(message: Any) -> None:
    """渲染工具结果消息。

    Args:
        message: tool 角色消息。

    payload 里的 diff 与结果同卡渲染。
    """
    render_tool_result(message.tool_call_id or "", message.content, diff=message.payload or None)


def render_session_history(session: Any) -> None:
    """按消息顺序重放会话历史。

    Args:
        session: 会话对象。

    跳过内部注入消息；助手消息的多个 tool_call 与其结果按 tool_call_id 配对：
    call、result、call、result…
    """
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
                render_tool_call(tool_call.get("id", ""), fn.get("name", "tool"), args)
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
    """一轮流式输出的结束标记。

    Yields:
        None；退出时停止当前 Markdown 流。
    """
    try:
        yield
    finally:
        _exec(lambda app: app._stop_stream())


def stream_assistant_response(chunk: str = "") -> None:
    """追加助手流式增量。

    Args:
        chunk: 本次新增片段；首次调用自动建卡，勿传累计全量。
    """
    if chunk:
        _exec(lambda app: app._stream_update(chunk))


def render_assistant_response(content: str) -> None:
    """渲染静态 Assistant Markdown 卡片。

    Args:
        content: Markdown 正文。

    链接经系统默认浏览器打开。
    """
    if content:
        _exec(lambda app: app._add_markdown_card(content))


@contextmanager
def _status_context(text: str):
    """状态行 spinner 上下文。

    Args:
        text: 状态文本。

    Yields:
        None；退出时恢复进入前的状态（busy「处理中…」动画无缝续播）。
    """
    previous = _exec(lambda app: app._status_swap(text, spin=True))
    try:
        yield
    finally:
        if previous is not None:
            _exec(lambda app: app._set_status_text(*previous))


def render_thinking_status(message: str = "Thinking..."):
    """状态行上下文管理器：spinner · 思考中。

    Args:
        message: 状态文本。

    Returns:
        状态行上下文管理器。
    """
    return _status_context(message)


def render_working_status(message: str = "Working..."):
    """状态行上下文管理器：spinner · 工具执行中。

    Args:
        message: 状态文本。

    Returns:
        状态行上下文管理器。
    """
    return _status_context(message)


def _ask_blocking(begin: Callable[[ChatApp, Future[str]], None], thread_error: str) -> str:
    """权限 / 澄清询问共用的桥接骨架。

    Args:
        begin: 在 App 线程发起询问的回调。
        thread_error: 从 App 线程调用时抛出的错误文本。

    Returns:
        用户作答；App 退出竞态返回空串。
    """
    app = _require_app()
    if threading.get_ident() == app._thread_id:
        raise RuntimeError(thread_error)

    future: Future[str] = Future()
    try:
        _exec(lambda a: begin(a, future))
        return future.result()
    except Exception:
        return ""


def ask_permission(message: str, details: list[str]) -> str:
    """渲染权限确认并阻塞等待回答（仅可在非 App 线程调用，如 handle_query 内）。

    Args:
        message: 确认文本。
        details: 触发数据段落（`command: ...` / `path: ...` 等）。

    Returns:
        "yes" 或 "no"；App 退出竞态可能返回空串。

    Raises:
        RuntimeError: 在 App 线程调用，或 UI 未启动（_APP 未设置）。
    """
    return _ask_blocking(lambda app, future: app._begin_permission(message, details, future),
                         "ask_permission must be called from a non-App thread (e.g. inside handle_query)")


def ask_clarify(questions: list[str], multi_select: bool = False) -> str:
    """渲染澄清选项并阻塞等待回答（仅可在非 App 线程调用，如工具执行线程）。

    Args:
        questions: 问题列表。
        multi_select: 是否多选（Space 勾选、Enter 确认）。

    Returns:
        用户作答；Esc 返回 "[User cancelled]"，App 退出竞态可能返回空串。

    Raises:
        RuntimeError: 在 App 线程调用，或 UI 未启动（_APP 未设置）。
    """
    return _ask_blocking(lambda app, future: app._begin_clarify(questions, multi_select, future),
                         "ask_clarify must be called from a non-App thread (e.g. inside a tool handler)")


# 渲染桥接入域层 UI 端口：工具（clarify）/ 内置权限门提问与渲染事件都经 core.interaction，
# 域层不再反向 import TUI；未导入本模块的场景（子代理 / 队友 / 测试）走端口默认（拒绝 / 取消 / no-op）。
register_interaction(ask_permission=ask_permission, ask_clarify=ask_clarify)
register_render(
    stream_assistant_response=stream_assistant_response,
    render_tool_call=render_tool_call,
    render_tool_result=render_tool_result,
    render_background_notification=render_background_notification,
    notify=notify,
    render_thinking_status=render_thinking_status,
    render_working_status=render_working_status,
    render_scope=render_scope,
)
