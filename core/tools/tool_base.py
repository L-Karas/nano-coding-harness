import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.runtime_context import ToolContext
from core.tools.utils import to_openai_tool


class BaseTool(BaseModel):
    """
    Base tool class of any tool
    """
    model_config = ConfigDict(extra="forbid")

    agent_type: set[Literal["main", "sub-agent", "teammate"]] = (
        Field(default_factory=lambda: {"main"},
              description="Agent types (main, sub-agent, teammate) that are allowed to use this tool; "
                          "a tool is available to an agent only if that agent's type is included in this set."))
    experimental: bool = Field(default=False, description="Whether the tool is in experimental status.")

    @classmethod
    def to_openai_tool(cls) -> dict[str, Any]:
        """
        get tool name
        """
        return to_openai_tool(cls)

    def run(self, tctx: ToolContext | None = None) -> str:
        """同步入口：子类覆写 run / arun 之一；缺省用 asyncio.run 跑异步实现（同步消费者所在线程无事件循环）。"""
        return asyncio.run(self.arun(tctx))

    async def arun(self, tctx: ToolContext | None = None) -> str:
        """异步入口：缺省做取消检查后委托同步实现。"""
        if tctx is not None:
            tctx.raise_if_cancelled()
        return self.run(tctx)
