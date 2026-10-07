"""
Context Compaction

# Compaction is layered: first shrink oversized tool results, then trim old
# message ranges, and only call the model for a summary when the context is
# still too large or the user explicitly runs /compact.
"""
import inspect
from functools import wraps
from typing import Union

from openai.types.chat import ChatCompletionMessage

from core.client import shared_model_client, shared_sub_model_client
from core.config import TOOL_RESULTS_DIR, CONFIGMANAGER
from core.context import to_llm_messages
from core.context.token import estimate_size, estimate_token
from core.log.log import get_logger
from core.template import SUMMARY_PROMPT_TEMPLATE

_LOGGER = get_logger(__name__)


def log_compact_step(func):
    """给压缩步骤加开始/结束日志（同步/异步函数通用）。"""
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            _LOGGER.info(f"Running {func.__name__} ...")
            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                _LOGGER.exception(e)
                raise
            _LOGGER.info(f"Finished {func.__name__}.")
            return result

        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        _LOGGER.info(f"Running {func.__name__} ...")
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            _LOGGER.exception(e)
            raise
        _LOGGER.info(f"Finished {func.__name__}.")
        return result

    return sync_wrapper


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
    if len(output) <= CONFIGMANAGER.config.persist_tool_tokens:
        return output

    path = TOOL_RESULTS_DIR / f"{tool_use_id}.text"
    if not path.exists():
        path.write_text(output, encoding="utf-8")

    return (f"<persisted-output>\n"
            f"<saved-path>Full output saved in: {path}</saved-path>\n"
            f"<preview>Preview content:\n{output[:CONFIGMANAGER.config.persist_tool_tokens]}</preview>\n"
            f"</persisted-output>")


def tool_result_budget(messages: list, max_bytes: int = int(2e6)) -> list:
    """尾部连续 tool 结果超预算时，从最大的开始落盘并替换为预览。"""
    if not messages:
        return messages

    tool_results = []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            break
        tool_results.append(message)
    if not tool_results:
        return messages

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


@log_compact_step
def micro_compact(messages: list) -> list:
    """
    Compact earlier tool call result messages.
    """
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= CONFIGMANAGER.config.keep_recent_tool_results:
        return messages
    for _, index, message in tool_results[:-CONFIGMANAGER.config.keep_recent_tool_results]:
        if estimate_token(message.get("content", "")) > CONFIGMANAGER.config.persist_tool_tokens:
            message["content"] = "[Old tool result content cleared. Re-run if needed.]"

            _LOGGER.info(f"Cleared old tool result, index: {index}, result: {message['content'][:100]}")

    return messages


def find_index_to_split(messages: list) -> int:
    # 1. 优先按 turn（user 消息分界）切：从最早的 user 开始，保留部分不超过 reserve_threshold
    # 2. 单个 turn 就超预算时，退化为按 assistant 消息切：尽量多地保留 assistant - tool 组
    #    （tool_calls 与其对应的 tool 结果不得被拆开）
    n = len(messages)
    # suffix_tokens[i] = messages[i:] 的 token 总数；user_index 按出现顺序记录 user 消息下标
    suffix_tokens = [0] * n
    user_index = []
    total = 0
    for i in range(n - 1, -1, -1):
        total += estimate_size(messages[i:i + 1])
        suffix_tokens[i] = total
        if messages[i]["role"] == "user":
            user_index.append(i)
    user_index.reverse()

    for index in user_index:
        if suffix_tokens[index] <= CONFIGMANAGER.config.reserve_threshold:
            return index

    # 任意单个 turn 均大于 reserve_threshold：从最后一个 turn 开始找可切分的 assistant 下标
    end = user_index[-1] if user_index else 0
    while end < n:
        if suffix_tokens[end] <= CONFIGMANAGER.config.reserve_threshold and messages[end]["role"] == "assistant":
            return end
        end += 1

    # 没有满足需求的划分索引，返回 n，不进行划分
    return n


@log_compact_step
async def summarize_history(messages: list, ctx=None, sub_model: bool = False) -> str:
    """
    Summarize history messages

    sub_model=True 用子代理 client 总结（与子代理对话同模型，缓存命中）；
    默认用主 client（主流程 / 手动 /compact）。
    """
    summary_text = ""
    request_messages = to_llm_messages(messages) + [{
        "role": "user",
        "content": SUMMARY_PROMPT_TEMPLATE,
    }]
    client = shared_sub_model_client() if sub_model else shared_model_client()
    stream = await client.get_model_client(async_client=True)(
        messages=request_messages,
        max_tokens=CONFIGMANAGER.config.summary_max_tokens,
        stream=True
    )
    try:
        async for chunk in stream:
            if ctx is not None:  # 手动 /compact 不带回合上下文：无中断检查
                ctx.raise_if_cancelled()
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.delta.content:
                summary_text += choice.delta.content
    finally:
        await stream.close()

    return summary_text


@log_compact_step
async def compact_history(messages: list, ctx=None, auto_compact: bool = True, sub_model: bool = False) -> list:
    """
    Summarize history messages
    """
    if not messages:
        return []
    if estimate_size(messages) < CONFIGMANAGER.config.compact_threshold:
        return messages

    split_index = -1
    if auto_compact:
        split_index = find_index_to_split(messages)
        summary = await summarize_history(messages[:split_index], ctx, sub_model)
    else:
        summary = await summarize_history(messages, ctx, sub_model)
    return [{"role": "user", "content": f"<compacted_messages>\n{summary}\n</compacted_messages>"}] + \
        (messages[split_index:] if auto_compact else [])


async def prepare_messages(messages: list, ctx=None, sub_model: bool = False) -> list:
    """
    Every LLM turn enters through the same context budget pipeline.

    sub_model=True 时压缩总结用子代理 client（与对话同模型，缓存命中）；
    auto_compact 关闭时只裁剪工具结果，不做模型总结。
    """
    messages[:] = tool_result_budget(messages)
    messages[:] = micro_compact(messages)
    if CONFIGMANAGER.config.auto_compact:
        messages[:] = await compact_history(messages, ctx, sub_model=sub_model)

    return messages
