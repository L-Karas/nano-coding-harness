"""
Sub Agent
"""
import json

from core.model import shared_model_client
from core.permission.hook_permission import trigger_hooks
from core.prompt import build_system_prompt


# 延迟到函数内导入:src.tools -> extra_tools -> sub_agent -> src.tools 存在导入环,
# 模块级导入会触发 partially initialized ImportError。
def spawn_subagent(description: str) -> str:
    from core.tools import assemble_tool_pool, call_tool_handler

    sub_tools, sub_handlers = assemble_tool_pool(agent_type="sub-agent")
    system_prompt = build_system_prompt("sub-agent", tools=sub_tools)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": description}
    ]

    for _ in range(30):
        # 模型统一走 shared_model_client（.harness/.setting.json），不再用独立 sub model
        response = shared_model_client().get_model_client()(
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
