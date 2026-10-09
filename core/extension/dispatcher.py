"""扩展事件派发器：按注册顺序链式调用，支持短路、异步与 fail-open。"""
import inspect
from typing import Any, Callable

from core.log.log import get_logger

_LOGGER = get_logger(__name__)

_HANDLERS: dict[str, list[tuple[str, Callable]]] = {}


def add(event: str, callback: Callable, module_name: str) -> None:
    """注册一个事件回调，保留注册顺序。"""
    _HANDLERS.setdefault(event, []).append((module_name, callback))


def reset() -> None:
    """清空全部注册，供测试隔离使用。"""
    for handlers in _HANDLERS.values():
        handlers.clear()


async def dispatch(event: str, ctx: Any) -> None:
    """顺序调用事件回调，原地修改 ctx；单个回调异常不影响其余回调。"""
    for module_name, callback in list(_HANDLERS.get(event, ())):
        try:
            result = callback(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception:
            _LOGGER.exception(
                f"[Extension] {module_name}:{_callback_name(callback)} failed at {event}")
            continue
        _LOGGER.debug(
            f"[Extension] {module_name}:{_callback_name(callback)} 执行成功 at {event}")
        if getattr(ctx, "blocked", False) or getattr(ctx, "aborted", False):
            break
    _normalize(ctx)


def _callback_name(callback: Callable) -> str:
    """取回调可读名；lambda / 可调用对象回退到 str(callback)。"""
    return getattr(callback, "__name__", str(callback))


def _normalize(ctx: Any) -> None:
    """把钩子可能改坏的可写字段归一为下游期望的类型。"""
    if hasattr(ctx, "result"):
        ctx.result = str(ctx.result)
    if hasattr(ctx, "content") and ctx.content is not None:
        ctx.content = str(ctx.content)
    for field_name in ("tool_calls", "inject_messages"):
        if hasattr(ctx, field_name) and not isinstance(getattr(ctx, field_name), list):
            _LOGGER.error(
                f"[Extension] {type(ctx).__name__}.{field_name} 必须是 list，收到: "
                f"{type(getattr(ctx, field_name)).__name__}，已置空")
            setattr(ctx, field_name, [])
