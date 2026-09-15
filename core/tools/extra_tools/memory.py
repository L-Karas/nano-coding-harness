from typing import Literal

from pydantic import Field

from core import memory
from core.tools.tool_base import BaseTool


class SaveMemory(BaseTool):
    """Save long-term memories, user preferences,
    and key contextual data across sessions—so every interaction picks up right where the last one left off,
    delivering a consistent, personalized experience whenever the user returns."""
    title: str = Field(description="Short identifier (e.g. prefer tabs, db schema)")
    content: str = Field(description="Full memory content (multi-line OK)")
    mem_type: Literal["user", "feedback", "project", "reference"] = Field(
        description="user=preferences, feedback=corrections, "
                    "project=non-obvious project conventions or decision reasons, "
                    "reference=external resource pointers")

    agent_type: set = {"main"}


def run_save_memory(title: str, content: str, mem_type: Literal["user", "feedback", "project", "reference"]):
    return memory.run_save_memory(title, content, mem_type)


async def run_save_memory_async(title: str, content: str,
                                mem_type: Literal["user", "feedback", "project", "reference"],
                                ctx=None):
    return memory.run_save_memory(title, content, mem_type)
