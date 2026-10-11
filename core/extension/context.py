"""外部扩展事件上下文对象：可写字段承载钩子对主流程的修改。"""
from dataclasses import dataclass, field
from typing import Any

from core.runtime_context import ToolContext


@dataclass
class BeforeLLMContext:
    messages: list[dict]
    tools: list[dict]
    max_tokens: int
    aborted: bool = False
    abort_reason: str = ""

    def abort(self, reason: str) -> None:
        self.aborted = True
        self.abort_reason = str(reason)


@dataclass
class AfterLLMContext:
    content: str | None
    tool_calls: list[dict]
    finish_reason: str = ""
    usage: Any = None
    inject_messages: list[dict] = field(default_factory=list)


@dataclass
class BeforeToolContext:
    tool_name: str
    args: dict
    blocked: bool = False
    block_reason: str = ""

    def block(self, reason: str) -> None:
        self.blocked = True
        self.block_reason = str(reason)


@dataclass
class AfterToolContext:
    tool_name: str
    args: dict
    result: str
    is_error: bool = False
    inject_messages: list[dict] = field(default_factory=list)


@dataclass
class ToolExecuteContext:
    tool_name: str
    args: dict
    tctx: ToolContext | None = None
    handled: bool = False
    result: str = ""
    is_error: bool = False

    def handle(self, result, is_error: bool = False) -> None:
        """声明接管本次执行；结果取代内置执行（只写 result 不算接管）。"""
        self.handled = True
        self.result = str(result)
        self.is_error = bool(is_error)
