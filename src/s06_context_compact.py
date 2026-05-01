"""
s06_context_compact.py - Context Compact
This teaching version keeps the compact model intentionally small:
1. Large tool output is persisted to disk and replaced with a preview marker.
2. Older tool results are micro-compacted into short placeholders.
3. When the whole conversation gets too large, the agent summarizes it and
   continues from that summary.
The goal is not to model every production branch. The goal is to make the
active-context idea explicit and teachable.
"""
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from tool_schema import COMPACT_TOOLS

load_dotenv(override=True)

WORKDIR = Path.cwd().parent
client = OpenAI(api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL"))
MODEL = os.getenv("MODEL")

SYSTEM = f"""You are a coding agent at {WORKDIR}.
Keep working step by step, and use compact if the conversation gets too long."""

CONTEXT_LIMIT = int(1e4)
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = int(6e3)
PREVIEW_CHARS = int(2e3)
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".transcripts" / "tool-results"


@dataclass
class CompactState:
    has_compacted: bool = False
    last_summary: str = ""
    recent_files: list[str] = field(default_factory=list)


def estimate_context_size(messages: list) -> int:
    return len(str(messages))


def track_recent_file(state: CompactState, path: str) -> None:
    if path in state.recent_files:
        state.recent_files.remove(path)
    state.recent_files.append(path)
    if len(state.recent_files) > 5:
        state.recent_files[:] = state.recent_files[-5:]


def persist_large_output(tool_call_id: str, output: str) -> str:
    """
    若工具输出结果长度超出限制，将其保存在文件中，并替换为带有保存文件信息和结果预览的信息版本
    Args:
        tool_call_id: 
        output: 

    Returns:

    """
    if len(output) < PERSIST_THRESHOLD:
        return output

    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stored_path = TOOL_RESULTS_DIR / f"{tool_call_id}.txt"
    # todo is right?
    if not stored_path.exists():
        stored_path.write_text(output)

    preview = output[:PREVIEW_CHARS]
    relative_path = stored_path.relative_to(WORKDIR)

    return (
        f"<persisted-output>\n"
        f"Full output saved to: {relative_path}\n"
        f"Preview:\n"
        f"{preview}\n"
        f"</persisted-output>"
    )


def collect_tool_result_blocks(messages: list) -> list[tuple[int, int, dict]]:
    tool_results = []
    for message_index, message in enumerate(messages):
        # todo: tool call messages
        if isinstance(message, dict) and message.get("role") == "tool":
            tool_results.append((message_index, 1, message))

    return tool_results


def micro_compact(messages: list) -> list:
    """
    将较早且内容较长的工具信息替换为简单的提示信息。
    Args:
        messages:

    Returns:

    """
    tool_results = collect_tool_result_blocks(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages

    for _, _, message in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        content = message.get("content", "")
        if len(content) < 120:
            continue

        message["content"] = "[Earlier tool result compacted. Re-run the tool if you need full detail.]"

    return messages


def write_transcript(messages: list) -> Path:
    """
    将消息列表以 json 格式保存在文件中
    Args:
        messages:

    Returns:

    """
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.json"
    with path.open("w") as f:
        for message in messages:
            f.write(json.dumps(message, default=str) + "\n")

    return path


def summarize_history(messages: list) -> str:
    """
    总结消息历史，保留其中重要的信息
    Args:
        messages:

    Returns:

    """
    conversation = json.dumps(messages, default=str)[:int(8e4)]
    prompt = (
        f"Summarize this coding-agent conversation so work cna continue.\n"
        f"Preserve:\n"
        f"1. The current goal\n"
        f"2. Important findings and decisions\n"
        f"3. Files read or changed\n"
        f"4. Remaining work\n"
        f"5. User constraints and preferences\n"
        f"Be compact but concrete.\n\n"
        f"{conversation}"
    )
    response = client.responses.create(
        model=MODEL,
        input=prompt,
        max_output_tokens=int(2e3)
    )
    return response.output_text.strip()


def compact_history(messages: list, state: CompactState, focus: Optional[str] = None) -> list:
    """
    压缩上下文，将消息记录保存在文件中，并返回总结后的消息列表
    Args:
        messages:
        state:
        focus:

    Returns:

    """
    transcript_path = write_transcript(messages)
    print(f">> [transcript saved: {transcript_path}]")

    summary = summarize_history(messages)
    if focus:
        summary += f"\n\nFocus to preserve next: {focus}"
    if state.recent_files:
        recent_files = "\n".join(f"- {path}" for path in state.recent_files)
        summary += f"\n\nRecent files to reopen if needed:\n{recent_files}"

    state.has_compacted = True
    state.last_summary = summary

    return [{
        "role": "user",
        "content": (
            f"This conversation was compacted so the agent can continue working.\n\n"
            f"{summary}"
        )
    }]


def safe_path(path: str) -> Path:
    path = (WORKDIR / path).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str, tool_call_id: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return f"Error: Dangerous command blocked, command: {command}"

    try:
        output = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )

    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

    output = (output.stdout + output.stderr).strip() or "Tool no output"
    return persist_large_output(tool_call_id, output)


def run_read(path: str, tool_use_id: str, state: CompactState, limit: Optional[int] = None) -> str:
    try:
        track_recent_file(state, path)
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]

        output = "\n".join(lines)
        return persist_large_output(tool_use_id, output)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def execute_tool(tool_call, state: CompactState) -> str:
    tool_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    if tool_name == "bash":
        return run_bash(**args, tool_call_id=tool_call.id)
    elif tool_name == "read_file":
        return run_read(**args, tool_use_id=tool_call.id, state=state)
    elif tool_name == "write_file":
        return run_write(**args)
    elif tool_name == "edit_file":
        return run_edit(**args)
    elif tool_name == "compact":
        return "Compacting conversation..."

    return f"Unknow tool: {tool_name}"


def agent_loop(messages: list, state: CompactState):
    while True:
        messages[:] = micro_compact(messages)

        if estimate_context_size(messages) > CONTEXT_LIMIT:
            print(">> [auto compact]")
            messages[:] = compact_history(messages, state)

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=COMPACT_TOOLS,
            max_tokens=int(8e3),
            extra_body={"enable_thinking": False}
        )

        if response.choices[0].finish_reason != "tool_calls":
            #     messages.append(response.choices[0].message)
            # else:
            messages.append(
                {"role": "assistant", "content": response.choices[0].message.content}
            )
            return

        results = []
        tool_call_messages = [{"role": "assistant", "tool_calls": []}]
        manual_compact = False
        compact_focus = None
        for tool_call in response.choices[0].message.tool_calls:
            output = execute_tool(tool_call, state)
            if tool_call.function.name == "compact":
                manual_compact = True
                args = json.loads(tool_call.function.arguments)
                compact_focus = (args or {}).get("focus")

            print(f">> {tool_call.function.name}: {str(output)[:200]}")
            tool_call_messages[0]["tool_calls"].append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                }
            })
            results.append({
                "role": "tool", "content": output, "tool_call_id": tool_call.id
            })

        messages.extend(tool_call_messages + results)
        if manual_compact:
            print(">> [manual compact]")
            messages[:] = compact_history(messages, state, focus=compact_focus)


if __name__ == "__main__":
    messages = [{"role": "system", "content": SYSTEM}]
    state = CompactState()
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        messages.append({"role": "user", "content": query})
        agent_loop(messages, state)

        response_content = messages[-1]["content"]
        if response_content:
            print(f"AI:\n{response_content}")

        print()
