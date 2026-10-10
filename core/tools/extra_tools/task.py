from pydantic import Field

from core.experimental.task import create_task, list_tasks, get_task_json, claim_task, complete_task, load_task
from core.experimental.worktree import WORKTREES_DIR
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool


class CreateTask(BaseTool):
    """Create a task for the task system."""
    subject: str = Field(description="Brief title of the task.")
    description: str = Field(description="Detailed description of what the task involves.")
    blockedBy: list[str] = Field(default_factory=list,
                                 description="List of task IDs that must be completed before this "
                                             "task can start.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        task = create_task(self.subject, self.description, self.blockedBy or [])
        dependencies = f" (Blocked by tasks: {', '.join(self.blockedBy) if self.blockedBy else ''})"
        return f"Created {task.id}: {task.subject}{dependencies}"


class ListTasks(BaseTool):
    """List all tasks with their status, owner, and worktree."""
    agent_type: set = {"main", "teammate"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        tasks = list_tasks()
        if not tasks:
            return "No tasks"

        return "\n".join(f"  {task.id}: {task.subject} [{task.status}]"
                         + (f" (worktree: {task.worktree})" if task.worktree else "")
                         for task in tasks)


class GetTask(BaseTool):
    """Get full details of a task by its ID."""
    task_id: str = Field(description="The task ID to retrieve details for.")

    agent_type: set = {"main"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        try:
            return get_task_json(self.task_id)
        except FileNotFoundError:
            raise Exception(f"Task {self.task_id} not found")


class ClaimTask(BaseTool):
    """Claim a pending task and start working on it."""
    task_id: str = Field(description="The task ID to claim.")

    agent_type: set = {"main", "teammate"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        try:
            result = claim_task(self.task_id, owner=tctx.agent_name if tctx else "agent")
        except FileNotFoundError:
            raise Exception(f"Task {self.task_id} not found")
        if tctx and "claimed" in result.lower():
            # 认领带 worktree 的任务后，后续文件工具自动切到该目录（teammate 的上下文由调用方持有）
            task = load_task(self.task_id)
            tctx.cwd = str(WORKTREES_DIR / task.worktree) if task.worktree else None
        return result


class CompleteTask(BaseTool):
    """Complete an in-progress task."""
    task_id: str = Field(description="The task ID to complete.")

    agent_type: set = {"main", "teammate"}
    experimental: bool = True

    def run(self, tctx: ToolContext | None = None) -> str:
        try:
            result = complete_task(self.task_id)
        except FileNotFoundError:
            raise Exception(f"Task {self.task_id} not found")
        if tctx:
            tctx.cwd = None
        return result
