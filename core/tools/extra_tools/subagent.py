from pydantic import Field

from core.runtime_context import AgentRunContext
from core.sub_agent import spawn_subagent
from core.tools.tool_base import BaseTool


class SpawnSubagent(BaseTool):
    """Launch a subagent in the background to handle a complex subtask; keep working and its conclusion arrives later as a background-task notification."""
    description: str = Field(description="The task description for the subagent to complete.")

    agent_type: set = {"main"}


async def run_spawn_subagent_async(description: str, ctx: AgentRunContext = None) -> str:
    return await spawn_subagent(description, ctx)
