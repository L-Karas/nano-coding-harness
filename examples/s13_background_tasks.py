"""
s13: Background Tasks — thread-based async execution + notification injection.

Run:  python s13_background_tasks/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s12:
  - threading.Thread for background execution
  - background_tasks dict for lifecycle tracking (bg_id, command, status)
  - background_results dict + threading.Lock for thread-safe storage
  - should_run_background: model explicit request via run_in_background param
  - is_slow_operation: fallback heuristic when model doesn't specify
  - start_background_task: dispatch to daemon thread, return bg task id
  - collect_background_results: gather completed, return as notifications
  - agent_loop: slow ops → background + placeholder, inject notifications
  - Notifications use <task_notification> format, not reused tool_use_id

Note: Teaching code keeps a basic agent loop to stay focused on background
tasks. S11's full error recovery (RecoveryState, backoff, escalation,
reactive compact, fallback model) is omitted.
"""
import json
import random
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal, Optional

from config import WORKDIR, MEMORY_INDEX, client, MODEL
from tool_schema import BASE_TOOLS
from tools import BASE_TOOL_HANDLERS

# Task System
TASK_DIR = WORKDIR / ".task"
TASK_DIR.mkdir(exist_ok=True)


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: Literal["pending", "in_progress", "completed"]
    owner: str | None
    blockedBy: list[str]


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def save_task(task: Task):
    _task_path(task.id).write_text(json.dumps(asdict(task), indent=2))


def create_task(subject: str, description: str = "", blockedBy: Optional[list[str]] = None) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject,
        description=description,
        status="pending",
        owner=None,
        blockedBy=blockedBy or []
    )

    save_task(task)
    return task


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text()))


def list_tasks() -> list[Task]:
    return [Task(**json.loads(p.read_text())) for p in sorted(TASK_DIR.glob("task_*.json"))]


def get_task(task_id: str) -> str:
    """
    Return full details of the task in the JSON file.
    Args:
        task_id:

    Returns:

    """
    task = load_task(task_id)
    return json.dumps(asdict(task), indent=2)


def can_start(task_id: str) -> bool:
    """
    Check if all blockedBy dependencies are completed.
    Missing dependencies are treated as blocked.
    Args:
        task_id:

    Returns:

    """
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(task_id).status != "completed":
            return False

    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    if not can_start(task_id):
        deps = [d for d in task.blockedBy
                if not _task_path(d).exists() or load_task(d).status != "completed"]

        return f"Blocked by tasks: {deps}"

    task.owner = owner
    task.status = "in_progress"
    save_task(task)
    print(f"  \033[36m[claim] {task.subject} → in_progress (owner: {owner})\033[0m")
    return f"Claimed {task.id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete."

    task.status = "completed"
    save_task(task)
    unblocked = [t.subject for t in list_tasks() if t.status == "pending" and t.blockedBy and can_start(t.id)]
    print(f"  \033[32m[complete] {task.subject} ✓\033[0m")

    msg = f"Completed {task.id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked tasks: {', '.join(unblocked)}"
        print(f"  \033[33m[unblocked] {', '.join(unblocked)}\033[0m")

    return msg


PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file. etc",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available."
}


def assemble_system_prompt(context: dict) -> str:
    sections = [
        PROMPT_SECTIONS["identity"],
        PROMPT_SECTIONS["tools"],
        PROMPT_SECTIONS["workspace"]
    ]

    memories = context.get("memories", "")
    if memories:
        sections.append(f"Relevant memories:\n{memories}")

    return "\n\n".join(sections)


_last_context_key, _last_prompt = None, None


def get_system_prompt(context: dict) -> str:
    global _last_context_key, _last_prompt

    key = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)

    if key == _last_context_key and _last_prompt:
        return _last_prompt

    _last_context_key = key
    _last_prompt = assemble_system_prompt(context)

    return _last_prompt


# Task tools

def run_create_task(subject: str, description: str = "", blockedBy: Optional[list[str]] = None) -> str:
    task = Task(subject=subject, description=description, blockedBy=blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks. Use create_task to add some."

    lines = []
    for t in tasks:
        icon = {"pending": "o", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
        deps = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
        owner = f" [{t.owner}]" if t.owner else ""
        lines.append(f"  {icon} {t.id}: {t.subject} [{t.status}]{owner}{deps}")

    return "\n".join(lines)


def run_get_task(task_id: str) -> str:
    try:
        return get_task(task_id)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found"


def run_claim_task(task_id: str) -> str:
    return claim_task(task_id, owner="agent")


def run_complete_task(task_id: str) -> str:
    return complete_task(task_id)


# Task tools schema
TASK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task with optional blockedBy dependencies.",
            "parameters": {
                "type": "object",
                "properties": {"subject": {"type": "string"},
                               "description": {"type": "string"},
                               "blockedBy": {"type": "array", "items": {"type": "string"}}},
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks with status, owner, and dependencies.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Get full details of a specific task by ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Get full details of a specific task by ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim a pending task. Sets owner, changes status to in_progress.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Complete an in-progress task. Reports unblocked downstream tasks.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
]

TOOLS = BASE_TOOLS + TASK_TOOLS

TASK_TOOL_HANDLERS = BASE_TOOL_HANDLERS | {"create_task": run_create_task, "list_tasks": run_list_tasks,
                                           "get_task": run_get_task, "claim_task": run_claim_task,
                                           "complete_task": run_complete_task}


# Context
def update_context(context: dict, messages: list) -> dict:
    memories = ""
    if MEMORY_INDEX.exists():
        content = MEMORY_INDEX.read_inbox(encoding="utf-8").strip()
        if content:
            memories = content

    return {
        "enabled_tools": list(TASK_TOOL_HANDLERS.keys()),
        "workspace": str(WORKDIR),
        "memories": memories
    }


# Background Tasks (new)
_bg_counter = 0
background_tasks: dict[str, dict] = {}
background_results: dict[str, str] = {}
background_lock = threading.Lock()


def is_slow_operation(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False

    cmd = tool_input.get("command", "").lower()
    slow_keywords = ["install", "build", "test", "deploy", "compile", "docker build",
                     "pip install", "npm install", "cargo build", "pytest", "make"]

    return any(cmd in kw for kw in slow_keywords)


def should_run_background(tool_name: str, tool_input: dict) -> bool:
    if tool_input.get("run_in_background"):
        return True
    return is_slow_operation(tool_name, tool_input)


def execute_tool(tool_name: str, tool_input: dict) -> str:
    handler = TASK_TOOL_HANDLERS.get(tool_name, "")
    if handler:
        return handler(**tool_input)
    return f"Unknown tool: {tool_name}"


def start_background_task(tool_name: str, tool_input: dict, tool_call_id: str) -> str:
    global _bg_counter
    _bg_counter += 1
    bg_id = f"bg_{_bg_counter:04d}"
    cmd = tool_input.get("command", tool_name)

    def worker():
        result = execute_tool(tool_name, tool_input)
        with background_lock:
            background_tasks[bg_id]["status"] = "completed"
            background_results[bg_id] = result

    with background_lock:
        background_tasks[bg_id] = {
            "tool_call_id": tool_call_id,
            "command": cmd,
            "status": "running"
        }
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print(f"  \033[33m[background] dispatched {bg_id}: {cmd[:40]}\033[0m")

    return bg_id


def collect_background_results() -> list[str]:
    with background_lock:
        read_ids = [bg_id for bg_id, task in background_tasks.items() if task["status"] == "completed"]

    notifications = []
    for bg_id in read_ids:
        with background_lock:
            task = background_tasks.pop(bg_id)
            output = background_results.pop(bg_id, "")
        summary = output[:100] if len(output) > 100 else output
        notifications.append(
            f"<task_notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            f"  <status>completed</status>\n"
            f"  <command>{summary}</command>\n"
            f"  <summary>{summary}</summary>\n"
            f"</task_notification>"
        )
        print(f"  \033[32m[background done] {bg_id}: "
              f"{task['command'][:40]} ({len(output)} chars)\033[0m")

    return notifications


def agent_loop(messages: list, context: dict):
    while True:
        system_message = [{"role": "system", "content": get_system_prompt(context)}]

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=system_message + messages,
                tools=TASK_TOOLS,
                max_tokens=int(8e3)
            )
        except Exception as e:
            messages.append({
                "role": "assistant", "content": f"[Error] {type(e)}: {e}"
            })
            return

        if response.choices[0].finish_reason != "tool_calls":
            messages.append({
                "role": "assistant",
                "content": response.choices[0].message.content
            })
            return

        results = []
        tool_call_messages = [{"role": "assistant", "tool_calls": []}]
        for tool_call in response.choices[0].message.tool_calls:
            tool_call_id = tool_call.id
            tool_name = tool_call.function.name
            tool_input = json.loads(tool_call.function.arguments)
            tool_call_messages[0]["tool_calls"].append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": tool_call.function.arguments
                }
            })
            print(f"\033[36m> {tool_name}\033[0m")

            if should_run_background(tool_name, tool_input):
                bg_id = start_background_task(tool_name, tool_input, tool_call_id)
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"[Background task {bg_id} started] "
                               f"Command: {tool_input.get('command', '')}. "
                               f"Result will be available when task completed."
                })
            else:
                output = execute_tool(tool_name, tool_input)
                print(f"[Tool Result] {str(output[:100])}...")
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(output)
                })

        user_content = results
        bg_notifications = collect_background_results()
        if bg_notifications:
            for notification in bg_notifications:
                user_content.append({
                    "role": "user",
                    "content": notification
                })
            print(f"  \033[32m[Inject] {len(bg_notifications)} background "
                  f"notification(s)\033[0m")
            print(f"  \033[32m[Inject] {bg_notifications} background "
                  f"notification(s)\033[0m")

        messages.extend(tool_call_messages + user_content)
        context = update_context(context, messages)


if __name__ == '__main__':
    print("s13: background tasks")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    messages = []
    context = update_context({}, [])
    while True:
        try:
            query = input("\033[36ms12 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in {"q", "exit", ""}:
            break

        messages.append({"role": "user", "content": query})
        agent_loop(messages, context)
        response = messages[-1]["content"]

        print(response)
