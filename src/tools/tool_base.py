from typing import Any, Literal

from pydantic import BaseModel, Field

from tools.utils import to_openai_tool


class BaseTool(BaseModel):
    """
    Base tool class of any tool
    """
    agent_level: Literal["sub", "main"] = Field(default="main",
                                                description="sub-agent can only use tools with value 'sub'.")

    @classmethod
    def to_openai_tool(cls) -> dict[str, Any]:
        """
        get tool name
        """
        return to_openai_tool(cls)
