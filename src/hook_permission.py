"""
Hooks + Permission Pipeline

# Hooks are intentionally outside tool handlers. The loop can add permission,
# logging, and stop behavior without changing each individual tool.
"""
import json
from typing import Optional

from openai.types.chat import ChatCompletionMessageToolCallUnion

from src.config import WORKDIR
from src.tui.ui import ask_permission

HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]


def register_hook(event: str, callback):
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result

    return None


def _confirm(question: str) -> bool:
    """通过 UI 面板询问用户，确认（y/yes）返回 True，其余一律拒绝"""
    return ask_permission(question).strip().lower() in ("y", "yes")


def permission_hook(tool_call: Optional[ChatCompletionMessageToolCallUnion] = None):
    # The permission layer sees the raw tool_use before dispatch. It can deny,
    # ask the user, or allow execution to continue.
    tool_args = json.loads(tool_call.function.arguments)

    if tool_call.function.name == "bash":
        command = tool_args.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied: '{pattern}' is on the deny list."

        if any(item in command for item in DESTRUCTIVE):
            if not _confirm(f"Destructive command detected:\n\n{command}"):
                return "Permission denied by user"

    if tool_call.function.name in ("read_file", "write_file", "edit_file"):
        path = tool_args.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            if not _confirm(f"Access outside workspace:\n\n{tool_call.function.name}: {path}"):
                return "Permission denied by user"

    # todo: mcp tool
    if tool_call.function.name.startswith("mcp_") and "deploy" in tool_call.function.name:
        pass

    return None


def log_hook(tool_call: Optional[ChatCompletionMessageToolCallUnion] = None):
    print(f"\033[90m[HOOK] {tool_call.function.name}\033[0m")
    return None


def large_output_hook(tool_call: Optional[ChatCompletionMessageToolCallUnion] = None, tool_output: str = ""):
    if len(str(tool_output)) > 1e5:
        print(f"\033[33m[HOOK] large output from {tool_call.function.name}: "
              f"{len(str(tool_output))} chars\033[0m")
    return None


def user_prompt_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: {query}\033[0m")
    return None


def stop_hook(messages: list):
    tool_count = 0
    for message in messages:
        if not isinstance(message, dict):
            tool_count += len(message.tool_calls)
    print(f"\033[90m[HOOK] Stop: {tool_count} tool result(s)\033[0m")
    return None


register_hook("UserPromptSubmit", user_prompt_hook)
register_hook("Stop", stop_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
