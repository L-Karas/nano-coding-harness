"""
Sub Agent

跑在调用方的 event loop（AgentRuntime.loop）上：模型客户端绑定该 loop，子代理不另起线程/loop。
后台语义（主代理不等结论、结果稍后注入）由 background_task 的占位符 + notification 提供，
这里只负责跑完并把最终结论返回。
"""
from core.client import shared_sub_model_client
from core.config import CONFIGMANAGER
from core.context.compact import prepare_messages
from core.context.prompt import build_system_prompt
from core.log.log import get_logger
from core.runtime_context import ToolContext
from core.runtime_state import RUNTIME_STATE

MAX_SUB_AGENT_ROUNDS = 30
_LOGGER = get_logger(__name__)


def _set_phase(agent_id: str, phase: str, detail: str = "") -> None:
    """右栏数据源：阶段与正在跑的工具名（"thinking" / "tool"）。"""
    RUNTIME_STATE.update_subagent(agent_id, phase, detail)


async def spawn_subagent(description: str, ctx=None) -> str:
    """在调用方 loop 上跑完一个子代理，返回它的最终文本结论。

    ctx 为父回合的 AgentRunContext：每轮模型调用前协作式检查取消（Esc），
    透传给 step 的工具执行使中断能到达子进程；每轮调用前走与主代理相同的
    上下文预算管线（prepare_messages，作用于不含 system 的 history）。
    """
    from core.agent_step import StepPolicy, StepRenderer, run_agent_step
    from core.tools import assemble_tool_pool

    agent_id = RUNTIME_STATE.allocate_subagent_id()
    RUNTIME_STATE.register_subagent(agent_id, description)

    try:
        pool = assemble_tool_pool("sub-agent")
        system_prompt = build_system_prompt("sub-agent", tools=pool.schemas())
        history = [{"role": "user", "content": description}]
        tctx = ToolContext(agent_run=ctx, agent_name="sub-agent")
        policy = StepPolicy(use_extensions=True, permission_hooks=True)
        renderer = StepRenderer(on_tool_call=lambda tool_name, _args: _set_phase(agent_id, "tool", tool_name))

        for _ in range(MAX_SUB_AGENT_ROUNDS):
            if ctx:
                ctx.raise_if_cancelled()

            _set_phase(agent_id, "thinking")
            history = await prepare_messages(history, ctx, sub_model=True)
            outcome = await run_agent_step(
                history,
                system_prompt,
                client=shared_sub_model_client(),
                pool=pool,
                tctx=tctx,
                max_tokens=CONFIGMANAGER.config.default_max_tokens,
                policy=policy,
                renderer=renderer,
            )
            if outcome.aborted:
                return f"(subagent aborted by extension: {outcome.abort_reason})"

            history.append(outcome.assistant_message)
            history.extend(outcome.followup_messages)
            if not outcome.tool_calls:
                return outcome.assistant_message.get("content") or "(subagent finished without a text conclusion)"

        _LOGGER.warning(f"[Subagent] reached {MAX_SUB_AGENT_ROUNDS} rounds without a conclusion")
        return f"(subagent reached the {MAX_SUB_AGENT_ROUNDS}-round limit without a conclusion)"
    finally:
        RUNTIME_STATE.remove_subagent(agent_id)
