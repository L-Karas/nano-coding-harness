"""
Context Compaction

# Compaction is layered: first shrink oversized tool results, then trim old
# message ranges, and only call the model for a summary when the context is
# still too large or the model explicitly asks for compact.
"""
import json
import time
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Union

from openai.types.chat import ChatCompletionMessage

from src.config import PERSIST_THRESHOLD, TOOL_RESULTS_DIR, KEEP_RECENT_TOOL_RESULTS, TRANSCRIPT_DIR, client, SUB_MODEL
from src.log.log import get_logger

REMAIN_TOOL_RESULT_THRESHOLD = 120

_LOGGER = get_logger(__name__)


@dataclass
class CompactConfig:
    # Minimum number of tokens to preserve after compaction
    min_tokens: int = 1_000
    # Maximum number of tokens to preserve after compaction (truncation)
    max_tokens: int = 3_000
    # Minimum number of text messages to keep (for dialog continuation)
    text_messages: int = 5


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


def estimate_token(text: str) -> int:
    """
    Roughly count tokens
    """
    return int(len(text) * 0.75)


def estimate_size(messages: list[Union[dict, ChatCompletionMessage]]) -> int:
    total_tokens = 0
    for message in messages:
        if isinstance(message, dict):
            total_tokens += estimate_token(message["content"])
        else:
            total_tokens += estimate_token(message.content) if message.content else 0

    return total_tokens


def message_has_tool_call(message: Union[dict, ChatCompletionMessage]) -> bool:
    if isinstance(message, dict):
        return bool(message.get("tool_calls"))
    return message.tool_calls is not None


def message_has_content(message: Union[dict, ChatCompletionMessage]) -> bool:
    if isinstance(message, dict):
        return bool(message.get("content"))
    return bool(message.content)


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
    if len(output) <= PERSIST_THRESHOLD:
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
    head_end, tail_start = 3, len(messages) - max_messages + 3
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


@log_compact_info
def summarize_history(messages: list) -> str:
    """
    Summarize history messages
    """
    conversation = ""
    for message in messages:
        if isinstance(message, dict):
            conversation += json.dumps(message, ensure_ascii=False)
        else:
            conversation += message.model_dump_json(ensure_ascii=False)

    prompt = ("Summarize this coding-agent conversation so work can continue. "
              "Preserve current goal, key findings, changed files, remaining work, "
              "and user constraints.\n\n") + "Conversation:\n" + conversation

    response = client.chat.completions.create(
        model=SUB_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    return response.choices[0].message.content


@log_compact_info
def compact_history(messages: list) -> list:
    """
    Summarize history messages
    """
    write_transcript(messages)
    # print(f"  \033[36m[Compact] transcript saved: {transcript}\033[0m")
    summary = summarize_history(messages[1:])
    return messages[:1] + [{"role": "user", "content": f"<compacted-messages>{summary}</compacted-messages>"}]


@log_compact_info
def reactive_compact(messages: list) -> list:
    transcript = write_transcript(messages)
    _LOGGER.info(f"[Reactive compact] transcript saved: {transcript}")
    tail = max(0, len(messages) - 5)
    if 0 < tail < len(messages) and is_tool_result_message(messages[tail]):
        while not message_has_tool_call(messages[tail - 1]):
            tail -= 1

    try:
        summary = summarize_history(messages[1:tail])
    except Exception:
        summary = "Earlier conversation was trimmed after a prompt-too-long error."

    return (messages[:1] +
            [{"role": "user", "content": f"<compacted-messages>{summary}</compacted-messages>"}] +
            messages[tail:])


if __name__ == "__main__":
    @log_compact_info
    def fun():
        print("Hello world! ..........")
        return (10, 10)


    print(fun())
