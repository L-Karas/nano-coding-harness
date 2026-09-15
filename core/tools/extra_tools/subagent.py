import asyncio

from pydantic import Field

from core.runtime_context import AgentRunContext
from core.sub_agent import spawn_subagent
from core.tools.tool_base import BaseTool


class SpawnSubagent(BaseTool):
    """Launch a subagent to handle a complex subtask. Returns only the final conclusion."""
    description: str = Field(description="The task description for the subagent to complete.")
    should_run_in_background: bool = Field(default=False, description="Whether to run in background or not.")

    agent_type: set = {"main"}


def run_spawn_subagent(description: str) -> str:
    return spawn_subagent(description)


async def run_spawn_subagent_async(description: str, ctx: AgentRunContext = None) -> str:
    result = await asyncio.to_thread(spawn_subagent, description)
    if ctx:
        ctx.raise_if_cancelled()
    return result
