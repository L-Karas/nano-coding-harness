"""
Token estimation shared by context modules.
"""
from functools import lru_cache
from typing import Union

from openai.types.chat import ChatCompletionMessage


@lru_cache(maxsize=1)
def _encoder():
    import tiktoken
    return tiktoken.encoding_for_model("gpt-5")


def estimate_token(text: str) -> int:
    """
    Roughly count tokens
    """
    return len(_encoder().encode(text))


IMAGE_TOKEN_ESTIMATE = 1000


def _estimate_content(content) -> int:
    """content 可能是 str 或 OpenAI content 数组；每个 image part 固定计 1000 token。"""
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


def _estimate_tool_calls(tool_calls) -> int:
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


def estimate_size(messages: list[Union[dict, ChatCompletionMessage]]) -> int:
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
