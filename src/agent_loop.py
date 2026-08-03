"""
Agent Loop
"""
import json
import threading
import time

from openai import Stream
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from background_task import collect_background_results, should_run_background, start_background_task
from base_tool_handlers import BUILTIN_HANDLERS
from base_tools import call_tool_handler
from config import CONTEXT_LIMIT, client, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES, \
    CONTINUATION_PROMPT
from context import update_context
from context_compact import tool_result_budget, snip_compact, micro_compact, estimate_size, compact_history, \
    reactive_compact, message_has_tool_use
from cron_scheduler import consume_cron_queue
from error_recovery import RecoveryState, with_retry, is_prompt_too_long_error
from hook_permission import trigger_hooks
from prompt import assemble_system_prompt
from protocol_state import consume_lead_inbox
from tool_schema import BUILTIN_TOOLS
from ui import (
    render_banner,
    render_user_input,
    render_tool_call,
    render_tool_result,
    render_assistant_response,
    render_background_notification,
    get_user_input, render_thinking_status
)

ROUNDS_SINCE_TODO = 0
AGENT_LOCK = threading.Lock()


def assemble_tool_pool():
    """
    Merge builtin tools + all MCP tools into a single tool pool.
    """
    tools = list(BUILTIN_TOOLS)
    handlers = BUILTIN_HANDLERS
    # todo: mcp tools

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


# todo: content append
def build_user_content(results: list[dict]) -> list[dict]:
    """
    Tool results and completed background notifications are both returned to the model
    as user content, matching the tool call feedback loop.
    """
    content = list(results)
    for note in collect_background_results():
        content.append()

    return content


def inject_background_notifications(messages: list) -> list:
    notes = collect_background_results()
    if notes:
        messages.append({
            "role": "user",
            "content": "\n\n".join(notes),
        })


def call_llm(
        messages: list,
        context: dict,
        tools: list,
        state: RecoveryState,
        max_tokens: int
) -> ChatCompletion | Stream[ChatCompletionChunk]:
    system = assemble_system_prompt(context)
    messages = [{"role": "system", "content": system}] + messages

    with render_thinking_status():
        return with_retry(
            lambda: client.chat.completions.create(
                model=state.current_model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
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
            messages.append({
                "role": "user",
                "content": f"[Scheduled crons] {cron.prompt}"
            })
            render_background_notification(f"Cron Prompt: {cron.prompt}", title="⏰ Cron Injected")

        inject_background_notifications(messages)

        if ROUNDS_SINCE_TODO >= 3:
            messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>",
            })
            ROUNDS_SINCE_TODO = 0

        prepare_context(messages)
        context = update_context(context, messages)
        tools, handlers = assemble_tool_pool()

        try:
            response = call_llm(messages, context, tools, state, max_tokens)
        except Exception as e:
            if is_prompt_too_long_error(e) and state.has_attempted_reactive_compact:
                messages[:] = reactive_compact(messages)
                state.has_attempted_reactive_compact = True
                continue
            messages.append({
                "role": "assistant",
                "content": f"[Error] {type(e).__name__}: {e}"
            })
            return

        if response.choices[0].finish_reason == "length":
            if not state.has_escalated:
                max_tokens = ESCALATED_MAX_TOKENS
                state.has_escalated = True
                print(f"  \033[33m[Max tokens] retry with {max_tokens}\033[0m")
                continue
            messages.append({
                "role": "assistant",
                "content": response.choices[0].message.content
            })

            if state.recovery_count < MAX_RECOVERY_RETRIES:
                messages.append({
                    "role": "user",
                    "content": CONTINUATION_PROMPT
                })
                state.recovery_count += 1
                continue
            return

        max_tokens = DEFAULT_MAX_TOKENS
        state.has_escalated = False

        if not message_has_tool_use(response.choices[0].message):
            messages.append({
                "role": "assistant",
                "content": response.choices[0].message.content
            })
            trigger_hooks("Stop", messages)
            return

        results = []
        compact_now = False
        messages.append(response.choices[0].message)
        for tool_call in response.choices[0].message.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            render_tool_call(tool_name, tool_args)

            if tool_name == "compact":
                messages[:] = compact_history(messages)
                messages.append({
                    "role": "user",
                    "content": "[Compacted. Continue with summarized context.]"
                })
                compact_now = True

            blocked = trigger_hooks("PreToolUse", tool_call)
            if blocked:
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(blocked)
                })
                continue

            if should_run_background(tool_name, tool_args):
                bg_id = start_background_task(tool_call, handlers)
                output = (f"[Background task {bg_id} started] "
                          f"Result will arrive as a task_notification.")
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output
                })
                continue

            handler = handlers.get(tool_name)
            output = call_tool_handler(handler, tool_args, tool_name)
            trigger_hooks("PostToolUse", tool_call, output)
            render_tool_result(output)

            if tool_name == "todo_write":
                ROUNDS_SINCE_TODO = 0
            else:
                ROUNDS_SINCE_TODO += 1

            results.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(output)
            })

        if compact_now:
            continue

        messages.extend(results)


def print_turn_assistants(messages: list, turn_start: int):
    for msg in messages[turn_start:]:
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            render_assistant_response(msg["content"])


def cron_auto_loop(messages: list, context: dict):
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue

        with AGENT_LOCK:
            turn_start = len(messages)
            for job in fired:
                messages.append({
                    "role": "user",
                    "content": f"[Scheduled] {job.prompt}"
                })
                render_background_notification(f"Cron Auto Prompt: {job.prompt}", title="⏰ Cron Triggered")

            agent_loop(messages, context)
            context.update(update_context(context, messages))
            print_turn_assistants(messages, turn_start)


if __name__ == '__main__':
    CLI_ACTIVE = True
    render_banner("🤖 Nano-Harness Agent Loop", "Enter a question, press Enter to send. Type /exit or q to quit.")

    messages = []
    context = update_context({}, [])
    threading.Thread(target=cron_auto_loop, args=(messages, context), daemon=True).start()

    while True:
        try:
            query = get_user_input()
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ["q", "quit", "exit", "/exit", "/quit"]:
            break

        render_user_input(query)
        trigger_hooks("UserPromptSubmit", query)
        turn_start = len(messages)
        messages.append({
            "role": "user",
            "content": query
        })

        with AGENT_LOCK:
            agent_loop(messages, context)
            context = update_context(context, messages)
            print_turn_assistants(messages, turn_start)

        inbox = consume_lead_inbox(route_protocol=True)
        if inbox:
            def inbox_label(message):
                request_id = message.get("metadata", {}).get("request_id", "")
                suffix = f"  request_id: {request_id}" if request_id else ""
                return f"{message.get('msg_type', 'message')}{suffix}"


            inbox_text = "\n".join(
                f"From {message['from_agent']} [{inbox_label(message)}]: "
                f"{message['content']}"
                for message in inbox
            )

            messages.append({
                "role": "user",
                "content": f"[Inbox messages]\n{inbox_text}"
            })

        print()
