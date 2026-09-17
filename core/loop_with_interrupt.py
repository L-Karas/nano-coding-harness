"""
Agent Loop
"""
import asyncio
import concurrent.futures
import json
import threading
import time
from asyncio import Future
from typing import Callable

from openai import AsyncStream
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageToolCall

from core.background_task import collect_background_results, should_run_background, start_background_task
from core.client import shared_model_client
from core.compact.context_compact import tool_result_budget, micro_compact, estimate_size, \
    compact_history
from core.config import CONTEXT_LIMIT, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES
from core.cron_scheduler import consume_cron_queue
from core.experimental.protocol_state import consume_lead_inbox
from core.log.log import get_logger
from core.permission.hook_permission import trigger_hooks
from core.prompt import build_system_prompt
from core.recovery.error_recovery import RecoveryState, with_retry_async
from core.runtime_context import AgentRunContext, AgentInterrupted
from core.session.session import SESSION_MANAGER
from core.template import CONTINUATION_PROMPT, INJECTION_MESSAGES_PREFIX, INJECTION_MESSAGES_SUFFIX, \
    USER_INTERRUPT_PROMPT
from core.template.prompt_template import INJECTION_MESSAGES_TEMPLATE
from core.tools import TOOL_ERROR_PREFIXES, assemble_tool_pool
from core.tools.base_tools.git import DIFF_TOOLS, preview_edit, preview_write
from core.tools.tool_loader import execute_tool
from core.tui.render import render_scope, stream_assistant_response, render_tool_call, render_tool_result, \
    render_tool_result_diff, render_background_notification, render_thinking_status, render_tool_calling_status

AGENT_LOCK = threading.Lock()
_LOGGER = get_logger(__name__)


def _get_interrupt_message(tool_call_id: str):
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": USER_INTERRUPT_PROMPT
    }


class AgentRuntime:

    def __init__(self):
        self._current_ctx: AgentRunContext | None = None
        self._run_task: asyncio.Task | None = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def interrupt(self) -> bool:
        ctx, task = self._current_ctx, self._run_task
        if ctx is None or task is None:
            return False
        if task.done() or ctx.interrupted:
            return False

        def cancel_all():
            ctx.cancel()
            task.cancel()

        loop = task.get_loop()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        try:
            if running is loop:
                cancel_all()
            else:
                task.get_loop().call_soon_threadsafe(cancel_all)
        except RuntimeError:
            return False

        return True

    def submit(self, coro) -> Future:
        """跑协程并阻塞取结果；被 interrupt() 取消（concurrent.futures.CancelledError）时统一抛
        AgentInterrupted，取消语义单一（Esc 的 ctx/task 双路取消都落到同一个异常）。"""
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop=self.loop).result()
        except concurrent.futures.CancelledError:
            raise AgentInterrupted("User interrupted") from None

    async def compact(self) -> None:
        """手动压缩当前会话上下文（UI 的 /compact）：须经 submit() 在 self.loop 上执行——
        模型客户端绑定该事件循环。AGENT_LOCK 由调用线程（UI 的 _compact_worker）持有：
        在协程内取锁会阻塞事件循环，与持锁等待该循环的 cron 线程死等。
        注册 _current_ctx / _run_task，压缩期间 Esc（runtime.interrupt）可中断；中断不落会话。"""
        ctx = AgentRunContext()
        self._current_ctx = ctx
        self._run_task = asyncio.current_task()
        try:
            messages = await compact_history(SESSION_MANAGER.load_messages(), ctx=ctx, auto_compact=False)
            SESSION_MANAGER.update_messages(messages)
        finally:
            self._current_ctx = None
            self._run_task = None

    async def run(self):
        if self._run_task is not None:
            raise RuntimeError("Agent is already running")

        state = RecoveryState()
        ctx = AgentRunContext()
        max_tokens = DEFAULT_MAX_TOKENS
        self._current_ctx = ctx
        self._run_task = asyncio.current_task()

        try:
            while True:
                ctx.raise_if_cancelled()

                fired_crons = consume_cron_queue()
                for cron in fired_crons:
                    ctx.raise_if_cancelled()
                    SESSION_MANAGER.add_message({
                        "role": "user",
                        "content": INJECTION_MESSAGES_TEMPLATE.format(content=f"[Scheduled cron] {cron.prompt}")
                    })
                    render_background_notification(f"Cron prompt: {cron.prompt}", title="⏰ Cron Injected")

                ctx.raise_if_cancelled()
                inject_background_notifications()

                messages = await prepare_messages(SESSION_MANAGER.load_messages(), ctx)
                SESSION_MANAGER.update_messages(messages)
                # todo: 工具获取异步重构?
                tools, handlers = assemble_tool_pool("main", tool_type="async")

                try:
                    stream = await self.call_llm(messages=messages, tools=tools, max_tokens=max_tokens, ctx=ctx)
                except AgentInterrupted:
                    raise
                except Exception as e:
                    # todo: 是否需要将模型调用错误信息作为消息历史的一部分
                    error_text = f"[Error] {type(e).__name__}: {e}"
                    SESSION_MANAGER.add_message({
                        "role": "assistant",
                        "content": error_text
                    })
                    render_background_notification(error_text, title="⚠️ Agent Error")
                    return

                # todo: usage 变量暂未使用
                accumulated_text, reasoning_text, tool_calls, finish_reason, usage = await self.stream(stream, ctx)

                assistant_message = {
                    "role": "assistant",
                    "content": "" or accumulated_text,
                    "reasoning_content": "" or reasoning_text
                }
                if finish_reason == "length":
                    # todo: 半截 tool_calls 情况
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
                    render_background_notification("Agent exceeded maximum retry limits", title="⚠️ Agent Error")
                    return

                max_tokens = DEFAULT_MAX_TOKENS
                state.has_escalated = False

                if not tool_calls:
                    SESSION_MANAGER.add_message(assistant_message)
                    return
                else:
                    assistant_message["tool_calls"] = tool_calls
                    SESSION_MANAGER.add_message(assistant_message)
                    await self.call_tools(tool_calls, handlers, ctx)
        except AgentInterrupted:
            raise
        except asyncio.CancelledError:
            ctx.raise_if_cancelled()
            raise
        finally:
            # 不等待收尾 interrupt() 的 ctx.cancel() 已取消过一轮，这里只兜底取消中断后新登记的任务
            pending = [t for t in ctx.tasks if not t.done()]
            if ctx.interrupted:
                for t in pending:
                    t.cancel()
                _LOGGER.warning(f"[Interrupt] {len(pending)} pending task(s) without waiting")
            self._current_ctx = None
            self._run_task = None

    async def call_llm(self, messages, tools, max_tokens, ctx):
        system = build_system_prompt("main", tools)
        messages = [{"role": "system", "content": system}] + messages
        _LOGGER.debug(f"Session manager loaded {len(messages)} messages")

        with render_thinking_status():
            return await with_retry_async(
                lambda: shared_model_client().get_model_client(async_client=True)(
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    stream=True
                )
            )

    async def stream(self, stream: AsyncStream[ChatCompletionChunk], ctx: AgentRunContext):
        accumulated_text = ""
        reasoning_text = ""
        tool_calls: list[dict] = []
        finish_reason = ""
        usage = None
        with render_thinking_status():
            with render_scope():
                try:
                    async for chunk in stream:
                        ctx.raise_if_cancelled()
                        if not chunk.choices:
                            if chunk.usage:
                                usage = chunk.usage
                            continue

                        choice = chunk.choices[0]
                        if hasattr(choice.delta, "reasoning_content") and choice.delta.reasoning_content:
                            reasoning_text += choice.delta.reasoning_content
                        if choice.delta.content:
                            accumulated_text += choice.delta.content
                            stream_assistant_response(choice.delta.content)

                        if choice.delta.tool_calls:
                            for delta in choice.delta.tool_calls:
                                while len(tool_calls) <= delta.index:
                                    tool_calls.append({
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""}
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
                finally:
                    await stream.close()

        return accumulated_text, reasoning_text, tool_calls, finish_reason, usage

    async def call_tools(self, tool_calls: list[dict], handlers: dict, ctx: AgentRunContext):
        tool_call_results = []
        try:
            # todo: 采用 asyncio.TaskGroup 并发执行工具，当前工具执行实质为串行执行
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id", "")
                if ctx.interrupted:
                    tool_call_results.append(_get_interrupt_message(tool_call_id))
                    continue

                ctx.raise_if_cancelled()

                try:
                    tool_call = ChatCompletionMessageToolCall(**tool_call)
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    tool_call_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"[Error] {type(e).__name__}: {e}"
                    })
                    continue

                render_tool_call(tool_name, tool_args)

                blocked = trigger_hooks("PreToolUse", tool_call)
                if blocked:
                    tool_call_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"[{str(blocked)}]"
                    })
                    continue

                if should_run_background(tool_name, tool_args):
                    # todo: 后台工具异步重构?
                    bg_id = start_background_task(tool_call, handlers, ctx)
                    tool_call_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"[Background task `{bg_id}` started] "
                                   f"Result will arrive with a `<background-task-notification>` tag."
                    })
                    continue

                handler = handlers.get(tool_name)
                diff = ""
                with render_tool_calling_status(f"{tool_name} ({tool_args})"):
                    if tool_name in DIFF_TOOLS:
                        try:
                            diff = preview_write(tool_args["path"], tool_args["content"]) if tool_name == "write_file" \
                                else preview_edit(tool_args["path"], tool_args["old_text"], tool_args["new_text"])
                        except Exception as e:
                            _LOGGER.exception(f"[Diff exception] {e}")

                    task = asyncio.create_task(execute_tool(handler, tool_args, tool_name, ctx))
                    ctx.track(task)

                    try:
                        result = await task
                    except AgentInterrupted:
                        tool_call_results.append(_get_interrupt_message(tool_call_id))
                        continue
                    except asyncio.CancelledError:
                        if not ctx.interrupted:
                            raise
                        task.cancel()
                        tool_call_results.append(_get_interrupt_message(tool_call_id))
                        continue

                    tool_failed = str(result).startswith(TOOL_ERROR_PREFIXES)
                    if diff and not tool_failed:
                        render_tool_result_diff(diff)

                render_tool_result(result)

                tool_call_results.append({
                                             "role": "tool",
                                             "tool_call_id": tool_call_id,
                                             "content": str(result)
                                         } | ({"payload": diff} if diff and not tool_failed else {}))

        finally:
            # 处理未捕获异常的 tool call result 缺漏
            answered = {r["tool_call_id"] for r in tool_call_results}
            for tc in tool_calls:
                if tc.get("id", "") not in answered:
                    tool_call_results.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "[Error] tool call aborted before execution"
                    })
            for tool_result in tool_call_results:
                SESSION_MANAGER.add_message(tool_result)

        ctx.raise_if_cancelled()


async def prepare_messages(messages: list, ctx) -> list:
    """
    Every LLM turn enters through the same context budget pipeline.
    """
    messages[:] = tool_result_budget(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > CONTEXT_LIMIT:
        messages[:] = await compact_history(messages, ctx)

    return messages


def inject_background_notifications():
    notes = collect_background_results()
    if notes:
        SESSION_MANAGER.add_message({
            "role": "user",
            "content": INJECTION_MESSAGES_PREFIX + "\n".join(notes) + INJECTION_MESSAGES_SUFFIX,
        })


def cron_auto_loop(agent: AgentRuntime):
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue

        with AGENT_LOCK:
            for job in fired:
                SESSION_MANAGER.add_message({
                    "role": "user",
                    "content": INJECTION_MESSAGES_TEMPLATE.format(content=f"[Scheduled Cron]\n{job.prompt}")
                })
                render_background_notification(f"Cron Auto Prompt: {job.prompt}", title="⏰ Cron Triggered")

            try:
                agent.submit(agent.run())  # submit 内部已阻塞至回合结束
            except Exception as e:
                _LOGGER.warning(f"[Cron] run skipped: {type(e).__name__}: {e}")


def absorb_lead_inbox() -> None:
    """把 lead inbox 消息作为一条用户消息注入会话（agent 回合结束后调用）。"""
    inbox = consume_lead_inbox(route_protocol=True)
    if not inbox:
        return

    def _label(message: dict) -> str:
        request_id = message.get("metadata", {}).get("request_id", "")
        return f"{message.get('msg_type', 'message')}" + (f"  request_id: {request_id}" if request_id else "")

    inbox_text = "\n".join(
        f"From {message['from_agent']} [{_label(message)}]: {message['content']}"
        for message in inbox
    )
    SESSION_MANAGER.add_message({
        "role": "user",
        "content": INJECTION_MESSAGES_TEMPLATE.format(content=f"[Inbox messages]\n{inbox_text}")
    })


def start_agent_runtime() -> AgentRuntime:
    """建 runtime 并起 MCP 预热 / cron 自动回合线程（各一次）；调用方拿它做中断收口。"""
    runtime = AgentRuntime()

    def _warmup() -> None:
        from core.mcp.mcp_client import warmup
        warmup()

    # 后台线程预热 MCP 连接（阻塞至就绪或失败），避免首个 agent 轮次被慢建连卡住
    threading.Thread(target=_warmup, name="mcp-warmup", daemon=True).start()
    threading.Thread(target=cron_auto_loop, args=(runtime,), name="cron-auto", daemon=True).start()
    return runtime


def make_agent_turn(runtime: AgentRuntime) -> Callable[[str], None]:
    """构造 UI 每轮用户消息的处理函数：与 cron 串行跑完整个 agent 回合（阻塞），再吸收 lead inbox。

    由 Textual UI 在后台线程调用；query 已由 UI 写入会话管理器，此处不使用。
    阻塞是契约：UI 的 _turn_worker 靠它判断忙碌态，并把逃逸异常上屏。
    runtime.interrupt() 可在回合中途取消（UI 的 Esc，权限确认优先，见 ui_textual）。
    """

    def agent_turn(_query: str) -> None:
        with AGENT_LOCK:
            try:
                runtime.submit(runtime.run())  # submit 内部已阻塞至回合结束
            except AgentInterrupted:
                pass  # 用户中断，不是错误
            finally:
                absorb_lead_inbox()

    return agent_turn
