"""外部扩展系统：事件上下文与注册 API。"""
from core.extension.api import EVENTS, ExtensionAPI
from core.extension.context import (
    AfterLLMContext,
    AfterToolContext,
    BeforeLLMContext,
    BeforeToolContext,
)

__all__ = [
    "EVENTS",
    "ExtensionAPI",
    "BeforeLLMContext",
    "AfterLLMContext",
    "BeforeToolContext",
    "AfterToolContext",
]
