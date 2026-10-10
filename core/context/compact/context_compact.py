"""
Context Compaction

# Compaction is layered: first shrink oversized tool results, then trim old
# message ranges, and only call the model for a summary when the context is
# still too large or the user explicitly runs /compact.
"""
import inspect
import uuid
from functools import wraps

from core.client import shared_model_client, shared_sub_model_client
from core.config import TOOL_RESULTS_DIR, CONFIGMANAGER
from core.context import to_llm_messages
from core.context.message import Message, message_get, message_set
from core.context.token import estimate_size
from core.context.truncate import truncate_tool_output
from core.log.log import get_logger
from core.template import (
    PERSIST_TOOL_MESSAGE_PREFIX,
    PERSIST_TOOL_MESSAGE_SUFFIX,
    PERSIST_TOOL_MESSAGE_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)

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


READ_FILE_TOOL_NAME = "read_file"


def _is_persisted_output(content: str) -> bool:
    return content.startswith(PERSIST_TOOL_MESSAGE_PREFIX) and content.endswith(PERSIST_TOOL_MESSAGE_SUFFIX)


def _tool_names_by_call_id(messages: list) -> dict[str, str]:
    """assistant.tool_calls → {tool_call_id: 工具名}；工具结果消息本身不带工具名字段。"""
    names: dict[str, str] = {}
    for message in messages:
        for tool_call in message_get(message, "tool_calls") or []:
            call_id = tool_call.get("id", "")
            name = (tool_call.get("function") or {}).get("name", "")
            if call_id:
                names[call_id] = name
    return names


def persist_tool_output(tool_call_id: str, full_output: str, preview: str) -> str:
    """全文落盘并返回内联替换文本（保存路径 + 预览）；已是 persisted 包裹时原样返回。

    preview 由调用方传入（调用方已按上限截断），保证内联正文有界。
    """
    if _is_persisted_output(full_output):
        return full_output

    path = TOOL_RESULTS_DIR / (f"{tool_call_id}.text" if tool_call_id else f"call-{uuid.uuid4().hex[:8]}.text")
    if not path.exists():
        path.write_text(full_output, encoding="utf-8")

    return PERSIST_TOOL_MESSAGE_TEMPLATE.format(path=path, preview=preview)


def truncate_large_tool_outputs(messages: list) -> list:
    """按 read 的行数 / 字节上限截断所有工具消息（原地修改并返回）。

    - read_file 输出已在工具侧截断并自带 offset 续读提示，不落盘、不二次截断；
    - 其余工具超限时全文落盘，正文回填 <persisted-output>（截断预览 + 保存路径），
      需要全文时可用 read_file <saved-path> offset=N 续读。
    """
    tool_names = _tool_names_by_call_id(messages)
    for message in messages:
        if message_get(message, "role", "") != "tool":
            continue
        content = message_get(message, "content", "")
        if _is_persisted_output(content):
            continue
        if tool_names.get(message_get(message, "tool_call_id", "")) == READ_FILE_TOOL_NAME:
            continue
        truncated_output, was_truncated = truncate_tool_output(content)
        if was_truncated:
            message_set(message, "content", persist_tool_output(message_get(message, "tool_call_id", ""), content,
                                                                preview=truncated_output))

    return messages


def find_index_to_split(messages: list, reserve_threshold: int) -> int:
    # 1. 优先按 turn（user 消息分界）切：从最早的 user 开始，保留部分不超过 reserve_threshold
    # 2. 单个 turn 就超预算时，退化为按 assistant 消息切：尽量多地保留 assistant - tool 组
    #    （tool_calls 与其对应的 tool 结果不得被拆开）
    # reserve_threshold 由调用方按当前模型解析（见 ModelClient.reserve_threshold）。
    n = len(messages)
    # suffix_tokens[i] = messages[i:] 的 token 总数；user_index 按出现顺序记录 user 消息下标
    suffix_tokens = [0] * n
    user_index = []
    total = 0
    for i in range(n - 1, -1, -1):
        total += estimate_size(messages[i:i + 1])
        suffix_tokens[i] = total
        if message_get(messages[i], "role") == "user":
            user_index.append(i)
    user_index.reverse()

    for index in user_index:
        if suffix_tokens[index] <= reserve_threshold:
            return index

    # 任意单个 turn 均大于 reserve_threshold：从最后一个 turn 开始找可切分的 assistant 下标
    end = user_index[-1] if user_index else 0
    while end < n:
        if suffix_tokens[end] <= reserve_threshold and message_get(messages[end], "role") == "assistant":
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
        max_tokens=client.clamp_max_tokens(CONFIGMANAGER.config.summary_max_tokens),
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
async def compact_history(messages: list, ctx=None, auto_compact: bool = True,
                          sub_model: bool = False) -> tuple[list, bool]:
    """压缩历史消息；阈值/保留预算随当前模型（主代理或子代理）动态解析。

    Returns:
        (messages, compacted)：未触发压缩（低于阈值 / 无可用上下文窗口）时返回原 list 且
        compacted=False；触发时返回新 list 且 compacted=True。
    """
    if not messages:
        return [], False
    client = shared_sub_model_client() if sub_model else shared_model_client()
    # threshold <= 0：当前模型没有可用上下文窗口（未选择模型）→ 无法压缩，原样返回
    threshold = client.compact_threshold()
    if threshold <= 0 or estimate_size(messages) < threshold:
        return messages, False

    split_index = -1
    if auto_compact:
        split_index = find_index_to_split(messages, client.reserve_threshold())
        summary = await summarize_history(messages[:split_index], ctx, sub_model)
    else:
        summary = await summarize_history(messages, ctx, sub_model)
    compacted_messages = [
        Message(role="user", content=f"<compacted_messages>\n{summary}\n</compacted_messages>")
    ] + (messages[split_index:] if auto_compact else [])
    return compacted_messages, True


async def prepare_messages(messages: list, ctx=None, sub_model: bool = False) -> list:
    """
    Every LLM turn enters through the same context budget pipeline.

    sub_model=True 时压缩总结用子代理 client（与对话同模型，缓存命中）；
    auto_compact 关闭时只裁剪工具结果，不做模型总结。
    """
    truncate_large_tool_outputs(messages)
    if CONFIGMANAGER.config.auto_compact:
        messages[:], _ = await compact_history(messages, ctx, sub_model=sub_model)

    return messages
