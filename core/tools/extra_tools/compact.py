import asyncio
from typing import Optional

from pydantic import Field

from core.compact.context_compact import compact_history
from core.tools.tool_base import BaseTool


class Compact(BaseTool):
    """Summarize earlier conversation and continue with compacted context."""
    focus: Optional[str] = Field(default=None,
                                 description="What to focus on when summarizing (e.g. 'current goal', "
                                             "'key findings').")

    agent_type: set = {"main", "sub-agent", "teammate"}


def run_compact(messages: list, focus: str = "") -> list:
    loop = asyncio.get_event_loop()
    return asyncio.run_coroutine_threadsafe(compact_history(messages), loop=loop).result()


# todo: focus feature
async def run_compact_async(messages: list, focus: str = "", ctx=None) -> list:
    return await compact_history(messages)
