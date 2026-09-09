"""
Agent Loop
"""
import json
import threading
import time

from openai import Stream
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessageToolCall

from core.background_task import collect_background_results, should_run_background, start_background_task
from core.compact.context_compact import tool_result_budget, snip_compact, micro_compact, estimate_size, \
    compact_history, \
    reactive_compact
from core.config import CONTEXT_LIMIT, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES
from core.cron_scheduler import consume_cron_queue
from core.log.log import get_logger
from core.model import shared_model_client
from core.permission.hook_permission import trigger_hooks
from core.prompt import build_system_prompt
from core.recovery.error_recovery import RecoveryState, with_retry, is_prompt_too_long_error
from core.session.session import SESSION_MANAGER
from core.template import CONTINUATION_PROMPT, INJECTION_MESSAGES_PREFIX, INJECTION_MESSAGES_SUFFIX
from core.tools import TOOL_ERROR_PREFIXES, call_tool_handler, assemble_tool_pool
from core.tools.base_tools.git import DIFF_TOOLS, preview_edit, preview_write
from core.tui.render import render_scope, stream_assistant_response, render_tool_call, render_tool_result, \
    render_tool_result_diff, render_background_notification, render_thinking_status, render_tool_calling_status

ROUNDS_SINCE_TODO = 0
AGENT_LOCK = threading.Lock()
_LOGGER = get_logger(__name__)


def prepare_messages(messages: list) -> list:
    """
    Every LLM turn enters through the same context budget pipeline.
    """
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > CONTEXT_LIMIT:
        messages[:] = compact_history(messages)

    return messages


def inject_background_notifications():
    notes = collect_background_results()
    if notes:
        SESSION_MANAGER.add_message({
            "role": "user",
            "content": INJECTION_MESSAGES_PREFIX + "\n".join(notes) + INJECTION_MESSAGES_SUFFIX,
        })


def stream_message(stream: Stream[ChatCompletionChunk]) -> tuple[str, str, list, str, CompletionUsage]:
    """
    Consume the full stream, folding tool-call deltas into complete calls.
    Returns (accumulated_text, tool_calls, finish_reason).
    """
    accumulated_text = ""
    reasoning_text = ""
    tool_calls: list[dict] = []
    finish_reason = ""
    usage = None
    with render_scope():
        for chunk in stream:
            if not chunk.choices:
                # 代理（one-api/new-api 等）常在流末尾附 usage-only 尾块：choices 为空，仅记录 usage
                if chunk.usage:
                    usage = chunk.usage
                continue
            choice = chunk.choices[0]
            if hasattr(choice.delta, "reasoning_content") and choice.delta.reasoning_content:
                reasoning_text += choice.delta.reasoning_content
                continue
            if choice.delta.content:
                accumulated_text += choice.delta.content
                stream_assistant_response(choice.delta.content)  # 只传新增片段，UI 端增量追加渲染
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

    return accumulated_text, reasoning_text, tool_calls, finish_reason, usage


def call_tools(tool_calls: list[dict], handlers: dict) -> None:
    global ROUNDS_SINCE_TODO

    tool_call_results = []
    for tool_call_dict in tool_calls:
        tool_call = ChatCompletionMessageToolCall(**tool_call_dict)
        tool_name = tool_call.function.name
        tool_args = json.loads(tool_call.function.arguments)

        render_tool_call(tool_name, tool_args)

        if tool_name == "compact":
            messages = compact_history(SESSION_MANAGER.load_messages())
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
        diff = ""
        with render_tool_calling_status(f"{tool_name}({tool_args})"):
            if tool_name in DIFF_TOOLS:
                try:
                    diff = (preview_write(tool_args["path"], tool_args["content"]) if tool_name == "write_file"
                            else preview_edit(tool_args["path"], tool_args["old_text"], tool_args["new_text"]))
                except Exception as e:
                    _LOGGER.exception(f"[Diff exception] {e}]")

            output = call_tool_handler(handler, tool_args, tool_name)
            # 失败时 diff 只是未落地的预览：不渲染、不记录 payload，回放才不会把未应用改动显示成已应用。
            tool_failed = str(output).startswith(TOOL_ERROR_PREFIXES)
            if diff and not tool_failed:
                render_tool_result_diff(diff)

        render_tool_result(output)

        if tool_name == "todo_write":
            ROUNDS_SINCE_TODO = 0
        else:
            ROUNDS_SINCE_TODO += 1

        tool_call_results.append({
                                     "role": "tool",
                                     "tool_call_id": tool_call.id,
                                     "content": str(output)
                                 } | ({"payload": diff} if diff and not tool_failed else {}))

    for tool_result in tool_call_results:
        SESSION_MANAGER.add_message(tool_result)


def call_llm(
        tools: list,
        max_tokens: int
) -> ChatCompletion | Stream[ChatCompletionChunk]:
    system = build_system_prompt(agent_type="main", tools=tools)
    messages = [{"role": "system", "content": system}] + SESSION_MANAGER.load_messages()

    _LOGGER.debug(f"Session manager loaded messages: {SESSION_MANAGER.load_messages()}")

    with render_thinking_status():
        # 模型配置统一来自 shared_model_client（.harness/.setting.json），不再按调用方指定 model
        return with_retry(
            lambda: shared_model_client().get_model_client()(
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                stream=True
            )
        )


def agent_loop():
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
                "content": INJECTION_MESSAGES_PREFIX + f"[Scheduled crons] {cron.prompt}" + INJECTION_MESSAGES_SUFFIX
            })
            render_background_notification(f"Cron Prompt: {cron.prompt}", title="⏰ Cron Injected")

        inject_background_notifications()

        # todo: 当有待办 todo 时才使用该提示信息插入
        # if ROUNDS_SINCE_TODO >= 3:
        # messages.append({
        #     "role": "user",
        #     "content": "<reminder>Update your todos.</reminder>",
        # })
        # SESSION_MANAGER.add_message({
        #     "role": "user",
        #     "content": "<reminder>Update your todos.</reminder>",
        # })
        # ROUNDS_SINCE_TODO = 0

        messages = prepare_messages(SESSION_MANAGER.load_messages())
        SESSION_MANAGER.update_messages(messages)
        tools, handlers = assemble_tool_pool("main")

        try:
            stream = call_llm(tools, max_tokens)
        except Exception as e:
            if is_prompt_too_long_error(e) and state.has_attempted_reactive_compact:
                messages[:] = reactive_compact(SESSION_MANAGER.load_messages())
                SESSION_MANAGER.update_messages(messages)
                state.has_attempted_reactive_compact = True
                continue
            # todo: 是否需要将模型调用错误信息作为消息历史的一部分
            # 错误只落会话不渲染 = 用户输入后无任何反馈；同步出错误卡（线程安全渲染 API）
            error_text = f"[Error] {type(e).__name__}: {e}"
            SESSION_MANAGER.add_message({"role": "assistant", "content": error_text})
            render_background_notification(error_text, title="⚠️ Agent Error")
            return

        accumulated_text, reasoning_text, tool_calls, finish_reason, usage = stream_message(stream)

        assistant_message = {
            "role": "assistant",
            "content": "" or accumulated_text,
            "reasoning_content": "" or reasoning_text,
        }
        if finish_reason == "length":
            if not state.has_escalated:
                max_tokens = ESCALATED_MAX_TOKENS
                state.has_escalated = True
                _LOGGER.info(f"[Max tokens] retry with {max_tokens}")
                continue
            SESSION_MANAGER.add_message(assistant_message)

            if state.recovery_count < MAX_RECOVERY_RETRIES:
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
            SESSION_MANAGER.add_message(assistant_message)
            return
        else:
            assistant_message["tool_calls"] = tool_calls
            SESSION_MANAGER.add_message(assistant_message)
            call_tools(tool_calls, handlers)


def cron_auto_loop():
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue

        with AGENT_LOCK:
            for job in fired:
                SESSION_MANAGER.add_message({
                    "role": "user",
                    "content": INJECTION_MESSAGES_PREFIX + f"[Scheduled Cron]\n{job.prompt}" + INJECTION_MESSAGES_SUFFIX
                })
                render_background_notification(f"Cron Auto Prompt: {job.prompt}", title="⏰ Cron Triggered")

            agent_loop()


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

    stream = shared_model_client().get_model_client()(
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
