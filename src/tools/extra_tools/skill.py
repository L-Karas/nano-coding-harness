from pydantic import Field

from src.skills import load_skill
from src.tools.tool_base import BaseTool


class LoadSkill(BaseTool):
    """Load the full content of a skill by name."""
    name: str = Field(description="The name of the skill to load.")

    agent_level: set = {"main"}


def run_load_skill(name: str) -> str:
    return load_skill(name)
