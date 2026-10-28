"""扩展事件派发器：按阶段排序调用，支持短路、异步、决定票与 fail-open/fail-closed。"""
import copy
import inspect
from dataclasses import dataclass
from typing import Callable, Iterable

from core.extension.context import Ballot
from core.extension.events import EventSpec, spec_for
from core.extension.protocol import Registration, STAGE_ORDER, validate_registration
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)
_MISSING = object()


@dataclass
class _Handler:
    module_name: str
    callback: Callable
    stage: str = "user"
    fail_closed: bool = False
    builtin: bool = False


@dataclass(frozen=True)
class HandlerInfo:
    """只读注册快照：供测试 / 诊断断言阶段、来源与回调名，不暴露回调对象。"""

    module_name: str
    callback_name: str
    stage: str
    fail_closed: bool
    builtin: bool


_HANDLERS: dict[str, list[_Handler]] = {}
# 扩展级派发开关：loader 注册完一个模块后按 extension.json.enabled 设置；
# 禁用只影响后续派发，已注册回调保留，重新启用无需再次 register。
_DISABLED_MODULES: set[str] = set()


def add_many(
        module_name: str,
        registrations: Iterable[Registration],
        *,
        builtin: bool = False,
) -> None:
    """原子注册一批回调：全部校验通过后才写入注册表，避免半个扩展生效。"""
    prepared: list[tuple[str, _Handler]] = []
    for registration in registrations:
        validate_registration(
            registration.event,
            registration.callback,
            stage=registration.stage,
            fail_closed=registration.fail_closed,
            builtin=builtin,
        )
        prepared.append((
            registration.event,
            _Handler(
                module_name,
                registration.callback,
                registration.stage,
                registration.fail_closed,
                builtin,
            ),
        ))
    for event, handler in prepared:
        _HANDLERS.setdefault(event, []).append(handler)


def add(
        event: str,
        callback: Callable,
        module_name: str,
        *,
        stage: str = "user",
        fail_closed: bool = False,
        builtin: bool = False,
) -> None:
    """注册一个事件回调；底层入口，host 侧优先使用 `add_many` / `commit_extension`。

    stage 决定派发顺序，fail_closed 让异常短路为拒绝。只有内置回调可以声明
    非 user 阶段，保证外部扩展无法排到权限门之后。
    """
    add_many(
        module_name,
        [Registration(event, callback, stage, fail_closed)],
        builtin=builtin,
    )


def _set_module_enabled(module_name: str, enabled: bool) -> None:
    """切换一个扩展模块的派发开关。

    外部扩展禁用后回调仍保留在注册表中，只在下一次派发时跳过，重新启用无需重新
    import / register。内置模块的派发由 dispatch 的 handler.builtin 分支保证，不受此开关影响。
    """
    if enabled:
        _DISABLED_MODULES.discard(module_name)
    else:
        _DISABLED_MODULES.add(module_name)


def is_module_enabled(module_name: str) -> bool:
    """该模块是否未被显式禁用。"""
    return module_name not in _DISABLED_MODULES


def describe_handlers(event: str) -> tuple[HandlerInfo, ...]:
    """返回某事件当前注册的只读快照；未知事件立即报错。"""
    spec_for(event)
    return tuple(
        HandlerInfo(handler.module_name, _callback_name(handler.callback),
                    handler.stage, handler.fail_closed, handler.builtin)
        for handler in _HANDLERS.get(event, ())
    )


def reset() -> None:
    """清空全部注册与模块开关，供测试隔离使用。"""
    for handlers in _HANDLERS.values():
        handlers.clear()
    _DISABLED_MODULES.clear()


async def dispatch(event: str, ctx: object, *, include_external: bool = True) -> None:
    """按 user → observer → gate 顺序调用事件回调，原地修改 ctx；单回调异常不影响其余回调。

    被禁用模块的回调不参与派发；内置模块不受模块开关影响。
    """
    spec = spec_for(event)
    if not isinstance(ctx, spec.context):
        raise TypeError(
            f"{event} 需要 {spec.context.__name__}，收到 {type(ctx).__name__}")

    handlers = [
        handler for handler in _HANDLERS.get(event, ())
        if (include_external or handler.builtin)
        and (handler.builtin or is_module_enabled(handler.module_name))
    ]
    handlers.sort(key=lambda handler: STAGE_ORDER[handler.stage])

    for handler in handlers:
        if spec.decisions and handler.stage == "gate":
            # 每个 gate 回调前都作废旧票：前一个 gate 回调也可能改写参数。
            _drop_stale_decisions(ctx)

        before = _decision_count(ctx, spec)
        try:
            result = handler.callback(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            _LOGGER.exception(
                f"[Extension] {handler.module_name}:{_callback_name(handler.callback)} failed at {event}")
            # 异常 = 该回调弃权：回滚本次调用新登记的票，不能让崩溃回调的半票进入裁决。
            _rollback_decisions(ctx, spec, before)
            if handler.fail_closed and spec.stop_field == "blocked":
                ctx._set_blocked(f"Permission check failed: {type(exc).__name__}")
                break
            continue
        _LOGGER.debug(
            f"[Extension] {handler.module_name}:{_callback_name(handler.callback)} 执行成功 at {event}")
        _commit_decisions(ctx, spec, handler.module_name, before)
        if spec.stop_field and getattr(ctx, spec.stop_field, False):
            break
    if spec.decisions:
        _drop_stale_decisions(ctx)
    _normalize(ctx, spec)


def _callback_name(callback: Callable) -> str:
    """取回调可读名；lambda / 可调用对象回退到 str(callback)。"""
    return getattr(callback, "__name__", str(callback))


def _decision_count(ctx: object, spec: EventSpec) -> int:
    """返回当前决定票数量；非票箱上下文视作 0。"""
    if not spec.decisions:
        return 0
    decisions = getattr(ctx, "decisions", None)
    return len(decisions) if isinstance(decisions, Ballot) else 0


def _rollback_decisions(ctx: object, spec: EventSpec, before: int) -> None:
    """丢弃一次失败回调新增的票。"""
    if not spec.decisions:
        return
    decisions = getattr(ctx, "decisions", None)
    if isinstance(decisions, Ballot):
        decisions.rollback_from(before)


def _commit_decisions(ctx: object, spec: EventSpec, module_name: str, before: int) -> None:
    """把本次回调新增的票归一：只保留最后一条，并记录来源与参数快照。"""
    if not spec.decisions:
        return
    decisions = getattr(ctx, "decisions", None)
    if not isinstance(decisions, Ballot):
        return
    if len(decisions) <= before:
        return
    try:
        args_snapshot = copy.deepcopy(getattr(ctx, "args", None))
    except Exception:
        _LOGGER.exception("[Extension] 决定票参数快照失败，已丢弃本次投票")
        decisions.rollback_from(before)
        return
    decisions.commit_from(before, module_name, args_snapshot)


def _drop_stale_decisions(ctx: object) -> None:
    """作废基于旧参数作出的决定票；参数被后续回调改写时不再参与裁决。"""
    decisions = getattr(ctx, "decisions", None)
    if not isinstance(decisions, Ballot):
        return
    dropped = decisions.drop_stale(getattr(ctx, "args", None))
    if dropped:
        _LOGGER.info(f"[Extension] 丢弃 {dropped} 个过期决定票（参数已改写）")


def _normalize(ctx: object, spec: EventSpec) -> None:
    """把钩子可能改坏的可写字段归一为下游期望的类型。"""
    for field_name in spec.str_fields:
        value = getattr(ctx, field_name, _MISSING)
        if value is _MISSING:
            continue
        if value is None and field_name in spec.preserve_none:
            continue
        setattr(ctx, field_name, str(value))
    for field_name in spec.list_fields:
        value = getattr(ctx, field_name, _MISSING)
        if value is _MISSING:
            continue
        if not isinstance(value, list):
            _LOGGER.error(
                f"[Extension] {type(ctx).__name__}.{field_name} 必须是 list，收到: "
                f"{type(value).__name__}，已置空")
            setattr(ctx, field_name, [])
