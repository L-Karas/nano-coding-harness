from typing import Literal

from pydantic import Field

import memory
from tools.tool_base import BaseTool


class SaveMemory(BaseTool):
    title: str = Field(description="Short identifier (e.g. prefer tabs, db schema)")
    content: str = Field(description="Full memory content (multi-line OK)")
    mem_type: Literal["user", "feedback", "project", "reference"] = Field(
        description="user=preferences, feedback=corrections, "
                    "project=non-obvious project conventions or decision reasons, "
                    "reference=external resource pointers")

    agent_level: set = {"main"}


def run_save_memory(title: str, content: str, mem_type: Literal["user", "feedback", "project", "reference"]):
    return memory.run_save_memory(title, content, mem_type)
