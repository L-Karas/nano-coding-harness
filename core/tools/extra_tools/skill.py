from pydantic import Field

from core.runtime_context import ToolContext
from core.skill import load_skill
from core.tools.tool_base import BaseTool


class LoadSkill(BaseTool):
    """Load the full content of a skill by name."""
    name: str = Field(description="The name of the skill to load.")

    agent_type: set = {"main"}
    parallel_safe: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        """加载并返回指定技能的完整内容。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            技能正文；不存在时返回错误提示文本。
        """
        return load_skill(self.name)
