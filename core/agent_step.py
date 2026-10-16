"""Agent step：一次模型调用及其触发的工具派发（main / sub-agent 共享）。

step 不改调用方的 messages，不碰 session / cron / 阶段标记 / 协议门控；返回 StepOutcome 由外层决定
append、落盘与重试。差异经 StepPolicy 与 StepRenderer 显式化（见 docs/adr/0003-agent-step.md）。
"""
import asyncio
import json
from contextlib import nullcontext
from dataclasses import dataclass, field
from itertools import groupby
from typing import Callable, ContextManager

from openai.types.chat import ChatCompletionMessageToolCall

from core.background_task import should_run_background, start_background_task
from core.context import to_llm_messages
from core.diff import DIFF_TOOLS, preview_edit, preview_write
from core.extension import (
    AfterLLMContext,
    AfterToolContext,
    BeforeLLMContext,
    BeforeToolContext,
    ToolExecuteContext,
    dispatch,
)
from core.log.log import get_logger
from core.recovery.error_recovery import with_retry_async
from core.runtime_context import ToolContext
from core.streaming import streaming_message
from core.template import USER_INTERRUPT_PROMPT
from core.tools import ToolPool, ToolResult

_LOGGER = get_logger(__name__)


def _noop(*_args, **_kwargs) -> None:
    pass


def _null_cm() -> ContextManager:
    return nullcontext()


def _tool_message(tool_call_id: str, content: str) -> dict:
    """tool 角色消息：每个 tool_call_id 必须有且仅有一条结果消息。"""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


@dataclass(frozen=True)
class StepPolicy:
    """step 的行为差异：扩展四节点 / 后台路由 / diff 预览。

    use_extensions=False 只跳过外部回调（所有事件），内置观察者与权限门照常。
    defer_tools_on_length：finish_reason == "length" 时先不执行工具，把恢复决定留给外层（主循环的
    扩窗与续写重试必须在工具执行前发生）。
    """
    use_extensions: bool = True
    background: bool = False
    diff_preview: bool = False
    defer_tools_on_length: bool = False


@dataclass(frozen=True)
class StepRenderer:
    """step 的渲染槽：默认全 no-op；主循环从 TUI render 组装（候选 5 会把它挪到 interaction port 后面）。"""
    on_text: Callable[[str], None] | None = None
    on_tool_call: Callable[[str, dict], None] = _noop
    on_tool_result: Callable[[str, bool], None] = _noop
    on_diff: Callable[[list], None] = _noop
    thinking_status: Callable[[], ContextManager] = _null_cm
    working_status: Callable[[], ContextManager] = _null_cm
    scope: Callable[[], ContextManager] = _null_cm


@dataclass
class StepOutcome:
    assistant_message: dict
    followup_messages: list[dict]
    tool_calls: list[dict]
    finish_reason: str
    usage: dict
    aborted: bool = False
    abort_reason: str = ""


async def run_agent_step(
        history: list[dict],
        system_prompt: str,
        *,
        client,
        pool: ToolPool,
        tctx: ToolContext,
        max_tokens: int,
        policy: StepPolicy = StepPolicy(),
        renderer: StepRenderer = StepRenderer(),
) -> StepOutcome:
    """一次模型调用（扩展 → 重试请求 → 流式解析 → 扩展）与其工具派发（扩展（含权限门）→ 执行 → 扩展）。"""
    tools = pool.schemas()
    messages = [{"role": "system", "content": system_prompt}] + to_llm_messages(history)

    llm_ctx = BeforeLLMContext(messages=messages, tools=tools, max_tokens=max_tokens)
    await dispatch("before_llm", llm_ctx, include_external=policy.use_extensions)
    if llm_ctx.aborted:
        return StepOutcome(
            assistant_message={},
            followup_messages=[],
            tool_calls=[],
            finish_reason="",
            usage={},
            aborted=True,
            abort_reason=llm_ctx.abort_reason,
        )

    with renderer.working_status():
        stream = await with_retry_async(
            lambda: client.get_model_client(async_client=True)(
                messages=llm_ctx.messages,
                tools=llm_ctx.tools,
                max_tokens=client.clamp_max_tokens(llm_ctx.max_tokens),
                stream=True,
            ),
            provider=client.current_provider,
        )

    with renderer.thinking_status(), renderer.scope():
        content, reasoning_content, tool_calls, finish_reason, usage = await streaming_message(
            stream, on_text=renderer.on_text)

    assistant_message = {
        "role": "assistant",
        "content": content,
        "reasoning_content": reasoning_content,
        "usage": usage,
    }
    injected: list[dict] = []
    result_ctx = AfterLLMContext(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason or "",
        usage=usage,
    )
    await dispatch("after_llm", result_ctx, include_external=policy.use_extensions)
    assistant_message["content"] = result_ctx.content
    tool_calls = result_ctx.tool_calls or []
    injected = list(result_ctx.inject_messages)

    if not tool_calls:
        return StepOutcome(
            assistant_message=assistant_message,
            followup_messages=injected,
            tool_calls=[],
            finish_reason=finish_reason or "",
            usage=usage,
        )

    if finish_reason == "length" and policy.defer_tools_on_length:
        return StepOutcome(
            assistant_message=assistant_message,
            followup_messages=injected,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    assistant_message["tool_calls"] = tool_calls
    tool_messages, tool_injected = await _run_tools(
        tool_calls,
        pool,
        tctx,
        policy,
        renderer,
    )
    return StepOutcome(
        assistant_message=assistant_message,
        followup_messages=injected + tool_messages + tool_injected,
        tool_calls=tool_calls,
        finish_reason=finish_reason or "",
        usage=usage,
    )


@dataclass
class _PreparedCall:
    """一次工具调用的中间态。

    final_message / outcome / exception 互斥：准备阶段定型为 final_message 的调用不再执行；
    outcome 是待收尾的真实结局；exception 是待上抛的执行失败。
    """
    tool_call_id: str
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    # todo: 改为更直白且有辨识性的命名
    final_message: dict | None = None  # 已定型：直接入历史，跳过 after_tool / 渲染 / diff
    outcome: ToolResult | None = None  # 待收尾：走 after_tool / 渲染 / diff
    diff: str = ""
    exception: BaseException | None = None


async def _prepare_tool_call(
        tool_call: dict,
        pool: ToolPool,
        tctx: ToolContext,
        policy: StepPolicy,
        renderer: StepRenderer,
) -> _PreparedCall:
    """准备阶段（串行）：中断检查 → 解析 → before_tool（含权限门）→ 渲染调用 → tool_execute → 后台路由。

    立即定型的调用（中断 / 解析失败 / 被 block / 扩展接管 / 后台已启动）写好 final_message 或 outcome 返回；
    其余留给执行阶段。
    """
    tool_call_id = tool_call.get("id", "")
    item = _PreparedCall(tool_call_id)

    if tctx.interrupted:
        item.final_message = _tool_message(tool_call_id, USER_INTERRUPT_PROMPT)
        return item

    try:
        call = ChatCompletionMessageToolCall(**tool_call)
        tool_args = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as e:
        item.final_message = _tool_message(tool_call_id, f"[Error] {type(e).__name__}: {e}")
        return item
    tool_name = call.function.name
    item.tool_name = tool_name

    tool_ctx = BeforeToolContext(tool_name=tool_name, args=tool_args, cwd=tctx.cwd)
    await dispatch("before_tool", tool_ctx, include_external=policy.use_extensions)
    tool_args = tool_ctx.args
    call.function.arguments = json.dumps(tool_args, ensure_ascii=False)
    if tool_ctx.blocked:
        item.final_message = _tool_message(tool_call_id, tool_ctx.block_reason)
        return item

    item.tool_args = tool_args
    renderer.on_tool_call(tool_name, tool_args)

    exec_ctx = ToolExecuteContext(tool_name=tool_name, args=tool_args, tctx=tctx)
    with renderer.working_status():
        await dispatch("tool_execute", exec_ctx, include_external=policy.use_extensions)
    if exec_ctx.handled:
        item.outcome = ToolResult(content=exec_ctx.result, is_error=exec_ctx.is_error)
        return item

    if policy.background and should_run_background(tool_name, tool_args):
        bg_id = start_background_task(call, pool, tctx)
        item.final_message = _tool_message(
            tool_call_id,
            f"[Background task `{bg_id}` started] "
            f"Result will arrive with a `<background-task-notification>` tag.",
        )
        return item

    return item


def _preview_diff(item: _PreparedCall) -> list[tuple[str, int, str]] | str:
    """执行前预览 write / edit 将产生的变化行；失败记日志并返回空串。"""
    if item.tool_name not in DIFF_TOOLS:
        return ""
    try:
        if item.tool_name == "write_file":
            return preview_write(item.tool_args["path"], item.tool_args["content"])
        return preview_edit(item.tool_args["path"], item.tool_args["old_text"], item.tool_args["new_text"])
    except Exception as e:
        _LOGGER.exception(f"[Diff exception] {e}")
        return ""


async def _run_tools(
        tool_calls: list[dict],
        pool: ToolPool,
        tctx: ToolContext,
        policy: StepPolicy,
        renderer: StepRenderer,
) -> tuple[list[dict], list[dict]]:
    """按组派发工具：parallel_safe 声明组内用 asyncio.gather，不可并发工具自成一组形成屏障。

    每组三段：准备（串行，含扩展 / 权限 / 后台路由）→ 执行（组内并发）→ 收尾（串行，按原调用
    顺序回填消息、触发 after_tool 与渲染）。中断按占位收尾。
    """
    results: list[dict] = []
    injected: list[dict] = []
    executed_ids: set[str] = set()
    try:
        for group in group_parallel_tool_calls(tool_calls, pool):
            prepared = [
                await _prepare_tool_call(
                    tool_call,
                    pool,
                    tctx,
                    policy,
                    renderer,
                )
                for tool_call in group
            ]

            runnable = [item for item in prepared if item.final_message is None and item.outcome is None]
            if runnable:
                with renderer.working_status():
                    for item in runnable:
                        if policy.diff_preview:
                            item.diff = _preview_diff(item)

                    tasks = []
                    for item in runnable:
                        task = asyncio.create_task(pool.execute(item.tool_name, item.tool_args, tctx))
                        if tctx.agent_run:
                            tctx.agent_run.track(task)
                        tasks.append(task)
                    try:
                        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                    except asyncio.CancelledError:
                        # Esc 同时取消 turn task 与组内 task：按中断占位收尾，不随 CancelledError 丢弃整轮消息
                        if not tctx.interrupted:
                            raise
                        outcomes = [asyncio.CancelledError()] * len(runnable)
                for item, outcome in zip(runnable, outcomes):
                    if isinstance(outcome, BaseException):
                        item.exception = outcome
                    else:
                        item.outcome = outcome

            for item in prepared:
                if item.final_message is not None:
                    results.append(item.final_message)
                    executed_ids.add(item.tool_call_id)
                    continue
                if item.exception is not None:
                    if (isinstance(item.exception, asyncio.CancelledError)
                            and tctx.interrupted):
                        results.append(_tool_message(item.tool_call_id, USER_INTERRUPT_PROMPT))
                        executed_ids.add(item.tool_call_id)
                        continue
                    raise item.exception

                outcome = item.outcome
                output = outcome.content
                is_error = outcome.is_error
                if item.diff and not is_error:
                    renderer.on_diff(item.diff)
                result_ctx = AfterToolContext(
                    tool_name=item.tool_name,
                    args=item.tool_args,
                    result=output,
                    is_error=is_error,
                )
                await dispatch("after_tool", result_ctx, include_external=policy.use_extensions)
                output = result_ctx.result
                injected.extend(result_ctx.inject_messages)

                renderer.on_tool_result(output, is_error)

                message = _tool_message(item.tool_call_id, output)
                if item.diff and not is_error:
                    message["payload"] = item.diff
                results.append(message)
                executed_ids.add(item.tool_call_id)

    finally:
        # 未执行的 tool call 补占位：消息历史必须给每个 tool_call_id 一个结果
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id", "")
            if tool_call_id not in executed_ids:
                results.append(_tool_message(tool_call_id, "[Error] tool call aborted before execution"))
    return results, injected


def group_parallel_tool_calls(tool_calls: list[dict], pool: ToolPool) -> list[list[dict]]:
    """按工具声明的 parallel_safe 分组：连续的并发安全工具合成一组（组内可并发执行），
    其余工具各自单成一组的屏障，组间保持串行。"""

    def is_parallel(tool_call: dict) -> bool:
        return pool.is_parallel_safe((tool_call.get("function") or {}).get("name", ""))

    groups: list[list[dict]] = []
    for parallel, chunk in groupby(tool_calls, key=is_parallel):
        chunk = list(chunk)
        if parallel:
            groups.append(chunk)
        else:
            groups.extend([[call] for call in chunk])
    return groups
