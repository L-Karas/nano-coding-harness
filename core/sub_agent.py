"""
Sub Agent
"""
import json

from core.config import WORKDIR, client, SUB_MODEL
from core.permission.hook_permission import trigger_hooks

SUB_SYSTEM = (f"You are a coding subagent at {WORKDIR}."
              f"Complete the task, then return a concise final conclusion. "
              f"Do not spawn more agents.")


# 延迟到函数内导入:src.tools -> extra_tools -> sub_agent -> src.tools 存在导入环,
# 模块级导入会触发 partially initialized ImportError。
def spawn_subagent(description: str) -> str:
    from core.tools import call_tool_handler, get_builtin_tools, get_builtin_tool_handlers

    sub_tools = get_builtin_tools("sub-agent")
    sub_handlers = get_builtin_tool_handlers("sub-agent")
    messages = [
        {"role": "system", "content": SUB_SYSTEM},
        {"role": "user", "content": description}
    ]

    for _ in range(30):
        response = client.chat.completions.create(
            model=SUB_MODEL,
            messages=messages,
            tools=sub_tools,
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
                handler = sub_handlers.get(tool_call.function.name)
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

    return "Subagent finished without a text conclusion."
