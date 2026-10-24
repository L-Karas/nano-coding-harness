"""外部扩展系统：事件上下文与注册 API。"""
from core.extension.api import EVENTS, ExtensionAPI
from core.extension.context import (
    AfterLLMContext,
    AfterToolContext,
    BeforeLLMContext,
    BeforeToolContext,
    ToolExecuteContext,
)
from core.extension.dispatcher import add, dispatch, reset as reset_dispatcher
from core.extension.loader import load_extensions, reset as reset_loader
from core.extension.registry import ExtensionInfo, list_extensions, reset as reset_registry

__all__ = [
    "EVENTS",
    "ExtensionAPI",
    "ExtensionInfo",
    "BeforeLLMContext",
    "AfterLLMContext",
    "BeforeToolContext",
    "AfterToolContext",
    "ToolExecuteContext",
    "add",
    "dispatch",
    "list_extensions",
    "load_extensions",
    "reset_extensions",
]


def reset_extensions() -> None:
    """清空扩展注册与加载状态，供测试隔离使用。"""
    reset_dispatcher()
    reset_loader()
    reset_registry()
