"""
Agent Loop
"""
import asyncio
import threading
import time
from typing import Callable

from core import interaction
from core.agent_step import StepPolicy, StepRenderer, run_agent_step
from core.background_task import collect_background_results
from core.bootstrap import bootstrap
from core.client import shared_model_client
from core.config import CONFIGMANAGER
from core.context.compact import compact_history, prepare_messages
from core.context.prompt import build_system_prompt
from core.context.session import SESSION_MANAGER
from core.cron_scheduler import consume_cron_queue
from core.experimental.protocol_state import consume_lead_inbox
from core.log.log import get_logger
from core.recovery.error_recovery import RecoveryState
from core.runtime_context import AgentRunContext, AgentInterrupted, ToolContext
from core.template import (
    CONTINUATION_PROMPT,
    INJECTION_MESSAGES_PREFIX,
    INJECTION_MESSAGES_SUFFIX,
    INJECTION_MESSAGES_TEMPLATE,
)
from core.tools import assemble_tool_pool
from core.turn_runtime import TurnRunner

_LOGGER = get_logger(__name__)

MAIN_STEP_POLICY = StepPolicy(use_extensions=True, permission_hooks=True, background=True,
                              diff_preview=True, defer_tools_on_length=True)


def _main_renderer() -> StepRenderer:
    """从 UI port 组装 step 的渲染槽；headless 时端口默认 no-op（ADR-0005）。"""
    return StepRenderer(
        on_text=interaction.stream_assistant_response,
        on_tool_call=interaction.render_tool_call,
        on_tool_result=interaction.render_tool_result,
        on_diff=interaction.render_tool_result_diff,
        thinking_status=interaction.render_thinking_status,
        working_status=interaction.render_working_status,
        scope=interaction.render_scope,
    )


class AgentRuntime:

    def __init__(self):
        self._turns = TurnRunner()

    @property
    def current_context(self) -> AgentRunContext | None:
        """当前回合的上下文（无回合时 None）；只读，供测试 / UI 观察回合生命周期。"""
        return self._turns.current_context

    def interrupt(self) -> bool:
        return self._turns.interrupt()

    def is_running(self) -> bool:
        """是否有回合（run / compact，含 auto_loop 的 cron / 后台自动回合）已登记且未结束；
        UI 的 Esc 据此判断可否中断：auto_loop 直接跑 runtime，不经过 UI 的 _busy。"""
        return self._turns.is_running()

    def run_turn(self, prepare: Callable[[], bool] | None = None) -> bool:
        """跑一次完整 agent 回合（阻塞至结束）；prepare 在锁内、提交前执行，返回 False 则跳过。"""
        return self._turns.run(self.run, prepare=prepare)

    def run_compact(self) -> bool:
        """手动压缩当前会话（阻塞至结束，与 agent 回合串行）。"""
        return self._turns.run(self.compact)

    async def compact(self, ctx: AgentRunContext) -> bool:
        """压缩当前会话上下文（UI 的 /compact）：ctx 由 TurnRunner 提供，压缩期间 Esc 可中断；
        中断不落会话。返回是否实际压缩；未触发（低于阈值 / 未选模型）时不回写会话。"""
        try:
            messages, compacted = await compact_history(SESSION_MANAGER.load_messages(), ctx=ctx, auto_compact=False)
            if compacted:
                SESSION_MANAGER.update_messages(messages)
            return compacted
        except asyncio.CancelledError:
            ctx.raise_if_cancelled()
            raise

    async def run(self, ctx: AgentRunContext):
        state = RecoveryState()
        max_tokens = CONFIGMANAGER.config.default_max_tokens

        try:
            while True:
                ctx.raise_if_cancelled()

                fired_crons = consume_cron_queue()
                for cron in fired_crons:
                    SESSION_MANAGER.add_message({
                        "role": "user",
                        "content": INJECTION_MESSAGES_TEMPLATE.format(content=f"[Scheduled cron] {cron.prompt}")
                    })
                    interaction.render_background_notification(f"Cron prompt: {cron.prompt}", title="⏰ Cron Injected")

                ctx.raise_if_cancelled()
                inject_background_notifications()

                messages = await prepare_messages(SESSION_MANAGER.load_messages(), ctx)
                SESSION_MANAGER.update_messages(messages)
                pool = assemble_tool_pool("main")
                tctx = ToolContext(agent_run=ctx)

                try:
                    outcome = await run_agent_step(
                        messages,
                        build_system_prompt("main", pool.schemas()),
                        client=shared_model_client(),
                        pool=pool,
                        tctx=tctx,
                        max_tokens=max_tokens,
                        policy=MAIN_STEP_POLICY,
                        renderer=_main_renderer(),
                    )
                except Exception as e:
                    # todo: 是否需要将模型调用错误信息作为消息历史的一部分
                    error_text = f"[Error] {type(e).__name__}: {e}"
                    SESSION_MANAGER.add_message({
                        "role": "assistant",
                        "content": error_text
                    })
                    interaction.render_background_notification(error_text, title="⚠️ Agent Error")
                    return

                if outcome.aborted:
                    abort_text = f"[Extension aborted] {outcome.abort_reason}"
                    SESSION_MANAGER.add_message({"role": "assistant", "content": abort_text})
                    interaction.render_background_notification(abort_text, title="⛔ Extension Abort")
                    return

                if outcome.finish_reason == "length":
                    # todo: 半截 tool_calls 情况
                    if not state.has_escalated:
                        max_tokens = CONFIGMANAGER.config.escalated_max_tokens
                        state.has_escalated = True
                        _LOGGER.info(f"[Max tokens] retry with {max_tokens}")
                        continue

                    SESSION_MANAGER.add_message(outcome.assistant_message)
                    for message in outcome.followup_messages:
                        SESSION_MANAGER.add_message(message)

                    if state.recovery_count < CONFIGMANAGER.config.max_recovery_retries:
                        SESSION_MANAGER.add_message({
                            "role": "user",
                            "content": CONTINUATION_PROMPT
                        })
                        state.recovery_count += 1
                        continue
                    interaction.render_background_notification("Agent exceeded maximum retry limits", title="⚠️ Agent Error")
                    return

                max_tokens = CONFIGMANAGER.config.default_max_tokens
                state.has_escalated = False

                SESSION_MANAGER.add_message(outcome.assistant_message)
                for message in outcome.followup_messages:
                    SESSION_MANAGER.add_message(message)
                if not outcome.tool_calls:
                    return
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

    prepare 在 TurnRunner 的锁内执行：等锁期间运行中的回合可能已自行消费（run() 循环顶部
    会消费 cron 队列并注入后台结果），故拿到锁后再取一次，避免重复注入 / 空跑。"""
    while True:
        time.sleep(1)

        def _prepare() -> bool:
            fired = consume_cron_queue()
            for job in fired:
                SESSION_MANAGER.add_message({
                    "role": "user",
                    "content": INJECTION_MESSAGES_TEMPLATE.format(content=f"[Scheduled Cron]\n{job.prompt}")
                })
                interaction.render_background_notification(f"Cron Auto Prompt: {job.prompt}", title="⏰ Cron Triggered")

            notes = inject_background_notifications()
            if not fired and not notes:  # 等锁期间已被运行中的回合消费
                return False
            for note in notes:
                interaction.render_background_notification(note, title="🔔 Background Task")
            return True

        try:
            agent.run_turn(prepare=_prepare)
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
        try:
            runtime.run_turn()  # 阻塞至回合结束（TurnRunner 内部取锁串行）
        except AgentInterrupted:
            pass  # 用户中断，不是错误
        finally:
            absorb_lead_inbox()

    return agent_turn
