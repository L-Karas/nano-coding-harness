"""外部扩展系统：事件上下文、注册 API、加载与启停。"""
from core.extension.api import EVENTS, ExtensionAPI
from core.extension.context import (
    AfterLLMContext,
    AfterToolContext,
    BeforeLLMContext,
    BeforeToolContext,
    ToolExecuteContext,
)
from core.extension.dispatcher import dispatch, reset as reset_dispatcher
from core.extension.loader import (
    ToggleResult,
    load_extensions,
    toggle_extension,
    reset as reset_loader,
)
from core.extension.registry import ExtensionInfo, list_extensions, reset as reset_registry

__all__ = [
    "EVENTS",
    "ExtensionAPI",
    "ExtensionInfo",
    "ToggleResult",
    "BeforeLLMContext",
    "AfterLLMContext",
    "BeforeToolContext",
    "AfterToolContext",
    "ToolExecuteContext",
    "dispatch",
    "list_extensions",
    "load_extensions",
    "toggle_extension",
    "reset_extensions",
]


def reset_extensions() -> None:
    """清空扩展注册与加载状态，供测试隔离使用。"""
    reset_dispatcher()
    reset_loader()
    reset_registry()
