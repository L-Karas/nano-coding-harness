"""
s14: Cron Scheduler — independent daemon thread + queue processor.

Run:  python s14_cron_scheduler/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s13:
  - CronJob dataclass (id, cron, prompt, recurring, durable)
  - cron_matches: 5-field cron expression matching with DOM/DOW OR semantics
  - schedule_job / cancel_job: register/remove cron jobs (with validation)
  - cron_scheduler_loop: independent daemon thread, polls every 1s
  - cron_queue: thread-safe queue, scheduler writes, queue processor delivers
  - queue_processor_loop: auto-runs agent_loop when cron_queue has work
  - Durable storage: .scheduled_tasks.json (survives restart)
  - 3 new tools: schedule_cron, list_crons, cancel_cron

Four layers:
  1. Scheduler: daemon thread checks time → fires matching jobs
  2. Queue: cron_queue decouples scheduler from agent loop
  3. Queue processor: wakes the agent when queued work exists and it is idle
  4. Consumer: agent_loop consumes queued jobs and injects them into messages
"""
import json
import random
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from config import TASK_DIR, WORKDIR, MEMORY_INDEX, client, MODEL
from s13_background_tasks import TASK_TOOL_HANDLERS, TASK_TOOLS

TOOLS = TASK_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "schedule_cron",
            "description": "Schedule a cron job. cron is 5-field: min hour dom month dow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cron_expression": {"type": "string",
                                        "description": "5-field cron expression"},
                    "prompt": {"type": "string",
                               "description": "Message to inject when fired"},
                    "recurring": {"type": "boolean",
                                  "description": "True=recurring, False=one-shot"},
                    "durable": {"type": "boolean",
                                "description": "True=persist to disk"}
                },
                "required": ["cron", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_crons",
            "description": "List all registered cron jobs.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_cron",
            "description": "Cancel a cron job by ID.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"]
            },
        },
    },
]


# Task System

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
    "tools": "Available tools: bash, read_file, write_file, create_task, list_tasks, get_tasks, claim_task, complete_task.",
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

    return any(kw in cmd for kw in slow_keywords)


def should_run_background(tool_name: str, tool_input: dict) -> bool:
    if tool_input.get("run_in_background", ""):
        return True
    return is_slow_operation(tool_name, tool_input)


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


# ---- Cron Scheduler (s14 new) ----
DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"


@dataclass
class CronJob:
    id: str
    cron_expression: str  # "0 9 * * *"
    prompt: str  # message to inject when fired
    recurring: bool  # True = recurring, False = one-shot
    durable: bool  # True = persist to disk


scheduled_jobs: dict[str, CronJob] = {}
cron_queue: list[CronJob] = []
cron_lock = threading.Lock()
agent_lock = threading.Lock()
_lasy_fired: dict[str, str] = {}


def _cron_field_matches(field: str, value: int):
    """Match a single cron field against a value."""
    if field == "*":
        return True

    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0

    if "," in field:
        return any(_cron_field_matches(f.strip(), value) for f in field.strip(","))

    if "-" in field:
        l, h = field.split("-", 1)
        return int(l) <= value <= int(h)

    return value == int(field)


def cron_matches(cron_expression: str, dt: datetime) -> bool:
    """
    Check if a 5-field cron expression matches the given datetime.
    Standard cron semantics: DOM and DOW use OR when both are constrained.
    """
    fields = cron_expression.strip().split()
    if len(fields) != 5:
        return False

    minute, hour, dom, month, dow = fields
    # Python Monday=0 → cron Sunday=0
    dow_val = (dt.weekday() + 1) % 7

    m = _cron_field_matches(minute, dt.minute)
    h = _cron_field_matches(hour, dt.hour)
    dom_ok = _cron_field_matches(dom, dt.day)
    month_ok = _cron_field_matches(month, dt.month)
    dow_ok = _cron_field_matches(dow, dow_val)

    # Minute, hour, month must all match
    if not (m and h and month_ok):
        return False

    # DOM and DOW: if both constrained, either matching is enough (OR)
    dom_constrained = dom == "*"
    dow_constrained = dow == "*"
    if dow_constrained and dom_constrained:
        return True
    if dom_constrained:
        return dow_ok
    if dow_constrained:
        return dom_ok
    return dom_ok or dow_ok


def _validate_cron_field(field: str, low: int, high: int) -> Optional[str]:
    """Validate a single cron field value is within [low, high]."""
    if field == "*":
        return None

    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit():
            return f"[Invalid step] {field}"
        step = int(step)
        if step <= 0:
            return f"[Step must be positive] {field}"
        return None

    if "," in field:
        for part in field.split(","):
            err = _validate_cron_field(part.strip(), low, high)
            if err:
                return err
        return None

    if "-" in field:
        parts = field.split("-")
        if not parts[0].isdigit() or not parts[1].isdigit():
            return f"[Invalid cron expression] {field}"
        start, end = int(parts[0]), int(parts[1])
        if start < low or start > high or end < low or end > high:
            return f"[Invalid cron expression] Range {field} out of bounds [{low}, {high}]"
        if start > high:
            return f"[Invalid cron expression] start > end: {field}"
        return None

    if not field.isdigit():
        return f"[Invalid cron expression] {field}"

    val = int(field)
    if val < low or val > high:
        return f"[Invalid cron expression] Value {val} out of bounds [{low}, {high}]"
    return None


def validate_cron(cron_expression: str) -> Optional[str]:
    """Validate a cron expression. Returns error message or None."""
    fields = cron_expression.strip().split()
    if len(fields) != 5:
        return f"Expected 5 fields, got {len(fields)}"

    bounds = [(0, 59), (2, 23), (1, 31), (1, 12), (0, 6)]
    names = ["minute", "hour", "dom", "month", "dow"]
    for i, (field, (low, high), name) in enumerate(zip(fields, bounds, names)):
        err = _validate_cron_field(field, low, high)
        if err:
            return f"{name}: {err}"

    return None


def save_durable_jobs():
    """Persist durable jobs to .scheduled_tasks.json."""
    durable = [asdict(job) for job in scheduled_jobs.values() if job.durable]
    DURABLE_PATH.write_text(json.dumps(durable, indent=2))


def load_durable_jobs():
    """Load durable jobs from disk on startup."""
    if not DURABLE_PATH.exists():
        return
    try:
        jobs = json.loads(DURABLE_PATH.read_inbox())
        for job in jobs:
            cron_job = CronJob(**job)
            err = validate_cron(cron_job.cron_expression)
            if err:
                print(f"  \033[31m[cron] skipping invalid job {job.id}: {err}\033[0m")
                continue
            scheduled_jobs[cron_job.id] = cron_job
        valid = [job for job in jobs if job["id"] in scheduled_jobs]
        if valid:
            print(f"  \033[35m[cron] loaded {len(valid)} durable job(s)\033[0m")

    except Exception as e:
        pass


def schedule_job(cron_expression: str, prompt: str, recurring: bool = True,
                 durable: bool = False) -> Optional[CronJob]:
    """Register a new cron job. Returns CronJob or error string."""
    err = validate_cron(cron_expression)
    if err:
        return err
    job = CronJob(
        id=f"cron-{random.randint(0, 99999):06d}",
        cron_expression=cron_expression,
        prompt=prompt,
        recurring=recurring,
        durable=durable,
    )
    with cron_lock:
        scheduled_jobs[job.id] = job

    if durable:
        save_durable_jobs()
    print(f"  \033[35m[cron register] {job.id} '{cron_expression}' → {prompt[:40]}\033[0m")

    return job


def cancel_job(job_id: str) -> str:
    """Cancel an existing cron job."""
    with cron_lock:
        job = scheduled_jobs.pop(job_id, None)

    if not job:
        return f"Job {job_id} not found"

    if job.durable:
        save_durable_jobs()

    print(f"  \033[31m[Cron cancel] {job_id}\033[0m")
    return f"Canceled job {job_id}"


def cron_scheduler_loop():
    """
    Independent daemon thread: poll every 1s, fire matching jobs.
    Individual job errors are caught to prevent one bad job from
    killing the entire scheduler thread.
    """
    while True:
        time.sleep(1)
        now = datetime.now()
        # Date-aware marker prevents daily jobs from skipping on day 2+
        minute_marker = now.strftime("%Y-%m-%d %H:%M")
        with cron_lock:
            for job in list(scheduled_jobs.values()):
                try:
                    if cron_matches(job.cron_expression, now):
                        if _lasy_fired.get(job.id) != minute_marker:
                            cron_queue.append(job)
                            _lasy_fired[job.id] = minute_marker
                            print(f"  \033[35m[cron fire] {job.id} → {job.prompt[:40]}\033[0m")
                        if not job.recurring:
                            scheduled_jobs.pop(job.id, None)
                            if job.durable:
                                save_durable_jobs()

                except Exception as e:
                    print(f"  \033[31m[cron error] {job.id}: {e}\033[0m")


def consume_cron_queue() -> list[CronJob]:
    """Consume fired jobs from cron_queue (called by agent_loop)."""
    with cron_lock:
        fired = list(cron_queue)
        cron_queue.clear()
    return fired


def has_cron_queue() -> bool:
    """Return whether fired cron jobs are waiting to be delivered."""
    with cron_lock:
        return bool(cron_queue)


# Load durable jobs on startup, then start scheduler thread
load_durable_jobs()
threading.Thread(target=cron_scheduler_loop, daemon=True).start()
print("  \033[35m[cron] Scheduler thread started\033[0m")


# ---- Cron Tools ----
def run_schedule_cron(cron_expression: str, prompt: str, recurring: bool = True, durable: bool = True) -> str:
    result = schedule_job(cron_expression, prompt, recurring, durable)
    if isinstance(result, str):
        return f"[Error] {result}"

    return f"Scheduled job {result.id}: '{result.cron_expression}' -> '{result.prompt}'"


def run_list_crons() -> str:
    with cron_lock:
        jobs = list(scheduled_jobs.values())

    if not jobs:
        return f"No cron jobs scheduled, Use schedule_cron to add one."

    lines = []
    for job in jobs:
        tag = "recurring" if job.recurring else "one-shot"
        dur = "durable" if job.durable else "session"
        lines.append(f"  {job.id}: '{job.cron_expression}' -> '{job.prompt[:40]}' "
                     f"[{tag}, {dur}]")
    return "\n".join(lines)


def run_cancel_cron(job_id: str) -> str:
    return cancel_job(job_id)


def execute_tool(tool_name: str, tool_input: dict) -> str:
    handlers = TASK_TOOL_HANDLERS | {"schedule_cron": run_schedule_cron, "list_crons": run_list_crons,
                                     "cancel_cron": run_cancel_cron}
    handler = handlers.get(tool_name, "")
    if handler:
        return handler(**tool_input)
    return f"Unknown tool: {tool_name}"


# ----Context----
def update_context(context: dict, messages: list) -> dict:
    """Derive context from real state."""
    memories = ""
    if MEMORY_INDEX.exists():
        content = MEMORY_INDEX.read_text(encoding="utf-8").strip()
        if content:
            memories = content

    return {
        "enabled_tools": [tool["function"]["name"] for tool in TOOLS],
        "workspace": str(WORKDIR),
        "memories": memories,
    }


# ── Agent Loop (simplified, focused on cron scheduler) ──
# Teaching code keeps a basic agent loop. S11's full error recovery is omitted.
# cron_scheduler_loop produces work; queue_processor_loop wakes this loop when
# queued work exists and no other agent turn is running.

def agent_loop(messages: list, context: dict) -> dict:
    while True:
        # Layer 4: consume fired cron jobs → inject as messages
        fired = consume_cron_queue()
        for job in fired:
            messages.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
            print(f"  \033[35m[inject cron] {job.id} {job.prompt[:50]}\033[0m")

        try:
            system = [{"role": "system", "content": get_system_prompt(context)}]
            response = client.chat.completions.create(
                model=MODEL,
                messages=system + messages,
                tools=TOOLS,
                max_tokens=int(8e3)
            )
        except Exception as e:
            messages.append({
                "role": "assistant",
                "content": f"[Error] {type(e).__name__}: {str(e)}"
            })
            return context

        if response.choices[0].finish_reason != "tool_calls":
            messages.append({
                "role": "assistant",
                "content": response.choices[0].message.content
            })
            return context

        tool_results = []
        tool_call_messages = [{"role": "assistant", "tool_calls": []}]
        for tool_call in response.choices[0].message.tool_calls:
            tool_call_id = tool_call.id
            tool_name = tool_call.function.name
            tool_input = json.loads(tool_call.function.arguments)
            tool_call_messages[0]["tool_calls"].append({
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": tool_input,
                }
            })
            print(f"\033[36m> [Call tool] {tool_name}\033[0m")
            print(f"\033[36m> [Tool arguments] {tool_input}\033[0m")

            if should_run_background(tool_name, tool_input):
                bg_id = start_background_task(tool_name, tool_input, tool_call_id)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"[Background task {bg_id} started] Command: {tool_input.get('command', '')}. "
                               f"Result will be available when the task completed."
                })
            else:
                output = execute_tool(tool_name, tool_input)
                print(f"------------\n[Tool Result] {str(output[:100])}...\n------------")
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(output)
                })

        # Merge background tool results + notifications into one user message
        user_content = tool_results
        bg_notifications = collect_background_results()
        if bg_notifications:
            for bg_notification in bg_notifications:
                user_content.append({
                    "role": "user",
                    "content": bg_notification
                })

        messages.extend(tool_call_messages + user_content)
        context = update_context(context, messages)


session_history: list = []
session_context = update_context({}, [])


# todo
def print_latest_assistant_text(messages: list):
    """Print text blocks from the latest assistant message."""
    if not messages:
        return

    msg = messages[-1]
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return

    content = msg.get("content", "")
    if isinstance(content, str):
        print(content)
        return


def run_agent_turn_locked(user_query: Optional[str] = None):
    """Run one agent turn. Caller must hold agent_lock."""
    global session_context
    if user_query is not None:
        session_history.append({"role": "user", "content": user_query})
    session_context = agent_loop(session_history, session_context)
    session_context = update_context(session_context, session_history)
    print_latest_assistant_text(session_history)
    print()


def queue_processor_loop():
    """Auto-deliver fired cron jobs when the agent is idle."""
    global session_context
    while True:
        time.sleep(1)
        if not has_cron_queue():
            continue
        if not agent_lock.acquire(blocking=False):
            continue
        try:
            if not has_cron_queue():
                continue
            print("\n  \033[35m[Queue processor] delivering scheduled work\033[0m")
            run_agent_turn_locked()
        finally:
            agent_lock.release()


if __name__ == '__main__':
    print("s14: cron scheduler")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    threading.Thread(target=queue_processor_loop, daemon=True).start()
    print("  \033[35m[Queue processor] started\033[0m")

    while True:
        try:
            query = input("\033[36ms14 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        with agent_lock:
            run_agent_turn_locked(query)
