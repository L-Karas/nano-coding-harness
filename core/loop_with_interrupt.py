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
from core.bootstrap import bootstrap
from core.client import shared_model_client
from core.config import CONFIGMANAGER
from core.context import to_llm_messages
from core.context.compact import compact_history, prepare_messages
from core.context.prompt import build_system_prompt
from core.context.session import SESSION_MANAGER
from core.cron_scheduler import consume_cron_queue
from core.experimental.protocol_state import consume_lead_inbox
from core.extension import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext, dispatch,
)
from core.hook.hook import trigger_hooks
from core.log.log import get_logger
from core.recovery.error_recovery import RecoveryState, with_retry_async
from core.runtime_context import AgentRunContext, AgentInterrupted
from core.streaming import streaming_message
from core.template import (
    CONTINUATION_PROMPT,
    INJECTION_MESSAGES_PREFIX,
    INJECTION_MESSAGES_SUFFIX,
    INJECTION_MESSAGES_TEMPLATE,
    TOOL_ERROR_PREFIX,
    UNKNOWN_TOOL_PREFIX,
    USER_INTERRUPT_PROMPT,
)
from core.tools import assemble_tool_pool
from core.tools.base_tools.diff import DIFF_TOOLS, preview_edit, preview_write
from core.tools.tool_loader import execute_tool
from core.tui.render import render_scope, stream_assistant_response, render_tool_call, render_tool_result, \
    render_tool_result_diff, render_background_notification, render_thinking_status, render_working_status

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

    def is_running(self) -> bool:
        """是否有回合（run / compact，含 auto_loop 的 cron / 后台自动回合）已登记且未结束；
        UI 的 Esc 据此判断可否中断：auto_loop 直接跑 runtime，不经过 UI 的 _busy。"""
        task = self._run_task
        return task is not None and not task.done()

    def submit(self, coro) -> Future:
        """跑协程并阻塞取结果；被 interrupt() 取消（concurrent.futures.CancelledError）时统一抛
        AgentInterrupted，取消语义单一（Esc 的 ctx/task 双路取消都落到同一个异常）。"""
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop=self.loop).result()
        except concurrent.futures.CancelledError:
            raise AgentInterrupted("User interrupted") from None

    async def compact(self) -> bool:
        """手动压缩当前会话上下文（UI 的 /compact）：须经 submit() 在 self.loop 上执行——
        模型客户端绑定该事件循环。AGENT_LOCK 由调用线程（UI 的 _compact_worker）持有：
        在协程内取锁会阻塞事件循环，与持锁等待该循环的 cron 线程死等。
        注册 _current_ctx / _run_task，压缩期间 Esc（runtime.interrupt）可中断；中断不落会话，取消归一与 run() 一致。
        返回是否实际压缩；未触发（低于阈值 / 未选模型）时不回写会话。"""
        ctx = AgentRunContext()
        self._current_ctx = ctx
        self._run_task = asyncio.current_task()
        try:
            messages, compacted = await compact_history(SESSION_MANAGER.load_messages(), ctx=ctx, auto_compact=False)
            if compacted:
                SESSION_MANAGER.update_messages(messages)
            return compacted
        except asyncio.CancelledError:
            ctx.raise_if_cancelled()
            raise
        finally:
            self._current_ctx = None
            self._run_task = None

    async def run(self):
        if self._run_task is not None:
            raise RuntimeError("Agent is already running")

        state = RecoveryState()
        ctx = AgentRunContext()
        max_tokens = CONFIGMANAGER.config.default_max_tokens
        self._current_ctx = ctx
        self._run_task = asyncio.current_task()

        try:
            while True:
                ctx.raise_if_cancelled()

                fired_crons = consume_cron_queue()
                for cron in fired_crons:
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

                llm_ctx = BeforeLLMContext(messages=messages, tools=tools, max_tokens=max_tokens)
                await dispatch("before_llm", llm_ctx)
                if llm_ctx.aborted:
                    abort_text = f"[Extension aborted] {llm_ctx.abort_reason}"
                    SESSION_MANAGER.add_message({"role": "assistant", "content": abort_text})
                    render_background_notification(abort_text, title="⛔ Extension Abort")
                    return

                try:
                    stream = await self.call_llm(messages=llm_ctx.messages, tools=llm_ctx.tools,
                                                 max_tokens=llm_ctx.max_tokens, ctx=ctx)
                except Exception as e:
                    # todo: 是否需要将模型调用错误信息作为消息历史的一部分
                    error_text = f"[Error] {type(e).__name__}: {e}"
                    SESSION_MANAGER.add_message({
                        "role": "assistant",
                        "content": error_text
                    })
                    render_background_notification(error_text, title="⚠️ Agent Error")
                    return

                accumulated_text, reasoning_text, tool_calls, finish_reason, usage = await self.stream(stream)

                assistant_message = {
                    "role": "assistant",
                    "content": accumulated_text,
                    "reasoning_content": reasoning_text,
                    "usage": usage
                }
                if finish_reason == "length":
                    # todo: 半截 tool_calls 情况
                    if not state.has_escalated:
                        max_tokens = CONFIGMANAGER.config.escalated_max_tokens
                        state.has_escalated = True
                        _LOGGER.info(f"[Max tokens] retry with {max_tokens}")
                        continue

                    SESSION_MANAGER.add_message(assistant_message)

                    if state.recovery_count < CONFIGMANAGER.config.max_recovery_retries:
                        SESSION_MANAGER.add_message({
                            "role": "user",
                            "content": CONTINUATION_PROMPT
                        })
                        state.recovery_count += 1
                        continue
                    render_background_notification("Agent exceeded maximum retry limits", title="⚠️ Agent Error")
                    return

                max_tokens = CONFIGMANAGER.config.default_max_tokens
                state.has_escalated = False

                llm_ctx = AfterLLMContext(content=accumulated_text, tool_calls=tool_calls or [],
                                          finish_reason=finish_reason or "", usage=usage)
                await dispatch("after_llm", llm_ctx)
                assistant_message["content"] = llm_ctx.content
                tool_calls = llm_ctx.tool_calls
                injected = llm_ctx.inject_messages

                if not tool_calls:
                    SESSION_MANAGER.add_message(assistant_message)
                    for message in injected:
                        SESSION_MANAGER.add_message(message)
                    return
                else:
                    assistant_message["tool_calls"] = tool_calls
                    SESSION_MANAGER.add_message(assistant_message)
                    for message in injected:
                        SESSION_MANAGER.add_message(message)
                    await self.call_tools(tool_calls, handlers, ctx)
        except AgentInterrupted:
            raise
        except asyncio.CancelledError:
            ctx.raise_if_cancelled()
            raise
        finally:
            # 不等待收尾 interrupt() 的 ctx.cancel() 已取消过一轮，这里只兜底取消中断后新登记的任务
            pending = [t for t in list(ctx.tasks) if not t.done()]  # 后台 Future 的 done 回调在 worker 线程跑
            if ctx.interrupted:
                for t in pending:
                    t.cancel()
                _LOGGER.warning(f"[Interrupt] {len(pending)} pending task(s) without waiting")
            self._current_ctx = None
            self._run_task = None

    async def call_llm(self, messages, tools, max_tokens, ctx):
        system = build_system_prompt("main", tools)
        messages = [{"role": "system", "content": system}] + to_llm_messages(messages)
        _LOGGER.debug(f"Session manager loaded {len(messages)} messages")

        client = shared_model_client()
        with render_working_status():
            return await with_retry_async(
                lambda: client.get_model_client(async_client=True)(
                    messages=messages,
                    tools=tools,
                    max_tokens=client.clamp_max_tokens(max_tokens),
                    stream=True
                )
            )

    async def stream(self, stream: AsyncStream[ChatCompletionChunk]):
        with render_thinking_status(), render_scope():
            return await streaming_message(stream, on_text=stream_assistant_response)

    async def call_tools(self, tool_calls: list[dict], handlers: dict, ctx: AgentRunContext):
        tool_call_results = []
        injected_messages: list[dict] = []
        try:
            # todo: 采用 asyncio.gather 并发执行工具，当前工具执行实质为串行执行
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id", "")
                if ctx.interrupted:
                    tool_call_results.append(_get_interrupt_message(tool_call_id))
                    continue

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

                tool_ctx = BeforeToolContext(tool_name=tool_name, args=tool_args)
                await dispatch("before_tool", tool_ctx)
                tool_args = tool_ctx.args
                tool_call.function.arguments = json.dumps(tool_args, ensure_ascii=False)
                render_tool_call(tool_name, tool_args)
                if tool_ctx.blocked:
                    tool_call_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"[Extension blocked] {tool_ctx.block_reason}"
                    })
                    continue

                blocked = trigger_hooks("pre_tool_call", tool_call)
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
                with render_working_status():
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
                    except asyncio.CancelledError:
                        if not ctx.interrupted:
                            raise
                        task.cancel()
                        tool_call_results.append(_get_interrupt_message(tool_call_id))
                        continue

                    tool_failed = str(result).startswith((TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX))
                    if diff and not tool_failed:
                        render_tool_result_diff(diff)

                result_ctx = AfterToolContext(tool_name=tool_name, args=tool_args,
                                              result=str(result), is_error=tool_failed)
                await dispatch("after_tool", result_ctx)

                render_tool_result(result_ctx.result)
                injected_messages.extend(result_ctx.inject_messages)

                tool_call_results.append({
                                             "role": "tool",
                                             "tool_call_id": tool_call_id,
                                             "content": result_ctx.result
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
            for message in injected_messages:
                SESSION_MANAGER.add_message(message)

        ctx.raise_if_cancelled()


def inject_background_notifications() -> list[str]:
    """把已完成的后台任务结果写成注入消息（UI 回放会跳过它，只喂模型）；返回注入了哪些通知。"""
    notes = collect_background_results()
    if notes:
        SESSION_MANAGER.add_message({
            "role": "user",
            "content": INJECTION_MESSAGES_PREFIX + "\n".join(notes) + INJECTION_MESSAGES_SUFFIX,
        })
    return notes


def auto_loop(agent: AgentRuntime):
    """每秒轮询：定时任务到期 / 后台任务完成 → 注入消息并自动开一轮。

    与用户回合共用 AGENT_LOCK 串行；等锁期间运行中的回合可能已自行消费（run() 循环顶部
    会消费 cron 队列并注入后台结果），故拿到锁后再取一次，避免重复注入 / 空跑。"""
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        with AGENT_LOCK:
            for job in fired:
                SESSION_MANAGER.add_message({
                    "role": "user",
                    "content": INJECTION_MESSAGES_TEMPLATE.format(content=f"[Scheduled Cron]\n{job.prompt}")
                })
                render_background_notification(f"Cron Auto Prompt: {job.prompt}", title="⏰ Cron Triggered")

            notes = inject_background_notifications()
            if not fired and not notes:  # 等锁期间已被运行中的回合消费
                continue
            for note in notes:
                render_background_notification(note, title="🔔 Background Task")

            try:
                agent.submit(agent.run())  # submit 内部已阻塞至回合结束
            except AgentInterrupted:
                pass  # 自动回合被 Esc 取消，不应该把线程带走
            except Exception as e:
                _LOGGER.warning(f"[Auto Loop] run skipped: {type(e).__name__}: {e}")


# todo: teammates experimental
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
    """先 bootstrap 初始化，再建 runtime 并起 MCP 预热 / 自动回合轮询线程（各一次）；调用方拿它做中断收口。"""
    bootstrap()
    runtime = AgentRuntime()

    def _warmup() -> None:
        from core.mcp.mcp_client import warmup
        warmup()

    # 后台线程预热 MCP 连接（阻塞至就绪或失败），避免首个 agent 轮次被慢建连卡住
    threading.Thread(target=_warmup, name="mcp-warmup", daemon=True).start()
    threading.Thread(target=auto_loop, args=(runtime,), name="auto-loop", daemon=True).start()
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
