"""事件规格：事件 → 上下文类型、短路字段、归一字段。

把原先散落在 dispatcher 里的 `hasattr` 判断收成一张表：
- `context` 让错误事件/上下文配对立即暴露；
- `stop_field` 只允许该事件自己的控制字段短路，外部扩展无法用
  `ctx.aborted = True` 这类外来字段跳过权限门；
- `str_fields` / `list_fields` 是派发结束后的最小字段归一范围。
"""
from dataclasses import dataclass

from core.extension.context import (
    AfterLLMContext,
    AfterToolContext,
    BeforeLLMContext,
    BeforeToolContext,
    ToolExecuteContext,
)
from core.extension.protocol import EVENTS


@dataclass(frozen=True)
class EventSpec:
    context: type
    stop_field: str | None = None
    str_fields: tuple[str, ...] = ()
    preserve_none: frozenset[str] = frozenset()
    list_fields: tuple[str, ...] = ()
    decisions: bool = False


EVENT_SPECS: dict[str, EventSpec] = {
    "before_llm": EventSpec(BeforeLLMContext, stop_field="aborted"),
    "after_llm": EventSpec(
        AfterLLMContext,
        str_fields=("content",),
        preserve_none=frozenset({"content"}),
        list_fields=("tool_calls", "inject_messages"),
    ),
    "before_tool": EventSpec(BeforeToolContext, stop_field="blocked", decisions=True),
    "tool_execute": EventSpec(ToolExecuteContext, stop_field="handled", str_fields=("result",)),
    "after_tool": EventSpec(
        AfterToolContext,
        str_fields=("result",),
        list_fields=("inject_messages",),
    ),
}

if tuple(EVENT_SPECS) != EVENTS:  # 防止新增事件时只改 protocol 漏掉规格
    raise RuntimeError("EVENT_SPECS 与 EVENTS 不一致")


def spec_for(event: str) -> EventSpec:
    """按事件名取规格；未知事件立即报错，避免 dispatch 静默 no-op。

    Args:
        event: 事件名。

    Returns:
        对应的事件规格。

    Raises:
        ValueError: 事件名未在 EVENT_SPECS 中登记。
    """
    try:
        return EVENT_SPECS[event]
    except KeyError:
        raise ValueError(
            f"未知扩展事件: {event!r}（可用: {', '.join(EVENT_SPECS)}）"
        ) from None
