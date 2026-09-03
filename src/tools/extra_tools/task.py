from typing import Optional

from pydantic import Field
from src.tools.tool_base import BaseTool

from src.task import create_task, list_tasks, get_task_json, claim_task, complete_task


class CreateTask(BaseTool):
    """Create a task for the task system."""
    subject: str = Field(description="Brief title of the task.")
    description: Optional[str] = Field(default=None,
                                       description="Detailed description of what the task involves.")
    blockedBy: list[str] = Field(default_factory=list,
                                 description="List of task IDs that must be completed before this "
                                             "task can start.")

    agent_level: set = {"main"}


class ListTasks(BaseTool):
    """List all tasks with their status, owner, and worktree."""
    agent_level: set = {"main"}


class GetTask(BaseTool):
    """Get full details of a task by its ID."""
    task_id: str = Field(description="The task ID to retrieve details for.")

    agent_level: set = {"main"}


class ClaimTask(BaseTool):
    """Claim a pending task and start working on it."""
    task_id: str = Field(description="The task ID to claim.")

    agent_level: set = {"main"}


class CompleteTask(BaseTool):
    """Complete an in-progress task."""
    task_id: str = Field(description="The task ID to complete.")

    agent_level: set = {"main"}


def run_create_task(subject: str, description: str, blockedBy: Optional[list[str]] = None) -> str:
    task = create_task(subject, description, blockedBy)
    dependencies = f" (Blocked by tasks: {', '.join(blockedBy) if blockedBy else ''})"
    return f"Created {task.id}: {task.subject}{dependencies}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks"

    return "\n".join(f"  {task.id}: {task.subject} [{task.status}]"
                     + f" (worktree: {task.worktree})" if task.worktree else ""
                     for task in tasks)


def run_get_task(task_id: str) -> str:
    try:
        return get_task_json(task_id)
    except FileNotFoundError as e:
        return f"Error: Task {task_id} not found"


def run_claim_task(task_id: str) -> str:
    try:
        return claim_task(task_id, owner="agent")
    except FileNotFoundError as e:
        return f"Error: Task {task_id} not found"


def run_complete_task(task_id: str) -> str:
    try:
        return complete_task(task_id)
    except FileNotFoundError as e:
        return f"Error: Task {task_id} not found"
