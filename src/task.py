"""
Task System
"""
import json
import random
import time
from dataclasses import asdict
from pathlib import Path

from pydantic.dataclasses import dataclass

from config import WORKDIR

TASK_DIR = WORKDIR / ".tasks"
TASK_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_TODOS: list[dict] = []


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    blockedBy: list[str]
    owner: str | None = None
    worktree: str | None = None


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def save_task(task: Task):
    _task_path(task.id).write_text(json.dumps(asdict(task), indent=2, ensure_ascii=False), encoding="utf-8")


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text(encoding="utf-8")))


def list_tasks() -> list[Task]:
    return [Task(**json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(TASK_DIR.glob("task_*.json"))]


def get_task_json(task_id: str) -> str:
    return json.dumps(asdict(load_task(task_id)), indent=2, ensure_ascii=False)


def can_start(task_id: str) -> bool:
    task = load_task(task_id)
    for dep_task_id in task.blockedBy:
        if not _task_path(dep_task_id).exists():
            return False
        if load_task(dep_task_id).status != "completed":
            return False

    return True


def create_task(
        subject: str,
        description: str,
        blockedBy: list[str]
) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(1, 1000):04d}",
        subject=subject,
        description=description,
        status="pending",
        owner=None,
        blockedBy=blockedBy or []
    )
    save_task(task)
    return task


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim."
    if task.owner:
        return f"Task {task_id} already claimed by {task.owner}."

    if not can_start(task_id):
        deps = [dep_task_id for dep_task_id in task.blockedBy
                if _task_path(dep_task_id).exists() and load_task(dep_task_id).status != "completed"]
        missing = [dep_task_id for dep_task_id in task.blockedBy
                   if not _task_path(dep_task_id).exists()]
        parts = []
        if deps:
            parts.append(f"The task blocked by tasks: {deps}")
        if missing:
            parts.append(f"The task has missing tasks: {missing}")
        return f"Cannot start Task {task_id} - " + "\n".join(parts)

    task.owner = owner
    task.status = "in_progress"
    save_task(task)
    print(f"  \033[36m[Claim Task] {owner} claimed task {task_id}, {task.subject} → in_progress\033[0m")
    return f"Claimed Task {task_id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete."
    task.status = "completed"
    save_task(task)

    unblocked_tasks = [f"Task {task.id} ({task.subject})" for task in list_tasks()
                       if task.status == "pending" and task.blockedBy and can_start(task.id)]
    print(f"  \033[32m[Complete Task] {task.owner} completed task {task_id}, ({task.subject}) ✓\033[0m")
    msg = f"Completed Task {task_id} ({task.subject})."
    if unblocked_tasks:
        msg += f"Unblocked Tasks: {'\n'.join(unblocked_tasks)}"

    return msg
