"""流式 chunk 累积：主循环与子代理共用同一份增量解析。"""
from typing import Callable, Optional

from openai import AsyncStream
from openai.types.chat import ChatCompletionChunk


async def streaming_message(
        stream: AsyncStream[ChatCompletionChunk],
        on_text: Optional[Callable[[str], None]] = None
):
    """单遍解析流，返回 (content, reasoning, tool_calls, finish_reason, usage)。

    on_text：正文增量回调（主循环边收边渲染；子代理不传）。
    """
    content = ""
    reasoning = ""
    tool_calls: list[dict] = []
    finish_reason = ""
    usage = None
    try:
        async for chunk in stream:
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(delta, "reasoning_content", None):
                reasoning += delta.reasoning_content
            if delta.content:
                content += delta.content
                if on_text:
                    on_text(delta.content)

            for tool_call in delta.tool_calls or ():
                while len(tool_calls) <= tool_call.index:
                    tool_calls.append({
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""}
                    })
                entry = tool_calls[tool_call.index]
                if tool_call.id:
                    entry["id"] = tool_call.id
                if tool_call.function:
                    if tool_call.function.name and not entry["function"]["name"]:
                        entry["function"]["name"] = tool_call.function.name
                    if tool_call.function.arguments:
                        entry["function"]["arguments"] += tool_call.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason
    finally:
        await stream.close()

    return content, reasoning, tool_calls, finish_reason, usage
