"""
s02_tool_use.py - Tool dispatch + message normalization
The agent loop from s01 didn't change. We added tools to the dispatch map,
and a normalize_messages() function that cleans up the message list before
each API call.
Key insight: "The loop didn't change at all. I just added tools."
"""

import json
import os
import subprocess
from pathlib import Path
import sys

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

WORKDIR = Path.cwd()
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL")
)
model = os.getenv("MODEL")

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, dont't explain."


def safe_path(path: str) -> Path:
    path = (WORKDIR / path).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path}")

    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Tool Error: Dangerous command blocked."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        result = (result.stdout + result.stderr).strip()

        return result[:5000] if result else "Tool execute success. No output."
    except subprocess.TimeoutExpired:
        return "Tool Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)."]
        return "\n".join(lines)[:5000]
    except Exception as e:
        return "Tool Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    except Exception as e:
        return f"Tool Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Tool Error: Text not found in {path}."

        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"

    except Exception as e:
        return f"Tool Error: {e}"


# -- Concurrency safety classification --
# Read-only tools can safely run in parallel; mutating tools must be serialized.
CONCURRENCY_SAFE = {"read_file"}
CONCURRENCY_UNSAFE = {"write_file", "edit_file"}

# -- The dispatch map: {tool_name: handler} --
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
]


def agent_loop(messages: list):
    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=8000,
            extra_body={"enable_thinking": False},
            tools=TOOLS,
        )

        if response.choices[0].finish_reason == "tool_calls":
            messages.append(response.choices[0].message)
            # print(f"Tool calls: {response.choices[0].message.tool_calls}")
        else:
            messages.append(
                {"role": "assistant", "content": response.choices[0].message.content}
            )
            return

        results = []
        for block in response.choices[0].message.tool_calls:
            if block.type == "function":
                handler = TOOL_HANDLERS.get(block.function.name)
                args = json.loads(block.function.arguments)
                output = (
                    handler(**args)
                    if handler
                    else f"Unknown tool: {block.function.name}"
                )

                print(f"> {block.function.name}:")
                print(output[:200])

                results.append(
                    {"role": "tool", "content": output, "tool_call_id": block.id}
                )

        messages.extend(results)


if __name__ == "__main__":
    history = []

    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})

        agent_loop(history)

        response_content = history[-1]["content"]

        print("AI:\n", file=sys.stderr)
        if isinstance(response_content, str):
            print(response_content)
        elif isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)

        print()
