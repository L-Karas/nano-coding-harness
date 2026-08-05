"""
Base Tool Handlers
"""
from typing import Optional

from src import message_bus
from src.base_tools import run_bash, run_read, run_write, run_edit, run_glob, run_todo_write
from src.cron_scheduler import run_list_crons, run_cancel_cron, run_schedule_cron
from src.protocol_state import consume_lead_inbox, run_request_shutdown, run_request_plan, run_review_plan
from src.skills import load_skill
from src.sub_agent import spawn_subagent
from src.task import create_task, list_tasks, get_task_json, claim_task, complete_task
from src.teammates import spawn_teammate_thread
from src.worktree import create_worktree, remove_worktree, keep_worktree


def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)


def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)


def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)


def run_create_task(subject: str, description: str, blockedBy: Optional[list[str]] = None) -> str:
    task = create_task(subject, description, blockedBy)
    dependencies = f" (Blocked by tasks: {', '.join(blockedBy) if blockedBy else ''})"
    print(f"  \033[34m[Create Task] {task.subject}{dependencies}\033[0m")
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


def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to_agent: str, content: str) -> str:
    message_bus.MESSAGE_BUS.send("lead", to_agent, content)
    return f"Sent message to {to_agent}"


def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(Inbox empty)"

    lines = []
    for msg in msgs:
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")
        tag = f" [{msg['msg_type']} request_id: {request_id}]" if request_id else f" [{msg['msg_type']}]"
        lines.append(f"  [{msg['from_agent']}]{tag} {msg['content'][:500]}")

    return "\n".join(lines)


# todo: connect mcp server
def run_connect_mcp(name: str) -> str:
    return "mcp tool can't work."


BUILTIN_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
    "todo_write": run_todo_write, "task": spawn_subagent,
    "load_skill": load_skill,
    "create_task": run_create_task, "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task, "complete_task": run_complete_task,
    "schedule_cron": run_schedule_cron,
    "list_crons": run_list_crons,
    "cancel_cron": run_cancel_cron,
    "spawn_teammate": run_spawn_teammate,
    "send_message": run_send_message, "check_inbox": run_check_inbox,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
    "connect_mcp": run_connect_mcp,
}
