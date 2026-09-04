from pydantic import Field

from core.sub_agent import spawn_subagent
from core.tools.tool_base import BaseTool


class SpawnSubagent(BaseTool):
    """Launch a subagent to handle a complex subtask. Returns only the final conclusion."""
    description: str = Field(description="The task description for the subagent to complete.")
    should_run_in_background: bool = Field(default=False, description="Whether to run in background or not.")

    agent_level: set = {"main"}


def run_spawn_subagent(description: str) -> str:
    return spawn_subagent(description)
