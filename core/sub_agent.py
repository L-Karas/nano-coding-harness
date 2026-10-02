"""
Sub Agent

跑在调用方的 event loop（AgentRuntime.loop）上：模型客户端绑定该 loop，子代理不另起线程/loop。
后台语义（主代理不等结论、结果稍后注入）由 background_task 的占位符 + notification 提供，
这里只负责跑完并把最终结论返回。
"""
import json

from openai.types.chat import ChatCompletionMessageToolCall

from core.client import shared_model_client
from core.config import DEFAULT_MAX_TOKENS
from core.hook.hook import trigger_hooks
from core.log.log import get_logger
from core.prompt import build_system_prompt
from core.recovery.error_recovery import with_retry_async
from core.streaming import streaming_message

MAX_SUB_AGENT_ROUNDS = 30
_LOGGER = get_logger(__name__)

# TUI 数据源：运行中的子代理及其执行阶段（用法同 core.background_task.BACKGROUND_TASKS）。
# phase: "thinking" = 等 LLM 生成中， "tool" = 工具执行中；detail 为正在跑的工具名。结束即移除。
SUBAGENT_TASKS: dict[str, dict] = {}
_SUBAGENT_COUNTER = 0


def _set_phase(agent_id: str, phase: str, detail: str = "") -> None:
    info = SUBAGENT_TASKS.get(agent_id)
    if info is not None:
        info["phase"], info["detail"] = phase, detail


async def spawn_subagent(description: str, ctx=None) -> str:
    """在调用方 loop 上跑完一个子代理，返回它的最终文本结论。

    ctx 为父回合的 AgentRunContext：每轮模型调用前协作式检查取消（Esc），
    并透传给工具执行，使中断能到达子进程。
    """
    global _SUBAGENT_COUNTER

    from core.tools import assemble_tool_pool
    from core.tools.tool_loader import execute_tool

    _SUBAGENT_COUNTER += 1
    agent_id = f"sa-{_SUBAGENT_COUNTER:04d}"
    SUBAGENT_TASKS[agent_id] = {"description": description, "phase": "thinking", "detail": ""}

    try:
        tools, handlers = assemble_tool_pool("sub-agent", tool_type="async")
        messages = [
            {"role": "system", "content": build_system_prompt("sub-agent", tools=tools)},
            {"role": "user", "content": description},
        ]

        for _ in range(MAX_SUB_AGENT_ROUNDS):
            if ctx:
                ctx.raise_if_cancelled()

            _set_phase(agent_id, "thinking")
            stream = await with_retry_async(
                lambda: shared_model_client().get_model_client(async_client=True)(
                    messages=messages,
                    tools=tools,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    stream=True
                )
            )
            content, reasoning_content, tool_calls, _, _ = await streaming_message(stream)

            assistant_message = {
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning_content,
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)

            if not tool_calls:
                return content or "(subagent finished without a text conclusion)"

            _set_phase(agent_id, "tool", ", ".join(tc["function"]["name"] for tc in tool_calls))
            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                blocked = trigger_hooks("pre_tool_call", ChatCompletionMessageToolCall(**tool_call))
                if blocked:
                    output = str(blocked)
                else:
                    try:
                        tool_args = json.loads(tool_call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError as e:
                        output = f"[Error] {type(e).__name__}: {e}"
                    else:
                        output = await execute_tool(handlers.get(name), tool_args, name, ctx)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": str(output),
                })

        _LOGGER.warning(f"[Subagent] reached {MAX_SUB_AGENT_ROUNDS} rounds without a conclusion")
        return f"(subagent reached the {MAX_SUB_AGENT_ROUNDS}-round limit without a conclusion)"
    finally:
        SUBAGENT_TASKS.pop(agent_id, None)
