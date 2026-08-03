"""
Context Compaction

# Compaction is layered: first shrink oversized tool results, then trim old
# message ranges, and only call the model for a summary when the context is
# still too large or the model explicitly asks for compact.
"""
import json
import time
from pathlib import Path

from openai.types.chat import ChatCompletionMessage

from config import PERSIST_THRESHOLD, TOOL_RESULTS_DIR, KEEP_RECENT_TOOL_RESULTS, TRANSCRIPT_DIR, client, SUB_MODEL


def estimate_size(messages: list) -> int:
    return len(json.dumps(messages, default=str))


def message_has_tool_use(message: ChatCompletionMessage) -> bool:
    if isinstance(message, dict):
        return False
    return message.tool_calls is not None


def is_tool_result_message(message: dict) -> bool:
    if isinstance(message, dict) and message.get("role") == "tool":
        return True

    return False


def collect_tool_results(messages: list):
    found = []
    for i, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "tool":
            found.append((i, 0, message))

    return found


def persist_large_output(tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output

    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_use_id}.text"
    if not path.exists():
        path.write_text(output, encoding="utf-8")

    return (f"<persisted-output>\nFull output: {path}\n"
            f"Preview:\n{output[:2000]}\n</persisted-output>")


def tool_result_budget(messages: list, max_bytes: int = int(2e6)) -> list:
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


def snip_compact(messages: list, max_messages: int = 50) -> list:
    """
    Compact middle messages
    """
    if len(messages) <= max_messages:
        return messages
    head_end, tail_start = 3, len(messages) - max_messages + 3
    if head_end > 0 and message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and is_tool_result_message(messages[head_end]):
            head_end += 1

    if 0 < tail_start < len(messages) and is_tool_result_message(messages[tail_start]):
        while not message_has_tool_use(messages[tail_start - 1]):
            tail_start += 1

    if head_end >= tail_start:
        return messages

    snipped = tail_start - head_end
    return messages[:head_end] + [{"role": "user", "content": f"[snipped {snipped} messages]"}] + messages[tail_start:]


def micro_compact(messages: list) -> list:
    """
    Compact earlier tool call result messages.
    """
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, _, message in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        if len(message["content"]) > 120:
            message["content"] = "[Earlier tool result compacted. Re-run if needed.]"

    return messages


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

    return path


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


def compact_history(messages: list) -> list:
    """
    Summarize history messages
    """
    transcript = write_transcript(messages)
    print(f"  \033[36m[Compact] transcript saved: {transcript}\033[0m")
    summary = summarize_history(messages[1:])
    return messages[:1] + [{"role": "user", "content": f"<compacted-messages>{summary}</compacted-messages>"}]


def reactive_compact(messages: list) -> list:
    transcript = write_transcript(messages)
    print(f"  \033[31m[Reactive compact] transcript saved: {transcript}\033[0m")
    tail = max(0, len(messages) - 5)
    if 0 < tail < len(messages) and is_tool_result_message(messages[tail]):
        while not message_has_tool_use(messages[tail - 1]):
            tail -= 1

    try:
        summary = summarize_history(messages[1:tail])
    except Exception:
        summary = "Earlier conversation was trimmed after a prompt-too-long error."

    return (messages[:1] +
            [{"role": "user", "content": f"<compacted-messages>{summary}</compacted-messages>"}] +
            messages[tail:])
