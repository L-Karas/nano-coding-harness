"""
Token estimation shared by context modules.
"""
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Union

from openai.types.chat import ChatCompletionMessage

if TYPE_CHECKING:
    from core.context.message import Message


@lru_cache(maxsize=1)
def _encoder() -> Any:
    """返回 gpt-5 的 tiktoken 编码器；进程内只初始化一次。

    Returns:
        tiktoken 编码器实例。
    """
    import tiktoken
    return tiktoken.encoding_for_model("gpt-5")


def estimate_token(text: str) -> int:
    """粗略估算文本的 token 数。

    Args:
        text: 待估算的文本。

    Returns:
        token 数量。
    """
    return len(_encoder().encode(text))


IMAGE_TOKEN_ESTIMATE = 1000


def _estimate_content(content: Any) -> int:
    """估算消息 content 的 token 数。

    content 可能是 str 或 OpenAI content 数组；每个 image part 固定计
    IMAGE_TOKEN_ESTIMATE，其他非文本类型计 0。

    Args:
        content: 消息内容，字符串或 content part 数组。

    Returns:
        估算的 token 数。
    """
    if isinstance(content, str):
        return estimate_token(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                total += IMAGE_TOKEN_ESTIMATE
            elif part.get("type") == "text":
                total += estimate_token(part.get("text") or "")
        return total
    return 0


def _estimate_tool_calls(tool_calls: Any) -> int:
    """估算 tool_calls 的 token 数。

    兼容 dict 与 OpenAI tool call 对象；只统计函数名与参数。

    Args:
        tool_calls: assistant 消息的 tool_calls，可为空。

    Returns:
        估算的 token 数。
    """
    total_tokens = 0
    for tool_call in tool_calls or []:
        if isinstance(tool_call, dict):
            function = tool_call.get("function") or {}
            total_tokens += estimate_token(function.get("name") or "")
            total_tokens += estimate_token(function.get("arguments") or "")
        else:
            total_tokens += estimate_token(tool_call.function.name or "")
            total_tokens += estimate_token(tool_call.function.arguments or "")

    return total_tokens


def estimate_size(messages: list[Union[dict, ChatCompletionMessage, "Message"]]) -> int:
    """估算整段消息列表的 token 总数。

    统计正文、reasoning_content（仅 dict 输入）与 tool_calls；图片按固定值计。

    Args:
        messages: Message / dict / ChatCompletionMessage 组成的消息列表。

    Returns:
        估算的 token 总数。
    """
    total_tokens = 0
    for message in messages:
        if isinstance(message, dict):
            total_tokens += _estimate_content(message.get("content") or "")
            total_tokens += estimate_token(message.get("reasoning_content") or "")
            total_tokens += _estimate_tool_calls(message.get("tool_calls"))
        else:
            total_tokens += _estimate_content(message.content) if message.content else 0
            total_tokens += _estimate_tool_calls(message.tool_calls)

    return total_tokens
