from pydantic import Field

from core.experimental.task import create_task, list_tasks, get_task_json, claim_task, complete_task
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


class ListTasks(BaseTool):
    """List all tasks with their status, owner, and worktree."""
    agent_type: set = {"main", "teammate"}
    experimental: bool = True


class GetTask(BaseTool):
    """Get full details of a task by its ID."""
    task_id: str = Field(description="The task ID to retrieve details for.")

    agent_type: set = {"main"}
    experimental: bool = True


class ClaimTask(BaseTool):
    """Claim a pending task and start working on it."""
    task_id: str = Field(description="The task ID to claim.")

    agent_type: set = {"main", "teammate"}
    experimental: bool = True


class CompleteTask(BaseTool):
    """Complete an in-progress task."""
    task_id: str = Field(description="The task ID to complete.")

    agent_type: set = {"main", "teammate"}
    experimental: bool = True


def run_create_task(subject: str, description: str, blockedBy: list[str] | None = None) -> str:
    task = create_task(subject, description, blockedBy or [])
    dependencies = f" (Blocked by tasks: {', '.join(blockedBy) if blockedBy else ''})"
    return f"Created {task.id}: {task.subject}{dependencies}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks"

    return "\n".join(f"  {task.id}: {task.subject} [{task.status}]"
                     + (f" (worktree: {task.worktree})" if task.worktree else "")
                     for task in tasks)


def run_get_task(task_id: str) -> str:
    try:
        return get_task_json(task_id)
    except FileNotFoundError:
        raise Exception(f"Task {task_id} not found")


def run_claim_task(task_id: str) -> str:
    try:
        return claim_task(task_id, owner="agent")
    except FileNotFoundError:
        raise Exception(f"Task {task_id} not found")


def run_complete_task(task_id: str) -> str:
    try:
        return complete_task(task_id)
    except FileNotFoundError:
        raise Exception(f"Task {task_id} not found")


async def run_create_task_async(subject: str, description: str, blockedBy: list[str] | None = None, ctx=None) -> str:
    return run_create_task(subject, description, blockedBy or [])


async def run_list_tasks_async(ctx=None) -> str:
    return run_list_tasks()


async def run_get_task_async(task_id: str, ctx=None) -> str:
    return run_get_task(task_id)


async def run_claim_task_async(task_id: str, ctx=None) -> str:
    return run_claim_task(task_id)


async def run_complete_task_async(task_id: str, ctx=None) -> str:
    return run_complete_task(task_id)
