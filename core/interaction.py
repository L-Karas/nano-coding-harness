"""域层 → UI 端口（叶子模块：不 import 任何 core 模块）。

工具（clarify）、内置权限门与 agent 主循环只依赖本端口，不直接依赖 TUI；
TUI 适配器（core/tui/render.py）导入时经 register_interaction / register_render 注册实现，
headless 场景使用默认策略：权限默认拒绝，澄清默认取消，渲染事件默认 no-op（见 ADR-0005）。
"""
from contextlib import nullcontext
from typing import Any, Callable, ContextManager

_ask_permission: Callable[[str, list[str]], str] | None = None
_ask_clarify: Callable[[list[str], bool], str] | None = None
_stream_assistant_response: Callable[[str], None] | None = None
_render_tool_call: Callable[[str, Any], None] | None = None
_render_tool_result: Callable[[str, bool, list | None], None] | None = None
_render_background_notification: Callable[[str], None] | None = None
_notify: Callable[[str, str, str], None] | None = None
_render_thinking_status: Callable[[str], ContextManager] | None = None
_render_working_status: Callable[[str], ContextManager] | None = None
_render_scope: Callable[[], ContextManager] | None = None


def register_interaction(*, ask_permission: Callable[[str, list[str]], str] | None = None,
                         ask_clarify: Callable[[list[str], bool], str] | None = None) -> None:
    """TUI 注入交互实现；只覆盖传入的项，重复注册幂等。

    Args:
        ask_permission: 权限确认回调。
        ask_clarify: 澄清提问回调。
    """
    global _ask_permission, _ask_clarify
    if ask_permission is not None:
        _ask_permission = ask_permission
    if ask_clarify is not None:
        _ask_clarify = ask_clarify


def register_render(
    *,
    stream_assistant_response: Callable[[str], None] | None = None,
    render_tool_call: Callable[[str, Any], None] | None = None,
    render_tool_result: Callable[[str, bool, list | None], None] | None = None,
    render_background_notification: Callable[[str], None] | None = None,
    notify: Callable[[str, str, str], None] | None = None,
    render_thinking_status: Callable[[str], ContextManager] | None = None,
    render_working_status: Callable[[str], ContextManager] | None = None,
    render_scope: Callable[[], ContextManager] | None = None,
) -> None:
    """TUI 注入渲染实现；只覆盖传入的项，重复注册幂等。

    Args:
        stream_assistant_response: 正文增量渲染回调。
        render_tool_call: 工具调用渲染回调。
        render_tool_result: 工具结果渲染回调。
        render_background_notification: 后台任务结果通知回调。
        notify: 即时通知回调。
        render_thinking_status: 思考状态上下文回调。
        render_working_status: 工作状态上下文回调。
        render_scope: 渲染作用域上下文回调。
    """
    global _stream_assistant_response, _render_tool_call, _render_tool_result
    global _render_background_notification, _notify, _render_thinking_status, _render_working_status, _render_scope
    if stream_assistant_response is not None:
        _stream_assistant_response = stream_assistant_response
    if render_tool_call is not None:
        _render_tool_call = render_tool_call
    if render_tool_result is not None:
        _render_tool_result = render_tool_result
    if render_background_notification is not None:
        _render_background_notification = render_background_notification
    if notify is not None:
        _notify = notify
    if render_thinking_status is not None:
        _render_thinking_status = render_thinking_status
    if render_working_status is not None:
        _render_working_status = render_working_status
    if render_scope is not None:
        _render_scope = render_scope


def ask_permission(message: str, details: list[str]) -> str:
    """权限确认：message 为确认文本，details 为触发数据段落。

    Args:
        message: 确认问题。
        details: 越界路径清单、`command: ...` / `path: ...` 等触发数据。

    Returns:
        "yes" / "no"；未接入 UI 时默认 "no"。
    """
    if _ask_permission is None:
        return "no"
    return _ask_permission(message, details)


def ask_clarify(questions: list[str], multi_select: bool = False) -> str:
    """澄清提问；未接入 UI 时返回 "[User cancelled]"。

    Args:
        questions: 候选答案列表。
        multi_select: 是否允许多选。

    Returns:
        用户答案；未接入 UI 时为 "[User cancelled]"。
    """
    if _ask_clarify is None:
        return "[User cancelled]"
    return _ask_clarify(questions, multi_select)


# ---------- 渲染事件：未接入 UI 时静默 ----------

def stream_assistant_response(chunk: str = "") -> None:
    """把正文增量交给已注册的渲染器；未注册时静默。

    Args:
        chunk: 本次增量文本。
    """
    if _stream_assistant_response is not None:
        _stream_assistant_response(chunk)


def render_tool_call(name: str, args: Any) -> None:
    """把工具调用交给已注册的渲染器；未注册时静默。

    Args:
        name: 工具名。
        args: 工具参数。
    """
    if _render_tool_call is not None:
        _render_tool_call(name, args)


def render_tool_result(text: str, is_error: bool = False, diff: list | None = None) -> None:
    """工具结果：diff（write / edit）随结果一起交给 UI，由 UI 同卡渲染。

    Args:
        text: 结果文本。
        is_error: 是否为错误结果。
        diff: 变化行列表。
    """
    if _render_tool_result is not None:
        _render_tool_result(text, is_error, diff)


def render_background_notification(message: str) -> None:
    """后台任务结果通知卡片：只允许后台任务结果回流使用，其它提示统一走 notify。

    Args:
        message: 通知正文。
    """
    if _render_background_notification is not None:
        _render_background_notification(message)


def notify(message: str, title: str = "", severity: str = "information") -> None:
    """即时通知（toast）：后台任务结果之外的提示统一走这里。
    未注册适配器时静默；适配器已注册但 App 未启动时抛 RuntimeError（与其它渲染事件一致）。
    severity 取 Textual 的三档：information / warning / error。

    Args:
        message: 通知正文。
        title: 通知标题。
        severity: 严重程度。
    """
    if _notify is not None:
        _notify(message, title, severity)


def render_thinking_status(message: str = "Thinking...") -> ContextManager:
    """进入思考状态；未注册渲染器时返回空上下文。

    Args:
        message: 状态文案。

    Returns:
        状态上下文管理器。
    """
    if _render_thinking_status is not None:
        return _render_thinking_status(message)
    return nullcontext()


def render_working_status(message: str = "Working...") -> ContextManager:
    """进入工作状态；未注册渲染器时返回空上下文。

    Args:
        message: 状态文案。

    Returns:
        状态上下文管理器。
    """
    if _render_working_status is not None:
        return _render_working_status(message)
    return nullcontext()


def render_scope() -> ContextManager:
    """进入渲染作用域；未注册渲染器时返回空上下文。

    Returns:
        作用域上下文管理器。
    """
    if _render_scope is not None:
        return _render_scope()
    return nullcontext()
