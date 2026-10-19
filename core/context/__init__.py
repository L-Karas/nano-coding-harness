"""上下文层：会话消息、压缩与 token 估算。"""
from .message import (
    Message,
    content_text,
    is_injected_content,
    is_synthetic_content,
    to_llm_messages,
)

__all__ = [
    "Message",
    "content_text",
    "is_injected_content",
    "is_synthetic_content",
    "to_llm_messages",
]
