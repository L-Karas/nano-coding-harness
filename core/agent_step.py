"""Agent step：一次模型调用及其触发的工具派发（main / sub-agent / teammate 共享）。

step 不改调用方的 messages，不碰 session / cron / 阶段标记 / 协议门控；返回 StepOutcome 由外层决定
append、落盘与重试。差异经 StepPolicy 与 StepRenderer 显式化（见 docs/adr/0003-agent-step.md）。
"""
import asyncio
import json
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, ContextManager

from openai.types.chat import ChatCompletionMessageToolCall

from core.background_task import should_run_background, start_background_task
from core.context import to_llm_messages
from core.extension import (
    AfterLLMContext, AfterToolContext, BeforeLLMContext, BeforeToolContext, dispatch,
)
from core.hook.hook import trigger_hooks
from core.log.log import get_logger
from core.recovery.error_recovery import with_retry_async
from core.runtime_context import ToolContext
from core.streaming import streaming_message
from core.template import USER_INTERRUPT_PROMPT
from core.tools import ToolPool, ToolResult
from core.tools.base_tools.diff import DIFF_TOOLS, preview_edit, preview_write

_LOGGER = get_logger(__name__)


def _noop(*_args, **_kwargs) -> None:
    pass


def _null_cm() -> ContextManager:
    return nullcontext()


@dataclass(frozen=True)
class StepPolicy:
    """step 的行为差异：扩展四节点 / permission hooks / 后台路由 / diff 预览 / 工具执行后的 halt。

    defer_tools_on_length：finish_reason == "length" 时先不执行工具，把恢复决定留给外层（主循环的
    扩窗与续写重试必须在工具执行前发生）。
    """
    use_extensions: bool = True
    permission_hooks: bool = True
    background: bool = False
    diff_preview: bool = False
    defer_tools_on_length: bool = False
    should_halt: Callable[[str, ToolResult], bool] | None = None


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
    halted: bool = False


async def run_agent_step(history: list[dict], system_prompt: str, *, client, pool: ToolPool,
                         tctx: ToolContext, max_tokens: int, policy: StepPolicy = StepPolicy(),
                         renderer: StepRenderer = StepRenderer()) -> StepOutcome:
    """一次模型调用（扩展 → 重试请求 → 流式解析 → 扩展）与其工具派发（扩展 → hooks → 执行 → 扩展）。"""
    tools = pool.schemas()
    messages = [{"role": "system", "content": system_prompt}] + to_llm_messages(history)

    llm_ctx = BeforeLLMContext(messages=messages, tools=tools, max_tokens=max_tokens)
    if policy.use_extensions:
        await dispatch("before_llm", llm_ctx)
        if llm_ctx.aborted:
            return StepOutcome(assistant_message={}, followup_messages=[], tool_calls=[],
                               finish_reason="", usage={}, aborted=True,
                               abort_reason=llm_ctx.abort_reason)

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
    if policy.use_extensions:
        result_ctx = AfterLLMContext(content=content, tool_calls=tool_calls or [],
                                     finish_reason=finish_reason or "", usage=usage)
        await dispatch("after_llm", result_ctx)
        assistant_message["content"] = result_ctx.content
        tool_calls = result_ctx.tool_calls or []
        injected = list(result_ctx.inject_messages)

    if not tool_calls:
        return StepOutcome(assistant_message=assistant_message, followup_messages=injected,
                           tool_calls=[], finish_reason=finish_reason or "", usage=usage)

    if finish_reason == "length" and policy.defer_tools_on_length:
        return StepOutcome(assistant_message=assistant_message, followup_messages=injected,
                           tool_calls=tool_calls, finish_reason=finish_reason, usage=usage)

    assistant_message["tool_calls"] = tool_calls
    tool_messages, tool_injected, halted = await _run_tools(tool_calls, pool, tctx, policy, renderer)
    return StepOutcome(assistant_message=assistant_message,
                       followup_messages=injected + tool_messages + tool_injected,
                       tool_calls=tool_calls, finish_reason=finish_reason or "", usage=usage,
                       halted=halted)


async def _run_tools(tool_calls: list[dict], pool: ToolPool, tctx: ToolContext, policy: StepPolicy,
                     renderer: StepRenderer) -> tuple[list[dict], list[dict], bool]:
    results: list[dict] = []
    injected: list[dict] = []
    halted = False
    executed_ids: set[str] = set()
    try:
        # todo: 采用 asyncio.gather 并发执行工具，当前工具执行实质为串行执行
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id", "")
            if tctx.agent_run and tctx.agent_run.interrupted:
                results.append({"role": "tool", "tool_call_id": tool_call_id, "content": USER_INTERRUPT_PROMPT})
                executed_ids.add(tool_call_id)
                continue

            try:
                call = ChatCompletionMessageToolCall(**tool_call)
                tool_args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as e:
                results.append({"role": "tool", "tool_call_id": tool_call_id,
                                "content": f"[Error] {type(e).__name__}: {e}"})
                executed_ids.add(tool_call_id)
                continue
            tool_name = call.function.name

            if policy.use_extensions:
                tool_ctx = BeforeToolContext(tool_name=tool_name, args=tool_args)
                await dispatch("before_tool", tool_ctx)
                tool_args = tool_ctx.args
                call.function.arguments = json.dumps(tool_args, ensure_ascii=False)
                if tool_ctx.blocked:
                    results.append({"role": "tool", "tool_call_id": tool_call_id,
                                    "content": f"[Extension blocked] {tool_ctx.block_reason}"})
                    executed_ids.add(tool_call_id)
                    continue

            renderer.on_tool_call(tool_name, tool_args)

            if policy.permission_hooks:
                blocked = trigger_hooks("pre_tool_call", call)
                if blocked:
                    results.append({"role": "tool", "tool_call_id": tool_call_id,
                                    "content": f"[{str(blocked)}]"})
                    executed_ids.add(tool_call_id)
                    continue

            if policy.background and should_run_background(tool_name, tool_args):
                bg_id = start_background_task(call, pool, tctx)
                results.append({"role": "tool", "tool_call_id": tool_call_id,
                                "content": f"[Background task `{bg_id}` started] "
                                           f"Result will arrive with a `<background-task-notification>` tag."})
                executed_ids.add(tool_call_id)
                continue

            diff = ""
            with renderer.working_status():
                if policy.diff_preview and tool_name in DIFF_TOOLS:
                    try:
                        diff = preview_write(tool_args["path"], tool_args["content"]) if tool_name == "write_file" \
                            else preview_edit(tool_args["path"], tool_args["old_text"], tool_args["new_text"])
                    except Exception as e:
                        _LOGGER.exception(f"[Diff exception] {e}")

                task = asyncio.create_task(pool.execute(tool_name, tool_args, tctx))
                if tctx.agent_run:
                    tctx.agent_run.track(task)
                try:
                    result = await task
                except asyncio.CancelledError:
                    if not (tctx.agent_run and tctx.agent_run.interrupted):
                        raise
                    task.cancel()
                    results.append({"role": "tool", "tool_call_id": tool_call_id,
                                    "content": USER_INTERRUPT_PROMPT})
                    executed_ids.add(tool_call_id)
                    continue

                if diff and not result.is_error:
                    renderer.on_diff(diff)

            output = result.content
            is_error = result.is_error
            if policy.use_extensions:
                result_ctx = AfterToolContext(tool_name=tool_name, args=tool_args,
                                              result=output, is_error=is_error)
                await dispatch("after_tool", result_ctx)
                output = result_ctx.result
                injected.extend(result_ctx.inject_messages)

            renderer.on_tool_result(output, is_error)

            message = {"role": "tool", "tool_call_id": tool_call_id, "content": output}
            if diff and not is_error:
                message["payload"] = diff
            results.append(message)
            executed_ids.add(tool_call_id)

            if policy.should_halt and policy.should_halt(tool_name, result):
                halted = True
                break
    finally:
        # 未执行的 tool call 补占位：消息历史必须给每个 tool_call_id 一个结果
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id", "")
            if tool_call_id not in executed_ids:
                results.append({"role": "tool", "tool_call_id": tool_call_id,
                                "content": "[Error] tool call aborted before execution"})
    return results, injected, halted
