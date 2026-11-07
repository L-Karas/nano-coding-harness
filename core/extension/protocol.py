"""扩展注册协议：事件、阶段、决定动作、注册记录，以及注册参数的共享校验。

这些常量与校验只在此定义一次；`ExtensionAPI.on()`（扩展缓冲入口）与
`dispatcher.add()`（底层注册入口）共用同一层校验，避免两处各写一份导致契约漂移。
"""
from dataclasses import dataclass
from typing import Callable

EVENTS: tuple[str, ...] = ("before_llm", "after_llm", "before_tool", "tool_execute", "after_tool")
STAGES: tuple[str, ...] = ("user", "observer", "gate")
ACTIONS: tuple[str, ...] = ("allow", "ask", "deny")

# 阶段排序：声明顺序即派发顺序（user → observer → gate）
STAGE_ORDER = {stage: index for index, stage in enumerate(STAGES)}


@dataclass(frozen=True)
class Registration:
    """一次待提交的事件注册；loader / builtin 不再解包位置元组。"""

    event: str
    callback: Callable
    stage: str = "user"
    fail_closed: bool = False


def validate_registration(
    event: str,
    callback: Callable,
    *,
    stage: str,
    fail_closed: bool,
    builtin: bool,
) -> None:
    """校验一次事件注册；任一参数非法立即抛错，由调用方决定如何处置。

    Args:
        event: 事件名。
        callback: 事件回调。
        stage: 派发阶段。
        fail_closed: 回调异常时是否短路为拒绝。
        builtin: 是否为内置扩展注册。

    Raises:
        ValueError: 事件名 / 阶段非法，或外部扩展尝试使用内置能力。
        TypeError: 回调不可调用。
    """
    if event not in EVENTS:
        raise ValueError(f"未知扩展事件: {event!r}（可用: {', '.join(EVENTS)}）")
    if not callable(callback):
        raise TypeError(f"扩展回调必须可调用，收到: {type(callback).__name__}")
    if stage not in STAGES:
        raise ValueError(f"未知扩展阶段: {stage!r}（可用: {', '.join(STAGES)}）")
    if not builtin and (stage != "user" or fail_closed):
        raise ValueError("仅内置扩展可以注册非 user 阶段或声明 fail_closed")
