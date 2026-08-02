"""
Sub Agent
"""
import json

from base_tools import run_bash, run_read, run_write, run_edit, run_glob, call_tool_handler
from config import WORKDIR, client, SUB_MODEL
from hook_permission import trigger_hooks

SUB_SYSTEM = (f"You are a coding subagent at {WORKDIR}."
              f"Complete the task, then return a concise final summary. "
              f"Do not spawn more agents.")

SUB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read."},
                    "limit": {"type": "integer", "description": "Max lines to read."},
                    "offset": {"type": "integer", "description": "Line offset to start reading from."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to write."},
                    "content": {"type": "string", "description": "Content to write to the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string",
                                "description": "The glob pattern to match files against (e.g. '**/*.py')."},
                },
                "required": ["pattern"],
            },
        },
    },
]

SUB_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write, "edit_file": run_edit,
    "glob": run_glob
}


def spawn_subagent(description: str) -> str:
    messages = [
        {"role": "system", "content": SUB_SYSTEM},
        {"role": "user", "content": description}
    ]

    for _ in range(30):
        response = client.chat.completions.create(
            model=SUB_MODEL,
            messages=messages,
            tools=SUB_TOOLS,
            max_tokens=8000,
        )
        response_message = response.choices[0].message.model_dump()
        messages.append(response_message)
        if not response_message.tool_calls:
            break

        for tool_call in response_message.tool_calls:
            blocked = trigger_hooks("PreToolUse", tool_call)
            if blocked:
                output = str(blocked)
            else:
                handler = SUB_HANDLERS.get(tool_call.function.name)
                tool_args = json.loads(tool_call.function.arguments)
                output = call_tool_handler(handler, tool_args, tool_call.function.name)
                trigger_hooks("PostToolUse", tool_call, output)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(output),
            })

    for message in reversed(messages):
        if isinstance(message, dict) and message["role"] == "assistant":
            summary = message["content"]
            if summary:
                return summary

    return "Subagent finished without a text summary."
