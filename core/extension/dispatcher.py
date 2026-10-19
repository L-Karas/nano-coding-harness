"""扩展事件派发器：按阶段排序调用，支持短路、异步、决定票与 fail-open/fail-closed。"""
import copy
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from core.extension.protocol import STAGE_ORDER, validate_registration
from logger import get_logger

_LOGGER = get_logger(__name__)


@dataclass
class _Handler:
    module_name: str
    callback: Callable
    stage: str = "user"
    fail_closed: bool = False
    builtin: bool = False


_HANDLERS: dict[str, list[_Handler]] = {}


def add(
        event: str,
        callback: Callable,
        module_name: str,
        *,
        stage: str = "user",
        fail_closed: bool = False,
        builtin: bool = False,
) -> None:
    """注册一个事件回调；stage 决定派发顺序，fail_closed 让异常短路为拒绝。

    只有内置回调可以声明非 user 阶段，保证外部扩展无法排到权限门之后。
    """
    validate_registration(event, callback, stage=stage, fail_closed=fail_closed, builtin=builtin)
    _HANDLERS.setdefault(event, []).append(
        _Handler(module_name, callback, stage, fail_closed, builtin)
    )


def reset() -> None:
    """清空全部注册，供测试隔离使用。"""
    for handlers in _HANDLERS.values():
        handlers.clear()


async def dispatch(event: str, ctx: Any, *, include_external: bool = True) -> None:
    """按 user → observer → gate 顺序调用事件回调，原地修改 ctx；单回调异常不影响其余回调。"""
    handlers = [
        handler for handler in _HANDLERS.get(event, ())
        if include_external or handler.builtin
    ]
    handlers.sort(key=lambda handler: STAGE_ORDER[handler.stage])

    gate_seen = False
    for handler in handlers:
        if handler.stage == "gate" and not gate_seen:
            # todo: 多回调导致参数变化，进而影响决策过时，处理
            _drop_stale_decisions(ctx)
            gate_seen = True

        before = _decision_count(ctx)
        try:
            result = handler.callback(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            _LOGGER.exception(
                f"[Extension] {handler.module_name}:{_callback_name(handler.callback)} failed at {event}")
            # 异常 = 该回调弃权：回滚本次调用新登记的票，不能让崩溃回调的半票进入裁决。
            if isinstance(getattr(ctx, "decisions", None), list):
                ctx.decisions = ctx.decisions[:before]
            if handler.fail_closed and hasattr(ctx, "blocked"):
                ctx.blocked = True
                ctx.block_reason = f"Permission check failed: {type(exc).__name__}"
                break
            continue
        _LOGGER.debug(
            f"[Extension] {handler.module_name}:{_callback_name(handler.callback)} 执行成功 at {event}")
        _commit_decisions(ctx, handler.module_name, before)
        if (getattr(ctx, "blocked", False) or getattr(ctx, "aborted", False)
                or getattr(ctx, "handled", False)):
            break
    _drop_stale_decisions(ctx)
    _normalize(ctx)


def _callback_name(callback: Callable) -> str:
    """取回调可读名；lambda / 可调用对象回退到 str(callback)。"""
    return getattr(callback, "__name__", str(callback))


def _decision_count(ctx: Any) -> int:
    """返回当前决定票数量；被改坏为非 list 时视作 0。"""
    decisions = getattr(ctx, "decisions", None)
    return len(decisions) if isinstance(decisions, list) else 0


def _commit_decisions(ctx: Any, module_name: str, before: int) -> None:
    """把本次回调新增的决定票归一：只保留最后一条，并记录来源与参数快照。"""
    decisions = getattr(ctx, "decisions", None)
    if not isinstance(decisions, list):
        return
    new = decisions[before:]
    if not new:
        return
    decisions = decisions[:before] + [new[-1]]
    decisions[-1].source = module_name
    if hasattr(ctx, "args"):
        decisions[-1].args_snapshot = copy.deepcopy(ctx.args)
    ctx.decisions = decisions


def _drop_stale_decisions(ctx: Any) -> None:
    """作废基于旧参数作出的决定票；参数被后续回调改写时不再参与裁决。"""
    decisions = getattr(ctx, "decisions", None)
    if not isinstance(decisions, list):
        return
    args = getattr(ctx, "args", None)
    kept = []
    dropped = 0
    for decision in decisions:
        if decision.args_snapshot is not None and decision.args_snapshot != args:
            dropped += 1
        else:
            kept.append(decision)
    if dropped:
        _LOGGER.info(f"[Extension] 丢弃 {dropped} 个过期决定票（参数已改写）")
    ctx.decisions = kept


def _normalize(ctx: Any) -> None:
    """把钩子可能改坏的可写字段归一为下游期望的类型。"""
    if hasattr(ctx, "result"):
        ctx.result = str(ctx.result)
    if hasattr(ctx, "content") and ctx.content is not None:
        ctx.content = str(ctx.content)
    for field_name in ("tool_calls", "inject_messages", "decisions"):
        if hasattr(ctx, field_name) and not isinstance(getattr(ctx, field_name), list):
            _LOGGER.error(
                f"[Extension] {type(ctx).__name__}.{field_name} 必须是 list，收到: "
                f"{type(getattr(ctx, field_name)).__name__}，已置空")
            setattr(ctx, field_name, [])
