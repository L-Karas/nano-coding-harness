"""域层 → UI 的交互端口（叶子模块：不 import 任何 core 模块）。

工具（clarify）与 hook（permission）只依赖本端口，不直接依赖 TUI；
TUI 渲染桥（core.tui.render）导入时注册实现，headless 场景使用默认策略：
权限默认拒绝，澄清默认按取消处理。
"""
from typing import Callable

_ask_permission: Callable[[str], str] | None = None
_ask_clarify: Callable[[list[str], bool], str] | None = None


def register_interaction(*, ask_permission: Callable[[str], str] | None = None,
                         ask_clarify: Callable[[list[str], bool], str] | None = None) -> None:
    """TUI 注入实现；只覆盖传入的项，重复注册幂等。"""
    global _ask_permission, _ask_clarify
    if ask_permission is not None:
        _ask_permission = ask_permission
    if ask_clarify is not None:
        _ask_clarify = ask_clarify


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
