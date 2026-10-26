from pathlib import Path
from typing import Annotated

from pydantic import Field, StringConstraints

from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.sub_agent import spawn_subagent
from core.tools.tool_base import BaseTool


class SpawnSubagent(BaseTool):
    """Launch a subagent in the background to handle a complex subtask; keep working and its conclusion arrives later as a background-task notification."""
    description: str = Field(description="The task description for the subagent to complete.")
    goal: str = Field(description="A short one-line description of the subagent's goal, shown in the TUI "
                                   "when the entry is expanded.")
    role: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32,
                                           pattern=r"^[^\r\n]+$")] = Field(
        description="A short role label for the subagent (e.g. reviewer, researcher), shown in the TUI in place "
                    "of the subagent id.")
    working_directory: str = Field(default="",
                                   description="Directory the subagent works in. Relative paths resolve against the "
                                               "main agent's working directory; leave empty to keep the same working "
                                               "directory as the main agent.")

    agent_type: set = {"main"}
    parallel_safe: bool = True

    async def arun(self, tctx: ToolContext | None = None) -> str:
        base = tctx.cwd if tctx and tctx.cwd else WORKDIR
        work_dir = base
        if self.working_directory:
            work_dir = (base / Path(self.working_directory).expanduser()).resolve()
            if not work_dir.is_dir():
                raise ValueError(f"working_directory '{work_dir}' does not exist or is not a directory")
        return await spawn_subagent(self.description, tctx.agent_run if tctx else None,
                                    goal=self.goal, role=self.role, working_directory=work_dir)
