from pydantic import Field

from core.experimental.worktree import create_worktree, remove_worktree, keep_worktree
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool


class CreateWorktree(BaseTool):
    """Create an isolated git worktree for a task."""
    name: str = Field(description="Name for the new worktree.")
    task_id: str | None = Field(default=None, description="Optional task ID to associate with the worktree.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        return create_worktree(self.name, self.task_id or "")


class RemoveWorktree(BaseTool):
    """Remove a worktree. Refuses if uncommitted changes exist."""
    name: str = Field(description="The name of the worktree to remove.")
    discard_changes: bool = Field(default=False,
                                  description="Set to true to force removal even with uncommitted changes.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        return remove_worktree(self.name, self.discard_changes)


class KeepWorktree(BaseTool):
    """Keep a worktree for manual review instead of auto-removing it."""
    name: str = Field(description="The name of the worktree to keep.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        return keep_worktree(self.name)
