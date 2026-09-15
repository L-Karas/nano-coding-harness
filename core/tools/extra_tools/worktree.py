from pydantic import Field

from core.tools.tool_base import BaseTool
from core.worktree import create_worktree, remove_worktree, keep_worktree


class CreateWorktree(BaseTool):
    """Create an isolated git worktree for a task."""
    name: str = Field(description="Name for the new worktree.")
    task_id: str | None = Field(default=None, description="Optional task ID to associate with the worktree.")

    agent_type: set = {"main"}


class RemoveWorktree(BaseTool):
    """Remove a worktree. Refuses if uncommitted changes exist."""
    name: str = Field(description="The name of the worktree to remove.")
    discard_changes: bool = Field(default=False,
                                  description="Set to true to force removal even with uncommitted changes.")

    agent_type: set = {"main"}


class KeepWorktree(BaseTool):
    """Keep a worktree for manual review instead of auto-removing it."""
    name: str = Field(description="The name of the worktree to keep.")

    agent_type: set = {"main"}


def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)


def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)


def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)


async def run_create_worktree_async(name: str, task_id: str = "", ctx=None) -> str:
    return create_worktree(name, task_id)


async def run_remove_worktree_async(name: str, discard_changes: bool = False, ctx=None) -> str:
    return remove_worktree(name, discard_changes)


async def run_keep_worktree_async(name: str, ctx=None) -> str:
    return keep_worktree(name)
