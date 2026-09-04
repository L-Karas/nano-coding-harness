from typing import Optional

from pydantic import Field

from core.compact.context_compact import compact_history
from core.tools.tool_base import BaseTool


class Compact(BaseTool):
    """Summarize earlier conversation and continue with compacted context."""
    focus: Optional[str] = Field(default=None,
                                 description="What to focus on when summarizing (e.g. 'current goal', "
                                             "'key findings').")

    agent_level: set = {"main", "sub-agent", "teammate"}


# todo: focus feature
def run_compact(messages: list, focus: str = "") -> list:
    return compact_history(messages)
