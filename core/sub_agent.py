"""
Sub Agent

跑在调用方的 event loop（AgentRuntime.loop）上：模型客户端绑定该 loop，子代理不另起线程/loop。
后台语义（主代理不等结论、结果稍后注入）由 background_task 的占位符 + notification 提供，
这里只负责跑完并把最终结论返回。

取消：sa 条目在 RuntimeState 上挂线程安全句柄（ADR-0009），右栏可指名停它，
与父回合的 Esc 解耦；任务被 cancel 时 finally 清理条目与句柄。
"""
import asyncio
from pathlib import Path
from typing import Any

from core.client import ModelChoice, model_client_for, model_supports_vision, shared_sub_model_client
from core.config import CONFIGMANAGER, WORKDIR
from core.context.compact import prepare_messages
from core.context.prompt import build_system_prompt
from core.runtime_context import AgentRunContext, ToolContext
from core.runtime_state import DEFAULT_SUBAGENT_ROLE, RUNTIME_STATE
from core.shared.logger import get_logger

MAX_SUB_AGENT_ROUNDS = 500
_LOGGER = get_logger(__name__)


def _set_phase(agent_id: str, phase: str, detail: str = "") -> None:
    """右栏数据源：阶段与正在跑的工具名（"thinking" / "tool"）。

    Args:
        agent_id: 子代理 id。
        phase: 当前阶段。
        detail: 阶段详情，通常是工具名。
    """
    RUNTIME_STATE.update_subagent(agent_id, phase, detail)


def _thinking_display(client: Any) -> str:
    """子代理展示 / 日志用的思考状态标签。

    模型关闭思考（``thinking: false``）时显示 ``off``；思考开启但档位被模型降级时
    ``current_thinking_level`` 为空，此时显示 ``enabled (no effort)``。

    Args:
        client: 本次子代理使用的模型客户端。

    Returns:
        档位名、``enabled (no effort)`` 或 ``off``。
    """
    if not getattr(client, "current_model_thinking", False):
        return "off"
    level = getattr(client, "current_thinking_level", "")
    return level or "enabled (no effort)"


async def spawn_subagent(description: str, ctx: AgentRunContext | None = None, goal: str = "",
                         working_directory: Path | None = None,
                         role: str = DEFAULT_SUBAGENT_ROLE,
                         model_choice: ModelChoice | None = None) -> str:
    """在调用方 loop 上跑完一个子代理，返回它的最终文本结论。

    role 为右栏展示的角色标签（如 reviewer / researcher），默认值仅供直接调用方兜底；
    goal 为右栏展开时展示的简短目标；working_directory 为子代理工作目录（缺省与主代理一致的 WORKDIR）。
    model_choice 给出时按该模型配置取 client（与其它并发子代理互不覆盖）；缺省仍用 settings 的
    default_sub_model。ctx 为父回合的 AgentRunContext：每轮模型调用前协作式检查取消（Esc），
    透传给 step 的工具执行使中断能到达子进程；每轮调用前走与主代理相同的
    上下文预算管线（prepare_messages，作用于不含 system 的 history）。

    Args:
        description: 子代理任务描述。
        ctx: 父回合上下文；提供中断检查。
        goal: 右栏展开时展示的简短目标。
        working_directory: 子代理工作目录；省略时使用 WORKDIR。
        role: 右栏展示的角色标签。
        model_choice: 显式模型与思考档位；省略时使用 settings 默认子模型。

    Returns:
        子代理的最终文本结论；达到轮数上限时返回说明文本。

    Raises:
        AgentInterrupted: 父回合被用户中断。
    """
    from core.agent_step import StepPolicy, StepRenderer, run_agent_step
    from core.tools import assemble_tool_pool

    client = model_client_for(model_choice) if model_choice is not None else shared_sub_model_client()
    provider = getattr(client, "current_provider", "")
    model_name = getattr(client, "current_model", "")
    model_label = f"{provider}:{model_name}" if provider and model_name else "(unset)"
    thinking_level = _thinking_display(client)

    agent_id = RUNTIME_STATE.allocate_subagent_id()
    RUNTIME_STATE.register_subagent(agent_id, description, goal, role=role,
                                    model=model_label, thinking_level=thinking_level)
    _LOGGER.info(f"[Subagent] {agent_id} model: {model_label} @ {thinking_level}")

    # 指名取消句柄：子代理就跑在当前 task 上（局部导入避开 tools ↔ sub_agent 的 import 环）
    from core.background_task import threadsafe_cancel
    task = asyncio.current_task()
    if task is not None:
        RUNTIME_STATE.attach_cancel(agent_id, threadsafe_cancel(task))

    work_dir = working_directory or WORKDIR
    try:
        pool = assemble_tool_pool("sub-agent",
                                  enable_experimental=CONFIGMANAGER.config.enable_experimental_tools)
        system_prompt = build_system_prompt("sub-agent", tools=pool.schemas(), work_directory=work_dir)
        history = [{"role": "user", "content": description}]
        tctx = ToolContext(agent_run=ctx, cwd=work_dir, agent_name="sub-agent",
                           vision=model_supports_vision(client))
        policy = StepPolicy(use_extensions=True)
        renderer = StepRenderer(
            on_tool_call=lambda _tool_call_id, tool_name, _args: _set_phase(agent_id, "tool", tool_name))

        for _ in range(MAX_SUB_AGENT_ROUNDS):
            if ctx:
                ctx.raise_if_cancelled()

            _set_phase(agent_id, "thinking")
            history = await prepare_messages(history, ctx, client=client)
            outcome = await run_agent_step(
                history,
                system_prompt,
                client=client,
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
