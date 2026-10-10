from pydantic import Field

from core.runtime_context import ToolContext
from core.sub_agent import spawn_subagent
from core.tools.tool_base import BaseTool


class SpawnSubagent(BaseTool):
    """Launch a subagent in the background to handle a complex subtask; keep working and its conclusion arrives later as a background-task notification."""
    description: str = Field(description="The task description for the subagent to complete.")

    agent_type: set = {"main"}

    async def arun(self, tctx: ToolContext | None = None) -> str:
        return await spawn_subagent(self.description, tctx.agent_run if tctx else None)
