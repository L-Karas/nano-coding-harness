"""
Context Compaction

# Compaction is layered: first shrink oversized tool results, then trim old
# message ranges, and only call the model for a summary when the context is
# still too large or the model explicitly asks for compact.
"""
import json
import time
from functools import wraps
from pathlib import Path
from typing import Union

from openai.types.chat import ChatCompletionMessage

from core.client import shared_model_client
from core.config import PERSIST_THRESHOLD, TOOL_RESULTS_DIR, KEEP_RECENT_TOOL_RESULTS, TRANSCRIPT_DIR, RESERVE_TOKENS
from core.log.log import get_logger
from core.runtime_context import AgentInterrupted
from core.template import SUMMARY_PROMPT_TEMPLATE

REMAIN_TOOL_RESULT_THRESHOLD = 2000
_LOGGER = get_logger(__name__)


def log_compact_info(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        _LOGGER.info(f"Running {func.__name__} ...")
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            _LOGGER.exception(e)
            raise e
        _LOGGER.info(f"Finished {func.__name__}.")
        return result

    return wrapper


def async_log_compact_info(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        _LOGGER.info(f"Running {func.__name__} ...")
        try:
            result = await func(*args, **kwargs)
        except Exception as e:
            _LOGGER.exception(e)
            raise e
        _LOGGER.info(f"Finished {func.__name__}.")
        return result

    return wrapper


def estimate_token(text: str) -> int:
    """
    Roughly count tokens
    """
    import tiktoken
    return len(tiktoken.encoding_for_model("gpt-5").encode(text))


def estimate_size(messages: list[Union[dict, ChatCompletionMessage]]) -> int:
    total_tokens = 0
    for message in messages:
        if isinstance(message, dict):
            total_tokens += estimate_token(message.get("content", ""))
            total_tokens += estimate_token(message.get("reasoning_content", ""))
        else:
            total_tokens += estimate_token(message.content) if message.content else 0

    return total_tokens


def message_has_tool_call(message: Union[dict, ChatCompletionMessage]) -> bool:
    if isinstance(message, dict):
        return bool(message.get("tool_calls"))
    return message.tool_calls is not None


def is_tool_result_message(message: dict) -> bool:
    if isinstance(message, dict) and message.get("role") == "tool":
        return True

    return False


def collect_tool_results(messages: list) -> list[dict]:
    """
    Collect tool result messages, return (tool call index, tool result index, tool result message)
    """
    founds = []
    i = 0
    while i < len(messages):
        if message_has_tool_call(messages[i]):
            j = i + 1
            while j < len(messages) and is_tool_result_message(messages[j]):
                founds.append((i, j, messages[j]))
                j += 1
            i = j
        else:
            i += 1

    return founds


def persist_large_output(tool_use_id: str, output: str) -> str:
    """
    When tool result too large, persist large output to file and remain truncated content.
    """
    if estimate_token(output) <= PERSIST_THRESHOLD:
        return output

    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_use_id}.text"
    if not path.exists():
        path.write_text(output, encoding="utf-8")

    return (f"<persisted-output>\nFull output saved in: {path}\n"
            f"Preview content:\n{output[:2000]}\n</persisted-output>")


# todo: tool result budge
def tool_result_budget(messages: list, max_bytes: int = int(2e6)) -> list:
    """

    """
    if not messages:
        return messages

    tool_results = []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            if not tool_results:
                return messages
            else:
                break
        tool_results.append(message)

    total = sum(len(message.get("content")) for message in tool_results)
    if total <= max_bytes:
        return messages

    # 从最大工具返回结果开始处理
    for tool_result in sorted(tool_results,
                              key=lambda tool_message: len(tool_message["content"]),
                              reverse=True):
        if total <= max_bytes:
            break

        text = tool_result.get("content")
        tool_result["content"] = persist_large_output(tool_result.get("tool_call_id"), text)
        total = sum(len(message.get("content")) for message in tool_results)

    return messages


# todo: snip middle messages
@log_compact_info
def snip_compact(messages: list, max_messages: int = 50) -> list:
    """
    Snip middle messages
    """
    if len(messages) <= max_messages:
        return messages
    head_end, tail_start = 2, len(messages) - max_messages + 2
    if message_has_tool_call(messages[head_end - 1]):
        while head_end < len(messages) and is_tool_result_message(messages[head_end]):
            head_end += 1

    if 0 < tail_start < len(messages) and is_tool_result_message(messages[tail_start]):
        while not message_has_tool_call(messages[tail_start - 1]):
            tail_start += 1

    if head_end >= tail_start:
        return messages

    snipped = tail_start - head_end
    return messages[:head_end] + [{"role": "user", "content": f"[snipped {snipped} messages]"}] + messages[tail_start:]


@log_compact_info
def micro_compact(messages: list) -> list:
    """
    Compact earlier tool call result messages.
    """
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, index, message in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        if estimate_token(message.get("content", "")) > REMAIN_TOOL_RESULT_THRESHOLD:
            message["content"] = "[Old tool result content cleared. Re-run if needed.]"

            _LOGGER.info(f"Cleared old tool result, index: {index}, result: {message['content'][:100]}")

    return messages


@log_compact_info
def write_transcript(messages: list) -> Path:
    """
    Write messages to a transcript JSONL file.
    """
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for message in messages:
            if isinstance(message, dict):
                f.write(json.dumps(message, default=str, ensure_ascii=False) + "\n")
            else:
                f.write(message.model_dump_json(ensure_ascii=False) + "\n")

    _LOGGER.info(f"Wrote messages to {path}")

    return path


def find_index_to_split(messages: list) -> int:
    # 1. 按 turn 粗划分，每个 turn 由 user 信息划分
    # 2. 在满足 total_tokens <= RESERVE_TOKENS 的前提下，从后到前尽可能的保留多个 turn
    # 3. 若在单个 turn 的 total_tokens > RESERVE_TOKENS，则按一下规则划分：
    #    - 按 assistant 信息划分，在满足 total_tokens <= RESERVE_TOKENS 前提下，尽可能多的保留 assistant - tool 信息组
    #    - assistant - tool 信息组：tool_calls 信息和相应的 tool 信息

    # turn_index_tokens 元素由 (index, tokens) 组成，其中 tokens 指 messages[index:] 的 token 总数
    # user_message_index 从小到大保存 user 信息索引
    n = len(messages)
    user_message_index = []
    turn_index_tokens = [[i, 0] for i in range(len(messages))]
    for index, message in enumerate(messages):
        end = n - index - 1
        if message["role"] == "user":
            user_message_index.append(index)
        if end == n - 1:
            turn_index_tokens[end][1] = estimate_size(messages[end:])
        else:
            turn_index_tokens[end][1] = estimate_size(messages[end:end + 1]) + turn_index_tokens[end + 1][1]

    # 从前往后查找满足要求的 turn
    for index in user_message_index:
        if turn_index_tokens[index][1] <= RESERVE_TOKENS:
            return index

    # 任意单个 turn 均大于 RESERVE_TOKENS，查找满足要求的 assistant - tool 信息组的 assistant 信息索引
    # 从最后一个 turn 开始查找；若无 user 信息，则从第一个信息查找
    end = user_message_index[-1] if user_message_index else 0
    while end < n:
        if turn_index_tokens[end][1] <= RESERVE_TOKENS and messages[end]["role"] == "assistant":
            return end
        end += 1

    # 没有满足需求的划分索引，返回 n，不进行划分
    return n


@async_log_compact_info
async def summarize_history(messages: list, ctx=None) -> str:
    """
    Summarize history messages
    """
    # todo: 当前上下文压缩会覆盖原会话历史，为压缩后的信息保存额外副本？
    try:
        summary_text = ""
        messages.append({
            "role": "user",
            "content": SUMMARY_PROMPT_TEMPLATE,
        })
        stream = await shared_model_client().get_model_client(async_client=True)(
            messages=messages,
            max_tokens=2000,
            stream=True
        )
        try:
            async for chunk in stream:
                ctx.raise_if_cancelled()
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    summary_text += choice.delta.content
        finally:
            await stream.close()

        return summary_text
    except AgentInterrupted:
        raise
    except Exception as e:
        raise e


@async_log_compact_info
async def compact_history(messages: list, ctx=None) -> list:
    """
    Summarize history messages
    """
    write_transcript(messages)
    split_index = find_index_to_split(messages)
    summary = await summarize_history(messages[:split_index], ctx)
    return ([{"role": "user", "content": f"<compacted_messages>\n{summary}\n</compacted_messages>"}] +
            messages[split_index:])


@async_log_compact_info
async def reactive_compact(messages: list, ctx=None) -> list:
    transcript = write_transcript(messages)
    _LOGGER.info(f"[Reactive compact] transcript saved: {transcript}")
    tail = max(0, len(messages) - 5)
    if 0 < tail < len(messages) and is_tool_result_message(messages[tail]):
        while not message_has_tool_call(messages[tail - 1]):
            tail -= 1

    # todo: 重压缩上下文失败后，抛出错误信息？
    try:
        summary = await summarize_history(messages, ctx)
    except Exception as e:
        _LOGGER.exception(f"[Reactive compact] failed to summarize history. Error: {e}")
        summary = "Earlier conversation was trimmed after a prompt-too-long error."

    return ([{"role": "user", "content": f"<compacted_messages>\n{summary}\n</compacted_messages>"}] +
            messages[tail:])
