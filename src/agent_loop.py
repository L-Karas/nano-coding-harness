"""
Agent Loop
"""
import json
import threading
import time

from openai import Stream
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessageToolCall

from src.background_task import collect_background_results, should_run_background, start_background_task
from src.base_tool_handlers import BUILTIN_HANDLERS
from src.base_tools import call_tool_handler, DIFF_TOOLS, preview_write, preview_edit
from src.compact.context_compact import tool_result_budget, snip_compact, micro_compact, estimate_size, compact_history, \
    reactive_compact
from src.config import CONTEXT_LIMIT, client, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES, \
    CONTINUATION_PROMPT
from src.context import update_context
from src.cron_scheduler import consume_cron_queue
from src.error_recovery import RecoveryState, with_retry, is_prompt_too_long_error
from src.hook_permission import trigger_hooks
from src.log.log import get_logger
from src.mcps import get_client_manager
from src.prompt import assemble_system_prompt
from src.session.session import SESSION_MANAGER
from src.tool_schema import BUILTIN_TOOLS
from src.ui import render_scope, stream_assistant_response
from src.ui import (
    render_tool_call,
    render_tool_result,
    render_tool_result_diff,
    render_background_notification,
    render_thinking_status, render_tool_calling_status
)

ROUNDS_SINCE_TODO = 0
AGENT_LOCK = threading.Lock()
_LOGER = get_logger(__name__)


def assemble_tool_pool():
    """
    Merge builtin tools + all MCP tools into a single tool pool.
    """
    tools = list(BUILTIN_TOOLS)
    handlers = BUILTIN_HANDLERS
    # todo: mcp tools
    try:
        mcp_client_manager = get_client_manager()
    except Exception as e:
        _LOGER.exception(f"[MCP] init failed, falling back to builtin tools: {e}")
        mcp_client_manager = None

    if mcp_client_manager:
        tools.extend(mcp_client_manager.list_tools())
        handlers = handlers | mcp_client_manager.tool_handlers

    return tools, handlers


def prepare_context(messages: list) -> list:
    """
    Every LLM turn enters through the same context budget pipeline.
    """
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > CONTEXT_LIMIT:
        messages[:] = compact_history(messages)

    return messages


def inject_background_notifications(messages: list = []) -> list:
    notes = collect_background_results()
    if notes:
        # messages.append({
        #     "role": "user",
        #     "content": "\n\n".join(notes),
        # })
        SESSION_MANAGER.add_message({
            "role": "user",
            "content": "\n\n".join(notes),
        })


def stream_message(stream: Stream[ChatCompletionChunk]) -> tuple[str, list, str, CompletionUsage]:
    """
    Consume the full stream, folding tool-call deltas into complete calls.
    Returns (accumulated_text, tool_calls, finish_reason).
    """
    accumulated_text = ""
    tool_calls: list[dict] = []
    finish_reason = ""
    usage = None
    with render_scope():
        for chunk in stream:
            choice = chunk.choices[0]
            if getattr(choice.delta, "reasoning_content", None):
                continue
            if choice.delta.content:
                accumulated_text += choice.delta.content
                stream_assistant_response(accumulated_text)
            if choice.delta.tool_calls:
                for delta in choice.delta.tool_calls:
                    while len(tool_calls) <= delta.index:
                        tool_calls.append({
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                    if delta.id:
                        tool_calls[delta.index]["id"] = delta.id
                    if delta.function:
                        if delta.function.name and not tool_calls[delta.index]["function"]["name"]:
                            tool_calls[delta.index]["function"]["name"] = delta.function.name
                        if delta.function.arguments:
                            tool_calls[delta.index]["function"]["arguments"] += delta.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            if chunk.usage:
                usage = chunk.usage

    return accumulated_text, tool_calls, finish_reason, usage


def call_tools(tool_calls: list[dict], messages: list, handlers: dict) -> None:
    global ROUNDS_SINCE_TODO

    messages = SESSION_MANAGER.load_messages()
    tool_call_results = []
    for tool_call_dict in tool_calls:
        tool_call = ChatCompletionMessageToolCall(**tool_call_dict)
        tool_name = tool_call.function.name
        tool_args = json.loads(tool_call.function.arguments)
        render_tool_call(tool_name, tool_args)

        if tool_name == "compact":
            messages[:] = compact_history(messages)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": "[Compacted. Continue with summarized context.]"
            })
            SESSION_MANAGER.update_messages(messages)

        blocked = trigger_hooks("PreToolUse", tool_call)
        if blocked:
            tool_call_results.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(blocked)
            })
            continue

        if should_run_background(tool_name, tool_args):
            bg_id = start_background_task(tool_call, handlers)
            output = (f"[Background task {bg_id} started] "
                      f"Result will arrive as a task_notification.")
            tool_call_results.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            })
            continue

        handler = handlers.get(tool_name)
        with render_tool_calling_status(f"{tool_name}({tool_args})"):
            if tool_name in DIFF_TOOLS:
                try:
                    diff = (preview_write(tool_args["path"], tool_args["content"]) if tool_name == "write_file"
                            else preview_edit(tool_args["path"], tool_args["old_text"], tool_args["new_text"]))
                except Exception:
                    diff = ""
                if diff:
                    render_tool_result_diff(diff)
            output = call_tool_handler(handler, tool_args, tool_name)

        # trigger_hooks("PostToolUse", tool_call, output)

        render_tool_result(output)

        if tool_name == "todo_write":
            ROUNDS_SINCE_TODO = 0
        else:
            ROUNDS_SINCE_TODO += 1

        tool_call_results.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": str(output)
        })

    # messages.extend(tool_call_results)
    for tool_result in tool_call_results:
        SESSION_MANAGER.add_message(tool_result)


def call_llm(
        messages: list,
        context: dict,
        tools: list,
        state: RecoveryState,
        max_tokens: int
) -> ChatCompletion | Stream[ChatCompletionChunk]:
    system = assemble_system_prompt(context)
    messages = [{"role": "system", "content": system}] + messages
    messages = [{"role": "system", "content": system}] + SESSION_MANAGER.load_messages()

    _LOGER.debug(f"Session manager loaded messages: {SESSION_MANAGER.load_messages()}")

    with render_thinking_status():
        return with_retry(
            lambda: client.chat.completions.create(
                model=state.current_model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                stream=True
            ),
            state
        )


def agent_loop(messages: list, context: dict):
    global ROUNDS_SINCE_TODO
    state = RecoveryState()
    max_tokens = DEFAULT_MAX_TOKENS

    while True:
        # One cycle: inject scheduled/background work, prepare context, call the model,
        #  execute tool calls, append tool results, repeat.
        fired_crons = consume_cron_queue()
        for cron in fired_crons:
            SESSION_MANAGER.add_message({
                "role": "user",
                "content": f"[Scheduled crons] {cron.prompt}"
            })
            # messages.append({
            #     "role": "user",
            #     "content": f"[Scheduled crons] {cron.prompt}"
            # })
            render_background_notification(f"Cron Prompt: {cron.prompt}", title="⏰ Cron Injected")

        inject_background_notifications(messages)

        if ROUNDS_SINCE_TODO >= 3:
            # messages.append({
            #     "role": "user",
            #     "content": "<reminder>Update your todos.</reminder>",
            # })
            SESSION_MANAGER.add_message({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>",
            })
            ROUNDS_SINCE_TODO = 0

        prepare_context(SESSION_MANAGER.load_messages())
        SESSION_MANAGER.update_messages(messages)
        context = update_context(context, messages)
        tools, handlers = assemble_tool_pool()
        # print(SESSION_MANAGER.load_messages())

        try:
            stream = call_llm(messages, context, tools, state, max_tokens)
        except Exception as e:
            if is_prompt_too_long_error(e) and state.has_attempted_reactive_compact:
                messages[:] = reactive_compact(SESSION_MANAGER.load_messages())
                SESSION_MANAGER.update_messages(messages)
                state.has_attempted_reactive_compact = True
                continue
            # messages.append({
            #     "role": "assistant",
            #     "content": f"[Error] {type(e).__name__}: {e}"
            # })
            SESSION_MANAGER.add_message({
                "role": "assistant",
                "content": f"[Error] {type(e).__name__}: {e}"
            })
            return

        accumulated_text, tool_calls, finish_reason, usage = stream_message(stream)

        if finish_reason == "length":
            if not state.has_escalated:
                max_tokens = ESCALATED_MAX_TOKENS
                state.has_escalated = True
                # print(f"  \033[33m[Max tokens] retry with {max_tokens}\033[0m")
                _LOGER.info(f"[Max tokens] retry with {max_tokens}")
                continue
            # messages.append({
            #     "role": "assistant",
            #     "content": accumulated_text
            # })
            SESSION_MANAGER.add_message({
                "role": "assistant",
                "content": accumulated_text
            })

            if state.recovery_count < MAX_RECOVERY_RETRIES:
                # messages.append({
                #     "role": "user",
                #     "content": CONTINUATION_PROMPT
                # })
                SESSION_MANAGER.add_message({
                    "role": "user",
                    "content": CONTINUATION_PROMPT
                })
                state.recovery_count += 1
                continue
            return

        max_tokens = DEFAULT_MAX_TOKENS
        state.has_escalated = False

        if not tool_calls:
            # messages.append({
            #     "role": "assistant",
            #     "content": accumulated_text
            # })
            SESSION_MANAGER.add_message({
                "role": "assistant",
                "content": accumulated_text
            })
            # trigger_hooks("Stop", messages)
            return
        else:
            # messages.append({
            #     "role": "assistant",
            #     "content": accumulated_text or "",
            #     "tool_calls": tool_calls
            # })
            SESSION_MANAGER.add_message({
                "role": "assistant",
                "content": accumulated_text or "",
                "tool_calls": tool_calls
            })
            call_tools(tool_calls, messages, handlers)


def cron_auto_loop(messages: list, context: dict):
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue

        with AGENT_LOCK:
            for job in fired:
                # messages.append({
                #     "role": "user",
                #     "content": f"[Scheduled] {job.prompt}"
                # })
                SESSION_MANAGER.add_message({
                    "role": "user",
                    "content": f"[Scheduled] {job.prompt}"
                })
                render_background_notification(f"Cron Auto Prompt: {job.prompt}", title="⏰ Cron Triggered")

            agent_loop(messages, context)
            context.update(update_context(context, messages))


if __name__ == '__main__':
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取指定地点的天气信息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "地点名称，例如：北京、上海"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
    ]

    stream = client.chat.completions.create(
        model="glm-5.2",
        messages=[
            {"role": "user", "content": "北京今天天气怎么样？"}
        ],
        stream=True,
        temperature=0.8,
        tools=tools,
    )

    for chunk in stream:
        if hasattr(chunk.choices[0].delta, "reasoning_content") and chunk.choices[0].delta.reasoning_content:
            print(chunk.choices[0].delta.reasoning_content, end='')
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end='')
        if chunk.choices[0].delta.tool_calls:
            print(chunk.choices[0].delta.tool_calls, end='')

    # stream_message(stream)

    # print()  # 换行
