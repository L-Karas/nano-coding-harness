from pydantic import Field

from core.runtime_context import ToolContext
from core.skill import load_skill
from core.tools.tool_base import BaseTool


class LoadSkill(BaseTool):
    """Load the full content of a skill by name."""
    name: str = Field(description="The name of the skill to load.")

    agent_type: set = {"main"}

    def run(self, tctx: ToolContext | None = None) -> str:
        return load_skill(self.name)
