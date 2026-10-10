"""域层 → UI 端口（叶子模块：不 import 任何 core 模块）。

工具（clarify）、hook（permission）与 agent 主循环只依赖本端口，不直接依赖 TUI；
TUI 适配器（core/tui/render.py）导入时经 register_interaction / register_render 注册实现，
headless 场景使用默认策略：权限默认拒绝，澄清默认取消，渲染事件默认 no-op（见 ADR-0005）。
"""
from contextlib import nullcontext
from typing import Any, Callable, ContextManager

_ask_permission: Callable[[str], str] | None = None
_ask_clarify: Callable[[list[str], bool], str] | None = None
_stream_assistant_response: Callable[[str], None] | None = None
_render_tool_call: Callable[[str, Any], None] | None = None
_render_tool_result: Callable[[str, bool], None] | None = None
_render_tool_result_diff: Callable[[list], None] | None = None
_render_background_notification: Callable[[str, str], None] | None = None
_render_thinking_status: Callable[[str], ContextManager] | None = None
_render_working_status: Callable[[str], ContextManager] | None = None
_render_scope: Callable[[], ContextManager] | None = None


def register_interaction(*, ask_permission: Callable[[str], str] | None = None,
                         ask_clarify: Callable[[list[str], bool], str] | None = None) -> None:
    """TUI 注入交互实现；只覆盖传入的项，重复注册幂等。"""
    global _ask_permission, _ask_clarify
    if ask_permission is not None:
        _ask_permission = ask_permission
    if ask_clarify is not None:
        _ask_clarify = ask_clarify


def register_render(*, stream_assistant_response: Callable[[str], None] | None = None,
                    render_tool_call: Callable[[str, Any], None] | None = None,
                    render_tool_result: Callable[[str, bool], None] | None = None,
                    render_tool_result_diff: Callable[[list], None] | None = None,
                    render_background_notification: Callable[[str, str], None] | None = None,
                    render_thinking_status: Callable[[str], ContextManager] | None = None,
                    render_working_status: Callable[[str], ContextManager] | None = None,
                    render_scope: Callable[[], ContextManager] | None = None) -> None:
    """TUI 注入渲染实现；只覆盖传入的项，重复注册幂等。"""
    global _stream_assistant_response, _render_tool_call, _render_tool_result, _render_tool_result_diff
    global _render_background_notification, _render_thinking_status, _render_working_status, _render_scope
    if stream_assistant_response is not None:
        _stream_assistant_response = stream_assistant_response
    if render_tool_call is not None:
        _render_tool_call = render_tool_call
    if render_tool_result is not None:
        _render_tool_result = render_tool_result
    if render_tool_result_diff is not None:
        _render_tool_result_diff = render_tool_result_diff
    if render_background_notification is not None:
        _render_background_notification = render_background_notification
    if render_thinking_status is not None:
        _render_thinking_status = render_thinking_status
    if render_working_status is not None:
        _render_working_status = render_working_status
    if render_scope is not None:
        _render_scope = render_scope


def ask_permission(message: str) -> str:
    """权限确认：返回 "yes"/"no"；未接入 UI 时默认拒绝。"""
    if _ask_permission is None:
        return "no"
    return _ask_permission(message)


def ask_clarify(questions: list[str], multi_select: bool = False) -> str:
    """澄清提问；未接入 UI 时返回 "[User cancelled]"。"""
    if _ask_clarify is None:
        return "[User cancelled]"
    return _ask_clarify(questions, multi_select)


# ---------- 渲染事件：未接入 UI 时静默 ----------

def stream_assistant_response(chunk: str = "") -> None:
    if _stream_assistant_response is not None:
        _stream_assistant_response(chunk)


def render_tool_call(name: str, args: Any) -> None:
    if _render_tool_call is not None:
        _render_tool_call(name, args)


def render_tool_result(text: str, is_error: bool = False) -> None:
    if _render_tool_result is not None:
        _render_tool_result(text, is_error)


def render_tool_result_diff(rows: list) -> None:
    if _render_tool_result_diff is not None:
        _render_tool_result_diff(rows)


def render_background_notification(message: str, title: str = "🔔 Background Task") -> None:
    if _render_background_notification is not None:
        _render_background_notification(message, title)


def render_thinking_status(message: str = "Thinking...") -> ContextManager:
    if _render_thinking_status is not None:
        return _render_thinking_status(message)
    return nullcontext()


def render_working_status(message: str = "Working...") -> ContextManager:
    if _render_working_status is not None:
        return _render_working_status(message)
    return nullcontext()


def render_scope() -> ContextManager:
    if _render_scope is not None:
        return _render_scope()
    return nullcontext()
