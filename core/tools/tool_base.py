from typing import Any, Literal

from pydantic import BaseModel, Field

from core.tools.utils import to_openai_tool


class BaseTool(BaseModel):
    """
    Base tool class of any tool
    """
    agent_type: set[Literal["main", "sub-agent", "teammate"]] = (
        Field(default_factory=lambda: {"main"},
              description="Agent types (main, sub-agent, teammate) that are allowed to use this tool; "
                          "a tool is available to an agent only if that agent's type is included in this set."))

    @classmethod
    def to_openai_tool(cls) -> dict[str, Any]:
        """
        get tool name
        """
        return to_openai_tool(cls)
