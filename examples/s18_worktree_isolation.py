"""
s18: Worktree Isolation — git worktree + task-directory binding + event log.

Changes from s17:
  - Task dataclass gains worktree field (str | None)
  - validate_worktree_name: reject path traversal and illegal chars
  - create_worktree: validate name, git worktree add, optional task binding
  - bind_task_to_worktree: write worktree field only, keep task pending
  - remove_worktree: safety check before force, no auto-complete
  - run_git returns (ok, output), events only on success
  - Teammate tools: + complete_task, run in worktree cwd when bound
  - scan_unclaimed_tasks: uses can_start() for dependency checking
  - idle_poll: checks claim result, dispatches shutdown in IDLE
  - consume_lead_inbox: unified inbox consumer
  - 3 new Lead tools: create_worktree, remove_worktree, keep_worktree

ASCII topology:
  Main repo (/)
    ├── .worktrees/auth/  (branch: wt/auth)  ← Task #1
    ├── .worktrees/ui/    (branch: wt/ui)     ← Task #2
    ├── .tasks/task_xxx.json (worktree: "auth")
    └── .worktrees/events.jsonl
"""
import json
import random
import re
import subprocess
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from examples.config import TASK_DIR, WORKDIR, MAILBOX_DIR, MODEL, client, MEMORY_INDEX
from examples.tool_schema import BASE_TOOLS, TASK_TOOLS


# ── Task System (from s12 + s18 worktree field) ──
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
    worktree: str | None = None


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def save_task(task: Task):
    _task_path(task.id).write_text(json.dumps(asdict(task), indent=2, ensure_ascii=False), encoding="utf-8")


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text(encoding="utf-8")))


def create_task(subject: str, description: str = "", blockedBy: Optional[list[str]] = None) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(1, 1000):04d}",
        subject=subject,
        description=description,
        status="pending",
        owner=None,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def list_tasks() -> list[Task]:
    return [Task(**json.loads(path.read_text(encoding="utf-8"))) for path in sorted(TASK_DIR.glob("task_*.json"))]


def get_task_json(task_id: str) -> str:
    task = load_task(task_id)
    return json.dumps(asdict(task), indent=2, ensure_ascii=False)


def can_start(task_id: str) -> bool:
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status != "completed":
            return False

    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, can't claim"
    if task.owner:
        return f"Task {task_id} already claimed by {task.owner}"
    if not can_start(task_id):
        deps = [dep for dep in task.blockedBy
                if _task_path(dep).exists() and load_task(dep).status != "completed"]
        missing = [dep for dep in task.blockedBy if not _task_path(dep).exists()]
        parts = []
        if deps:
            parts.append(f"blocked by: {deps}")
        if missing:
            parts.append(f"missing dependencies: {missing}")
        return "Can't start - " + ", ".join(parts)

    task.owner = owner
    task.status = "in_progress"
    save_task(task)
    print(f"  \033[36m[Claim] (owner: {owner}) {task.subject} → in_progress\033[0m")

    return f"{owner} claimed task {task_id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is not in progress, can't complete task."
    task.status = "completed"
    save_task(task)
    unblocked = [task.subject for task in list_tasks()
                 if task.status == "pending" and task.blockedBy and can_start(task.id)]
    print(f"  \033[32m[Complete] (owner: {task.owner}) {task.subject} ✓\033[0m")
    msg = f"Completed task {task_id} ({task.subject})"
    if unblocked:
        msg += f"\n Unblocked tasks {', '.join(unblocked)}"
    return msg


# ── Worktree System (s18 new) ──
WORKTREES_DIR = WORKDIR / ".worktrees"
WORKTREES_DIR.mkdir(parents=True, exist_ok=True)

VALID_WT_NAME = re.compile(r"^[a-zA-Z0-9._-]{1,64}")


def validate_worktree_name(worktree_name: str) -> Optional[str]:
    """Return error message if worktree name is invalid."""
    if not worktree_name:
        return "Worktree name can't be empty."
    if worktree_name == "." or worktree_name == "..":
        return f"`{worktree_name}` is not an invalid worktree name."
    if not VALID_WT_NAME.match(worktree_name):
        return (f"Invalid worktree name: `{worktree_name}`, "
                f"only letters, digits, dots, underscores, dashes (1-64 chars).")
    return None


def run_git(args: list[str]) -> tuple[bool, str]:
    """Run git command. Return (ok, output)."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=30
        )
        out = (r.stdout + r.stderr).strip()
        out = out[:5000] if out else "No output."
        return r.returncode == 0, out
    except subprocess.TimeoutExpired as e:
        return False, "Error: git timeout.\n" + str(e)


def log_event(event_type: str, worktree_name: str, task_id: str = ""):
    """Append a lifecycle event to events.jsonl"""
    event = {"type": event_type, "worktree": worktree_name, "task": task_id, "timestamp": time.time()}
    events_file = WORKTREES_DIR / "events.jsonl"
    with open(events_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def bind_task_to_worktree(task_id: str, worktree_name: str):
    """Write worktree field to task. Keep status as pending for auto-claim."""
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)
    print(f"  \033[33m[Bind] {task.subject} → worktree:{worktree_name}. (task id: {task_id})\033[0m")


def create_worktree(name: str, task_id: str = "") -> str:
    """Create a git worktree with a dedicated branch. Optional bind to a task."""
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    path = WORKTREES_DIR / name
    if path.exists():
        return f"Worktree `{name}` already exists at `{path}`."
    ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
    if not ok:
        return f"Git error: {result}."
    if task_id:
        bind_task_to_worktree(task_id, name)
    log_event("create", name, task_id)
    print(f"  \033[33m[Worktree] created: {name} at {path}\033[0m")
    return f"Worktree `{name}` created at {path}."


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    """Count uncommitted files and commits in a worktree."""
    try:
        r1 = subprocess.run(["git", "status", "--porcelain"],
                            cwd=path, capture_output=True, text=True, timeout=10)
        files = len([splitline for splitline in r1.stdout.strip().splitlines() if splitline.strip()])
        r2 = subprocess.run(["git", "log", "'@{push}..HEAD'", "--oneline"],
                            cwd=path, capture_output=True, text=True, timeout=10)
        commits = len([splitline for splitline in r2.stdout.strip().splitlines() if splitline.strip()])
        return files, commits
    except Exception:
        return -1, -1


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    """Remove a worktree. Refuses if uncommitted changes unless discard_changes is True."""
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    path = WORKTREES_DIR / name
    if not path.exists():
        return f"Worktree `{name}` not found."
    if not discard_changes:
        files, commits = _count_worktree_changes(path)
        if files < 0:
            return (f"Can't verify worktree `{name}` status. "
                    f"Use discard_changes=true to force removal.")
        if files > 0 or commits > 0:
            return (f"Worktree `{name}` has {files} uncommitted files "
                    f"and {commits} unpushed commits."
                    f"Use discard_changes=true to force removal."
                    f"or keep_worktree to preserve for review.")

    ok1, _ = run_git(["worktree", "remove", str(path), "--force"])
    if not ok1:
        return f"Failed to remove worktree `{name}`."
    run_git(["branch", "-D", f"wt/{name}"])
    log_event("remove", name)
    print(f"  \033[33m[Worktree Remove] removed: {name}\033[0m")
    return f"Worktree `{name}` removed."


def keep_worktree(name: str) -> str:
    """Keep worktree for manual review. Branch preserved."""
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    log_event("keep", name)
    print(f"  \033[36m[Worktree Keep] kept: {name}\033[0m")
    return f"Worktree `{name}` kept for review (branch: wt/{name})"


# ── Prompt Assembly (from s10) ──
PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, "
             "create_task, list_tasks, get_task, claim_task, complete_task, "
             "spawn_teammate, send_message, check_inbox, request_shutdown, request_plan, review_plan, "
             "create_worktree, remove_worktree, keep_worktree.",
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available."
}
_last_context_hash, _last_prompt = None, None


def assemble_system_prompt(context: dict) -> str:
    sections = list(PROMPT_SECTIONS.values())
    if context.get("memories"):
        sections.append(f"Relevant memories: \n{context['memories']}")

    return "\n\n".join(sections)


def get_system_prompt(context: dict) -> str:
    global _last_context_hash, _last_prompt
    context_hash = json.dumps(context, sort_keys=True)
    if context_hash == _last_context_hash and _last_prompt:
        return _last_prompt
    _last_context_hash = context_hash
    _last_prompt = assemble_system_prompt(context)
    return _last_prompt


# ── Basic Tools ──
def safe_path(path: str, cwd: Optional[Path] = None) -> Path:
    base = cwd or WORKDIR
    path = (base / path).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {path}")
    return path


def run_bash(command: str, cwd: Optional[Path] = None) -> str:
    try:
        res = subprocess.run(
            command,
            shell=True,
            cwd=cwd or WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (res.stdout + res.stderr).strip()
        return output[:5000] if output else "No output."
    except subprocess.TimeoutExpired:
        return f"Error: Timeout (120s)"


def run_read(path: str, limit: Optional[int] = None, cwd: Optional[Path] = None) -> str:
    try:
        lines = safe_path(path, cwd).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit}) more lines."]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str, cwd: Optional[Path] = None) -> str:
    try:
        fp = safe_path(path, cwd)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to `{path}`."
    except Exception as e:
        return f"Error: {e}"


# ── MessageBus (from s15) ──
class MessageBus:
    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        msg = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "content": content,
            "msg_type": msg_type,
            "timestamp": int(time.time()),
            "metadata": metadata or {}
        }
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        print(f"  \033[33m[Message Bus] {from_agent} → {to_agent}: "
              f"({msg_type}) {content[:50]}\033[0m")

    def read_inbox(self, agent: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [splitline for splitline in inbox.read_text(encoding='utf-8').splitlines() if splitline.strip()]
        inbox.unlink()
        return msgs


BUS = MessageBus()
_activate_teammates: dict[str, bool] = {}


# ── Protocol State (from s16) ──
@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default_factory=time.time)


_pending_requests: dict[str, ProtocolState] = {}


def new_request_id() -> str:
    return f"request_{random.randint(1, 1000):06d}"


def match_response(response_type: str, request_id: str, approved: bool):
    """Correlate a response to the original request via request_id."""
    state = _pending_requests.get(request_id)
    if not state:
        print(f"  \033[31m[Protocol] unknown request_id: {request_id}\033[0m")
        return

    if state.type == "shutdown" and response_type != "shutdown_response":
        print(f"  \033[31m[Protocol] type mismatch: expected shutdown_response, "
              f"got {response_type}\033[0m")
        return

    if state.type == "plan_approval" and response_type != "plan_approval_response":
        print(f"  \033[31m[Protocol] type mismatch: expected plan_approval_response, "
              f"got {response_type}\033[0m")
        return

    state.status = "approved" if approved else "rejected"
    icon = "✓" if approved else "✗"
    color = "32" if approved else "31"
    print(f"  \033[{color}m[Protocol] {state.type} {icon} "
          f"({request_id}: {state.status})\033[0m")


# ── Autonomous Agent (from s17, + worktree cwd) ──
IDLE_POLL_INTERVAL_SECS = 5
IDLE_TIMEOUT = 60


def scan_unclaimed_tasks() -> list[dict]:
    """Find pending, unowned tasks with all dependencies completed."""
    unclaimed_tasks = []
    for task_path in sorted(TASK_DIR.glob("task_*.jsonl")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if task.get("status") == "pending" and not task.get("owner") and can_start(task.id):
            unclaimed_tasks.append(task)

    return unclaimed_tasks


def idle_poll(agent_name: str, messages: list[dict], name: str, role: str) -> str:
    """Poll for 60s, Return 'work', 'shutdown', or 'timeout'."""
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL_SECS):
        time.sleep(IDLE_POLL_INTERVAL_SECS)

        # check inbox - dispatch protocol messages first
        inbox = BUS.read_inbox(agent_name)
        if inbox:
            # check for shutdown
            for msg in inbox:
                if msg.get("msg_type") == "shutdown_request":
                    request_id = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(
                        name, "lead", "Shutting down gracefully.",
                        "shutdown_response", {"request_id": request_id, "approve": True}
                    )
                    print(f"  \033[35m[Protocol] {name} approved shutdown in idle ({request_id})\033[0m")
                    return "shutdown"

            # Non-protocol inbox: inject and resume work
            messages.append({
                "role": "user",
                "content": f"<inbox>{json.dumps(inbox)}</inbox>"
            })
            print(f"  \033[36m[Idle] {name} found inbox messages\033[0m")
            return "work"

        # Scan task board
        unclaimed_tasks = scan_unclaimed_tasks()
        if unclaimed_tasks:
            task = unclaimed_tasks[0]
            result = claim_task(task["id"], agent_name)
            if "Claimed" in result:
                worktree_info = ""
                if task.get("worktree"):
                    worktree_path = WORKTREES_DIR / task["worktree"]
                    worktree_info = f"\nWork directory: {worktree_path}\n"
                messages.append({
                    "role": "user",
                    "content": f"<auto-claimed>Task {task.get('id')}: {task.get('subject')}{worktree_info}</auto-claimed>"
                })
                print(f"  \033[32m[Idle] {name} auto-claimed: {task['subject']}\033[0m")
                return "work"
            print(f"  \033[33m[Idle] {name} claim failed: {result}\033[0m")

    print(f"  \033[31m[Idle] {name} timeout ({IDLE_TIMEOUT}s)\033[0m")
    return "timeout"


# ── Teammate Thread (from s15 + s16 + s17 + s18) ──
def _teammate_submit_plan(from_agent: str, plan: str) -> str:
    """Teammate submit a plan to Lead for approval."""
    request_id = new_request_id()
    _pending_requests[request_id] = ProtocolState(
        request_id=request_id,
        type="plan_approval",
        sender=from_agent,
        target="lead",
        status="pending",
        payload=plan,
    )
    BUS.send(from_agent, "lead", plan, "plan_approval_request", {"request_id": request_id})
    return f"Plan submitted ({request_id}). Waiting for approval..."


def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    """Spawn a teammate."""
    if name in _activate_teammates:
        return f"Teammate {name} already activated."

    system = (f"You are '{name}', a {role}. "
              f"Use tools to complete tasks. "
              f"You can list and claim tasks from the board. "
              f"If a task has a worktree, work in that directory.")

    def handle_inbox_message(name: str, msg: dict, messages: list[dict]):
        """Dispatch incoming protocol messages by type."""
        msg_type = msg.get("msg_type", "message")
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")

        if msg_type == "shutdown_request":
            BUS.send(name, "lead", "Shutting down gracefully.",
                     "shutdown_response", {"request_id": request_id, "approve": True})
            print(f"  \033[35m[Protocol] {name} approved shutdown ({request_id})\033[0m")
            return True

        if msg_type == "plan_approval_response":
            approve = metadata.get("approve", False)
            if approve:
                messages.append({
                    "role": "user",
                    "content": "[Plan approved] Proceed with the task."
                })
            else:
                messages.append({
                    "role": "user",
                    "content": f"[Plan rejected] Feedback: {msg.get('content', '')}."
                })
        return False

    def run():
        # Track current worktree for this teammate's cwd
        worktree_ctx = {"path": None}

        def _wt_cwd() -> Optional[Path]:
            p = worktree_ctx["path"]
            return Path(p) if p else None

        def _run_bash(command: str) -> str:
            return run_bash(command, cwd=_wt_cwd())

        def _run_read(path: str) -> str:
            return run_read(path, cwd=_wt_cwd())

        def _run_write(path: str, content: str) -> str:
            return run_write(path, content, cwd=_wt_cwd())

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks:
                return "No tasks found."
            return "\n".join(f"  {task.id}: {task.subject} [{task.status}]"
                             f" (worktree: {task.worktree if task.worktree else ''})" for task in tasks)

        def _run_claim_task(task_id: str):
            result = claim_task(task_id, owner=name)
            if "Claimed" in result:
                # Set worktree cwd if task has one
                task = load_task(task_id)
                if task.worktree:
                    worktree_ctx["path"] = str(WORKTREES_DIR / task.worktree)
                else:
                    worktree_ctx["path"] = None
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            worktree_ctx["path"] = None
            return result

        messages = [{"role": "user", "content": system}]
        sub_tools = BASE_TOOLS + [
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send message to another agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {"to_agent": {"type": "string"},
                                       "content": {"type": "string"}},
                        "required": ["to_agent", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_plan",
                    "description": "Submit a plan for Lead approval.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "plan": {"type": "string"},
                        },
                        "required": ["plan"],
                    },
                },
            },
            # s17 new: teammates can list, claim, and complete tasks
            {
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "description": "List all tasks on the board.",
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
                    "name": "claim_task",
                    "description": "Claim a pending task.",
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
                    "description": "Mark an in-progress task as completed.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
        ]
        sub_handlers = {
            "bash": _run_bash, "read_file": _run_read, "write_file": _run_write,
            "send_message": lambda to_agent, content: (BUS.send(name, to_agent, content), "Sent")[1],
            "submit_plan": lambda plan: _teammate_submit_plan(name, plan),
            "list_tasks": _run_list_tasks,
            "claim_task": _run_claim_task,
            "complete_task": _run_complete_task,
        }

        # Outer loop: WORK + IDLE cycle
        while True:
            # Identity re-injection
            if len(messages) < 3:
                messages.insert(
                    0,
                    {
                        "role": "user",
                        "content": f"<identity>You are '{name}', role: '{role}'."
                                   f"Continue your work.</identity>"
                    }
                )

            # WORK phase
            should_shutdown = False
            for _ in range(10):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    stopped = handle_inbox_message(name, msg, messages)
                    if stopped:
                        should_shutdown = True
                        break

                if should_shutdown:
                    break

                if inbox and not should_shutdown:
                    non_protocol = [msg for msg in inbox if msg.get("msg_type") == "message"]
                    if non_protocol:
                        messages.append({
                            "role": "user",
                            "content": f"<inbox>{json.dumps(non_protocol, ensure_ascii=False)}</inbox>"
                        })

                try:
                    response = client.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=sub_tools,
                        max_tokens=int(8e3),
                        extra_body={"enable_thinking": True},
                    )
                except Exception as e:
                    break

                if response.choices[0].finish_reason != "tool_calls":
                    messages.append({
                        "role": "assistant",
                        "content": response.choices[0].message.content
                    })
                    break

                # handle tool calls
                tool_call_results = []
                tool_call_messages = [{"role": "assistant", "tool_calls": []}]
                for tool_call in response.choices[0].message.tool_calls:
                    tool_call_id = tool_call.id
                    tool_name = tool_call.function.name
                    tool_input = json.loads(tool_call.function.arguments)

                    handler = sub_handlers.get(tool_name)
                    output = handler(**tool_input) if handler else f"Unknown tool: {tool_name}"
                    tool_call_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": str(output)
                    })
                    tool_call_messages[0]["tool_calls"].append({
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tool_input
                        }
                    })
                    print(f"\033[36m>   [Call tool] (Teammate: {name}) {tool_name}\033[0m")
                    print(f"\033[36m>   [Tool arguments] (Teammate: {name}) {tool_input}\033[0m")
                    print(f"\033[36m>   [Tool result] (Teammate: {name}) {output[:20]}\033[0m")

                messages.extend(tool_call_messages + tool_call_results)

            if should_shutdown:
                break

            # IDLE phase
            idle_result = idle_poll(name, messages, name, role)
            if idle_result == "shutdown":
                break
            if idle_result == "timeout":
                break

        # Summary
        summary = f"Teammate {name} done."
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                summary = msg["content"]
                break
        BUS.send(name, "lead", summary, msg_type="result")
        _activate_teammates.pop(name)
        print(f"  \033[32m[Teammate] {name} finished\033[0m")

    _activate_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    print(f"  \033[36m[Teammate] {name} spawned as {role}\033[0m")
    return f"Teammate '{name}' spawned as {role}."


# ── Lead Protocol Tools (from s16) ──
def run_request_shutdown(teammate: str) -> str:
    request_id = new_request_id()
    _pending_requests[request_id] = ProtocolState(
        request_id=request_id,
        type="shutdown",
        sender="lead",
        target=teammate,
        status="pending",
        payload=""
    )
    BUS.send("lead", teammate, "Please shut down gracefully.",
             "shutdown_request", {"request_id": request_id})
    print(f"  \033[35m[Protocol] shutdown_request → {teammate} ({request_id})\033[0m")
    return f"Shutdown request sent to {teammate} ({request_id})."


def run_request_plan(teammate: str, task: str) -> str:
    """Lead asks a teammate to submit a plan."""
    BUS.send("lead", teammate, f"Please submit a plan for: {task}")
    return f"Asked {teammate} to submit a plan ({task})."


def run_review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    state = _pending_requests.get(request_id)
    if not state:
        return f"Request id {request_id} not found."

    if state.status != "pending":
        return f"Request id {request_id} already {state.status}."

    state.status = "approved" if approve else "rejected"
    BUS.send("lead", state.sender, feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_request", {"request_id": request_id, "approve": approve})
    icon = "✓" if approve else "✗"
    print(f"  \033[32m[Protocol] plan {icon} ({request_id})\033[0m")
    return f"Plan {'approved' if approve else 'rejected'} ({request_id})."


# ── Lead Worktree Tools (s18 new) ──
def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)


def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)


def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)


# ── Basic tool handlers ──
def run_create_task(subject: str, description: str = "", blockedBy: Optional[list[str]] = None) -> str:
    task = create_task(subject, description, blockedBy)
    dependencies = f"  (blockedBy: {', '.join(blockedBy) if blockedBy else ''})"
    print(f"  \033[34m[Create Task] {task.subject}{dependencies}\033[0m")
    return f"Created {task.id}: ({task.subject}). dependencies: {dependencies}."


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks found."
    return "\n".join(f"  {task.id}: {task.subject} [{task.status}]"
                     f" (worktree: {task.worktree if task.worktree else ''})" for task in tasks)


def run_get_task(task_id: str) -> str:
    return get_task_json(task_id)


def run_claim_task(task_id: str) -> str:
    return claim_task(task_id, owner="agent")


def run_complete_task(task_id: str) -> str:
    return complete_task(task_id)


def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to_agent: str, content: str) -> str:
    BUS.send("lead", to_agent, content)
    return f"Sent to {to_agent} ({content})."


def consume_lead_inbox(route_protocol=True) -> list[dict]:
    """Read Lead inbox: route protocol responses, return all messages."""
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            metadata = msg.get("metadata", {})
            request_id = metadata.get("request_id", "")
            msg_type = msg.get("msg_type", "")
            if request_id and msg_type.endswith("_response"):
                match_response(msg_type, request_id, metadata.get("approve", False))

    return msgs


def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "No messages received."

    lines = []
    for msg in msgs:
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")
        tag = f"  [{msg['msg_type']} request_id: {request_id}]" if request_id else f"  [{msg['msg_type']}]"
        lines.append(f"  [{msg['from_agent']}]{tag}  {msg['content'][:200]}")

    return "\n".join(lines)


# ── Tool Definitions ──
TOOLS = TASK_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "spawn_teammate",
            "description": "Spawn an autonomous teammate agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "prompt": {"type": "string"}},
                "required": ["name", "role", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message to a teammate via MessageBus.",
            "parameters": {
                "type": "object",
                "properties": {"to_agent": {"type": "string"},
                               "content": {"type": "string"}},
                "required": ["to_agent", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_inbox",
            "description": "Check Lead's inbox. Routes protocol responses automatically.",
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
            "name": "request_shutdown",
            "description": "Request a teammate to shut down gracefully.",
            "parameters": {
                "type": "object",
                "properties": {"teammate": {"type": "string"}},
                "required": ["teammate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_plan",
            "description": "Ask a teammate to submit a plan for review.",
            "parameters": {
                "type": "object",
                "properties": {"teammate": {"type": "string"},
                               "task": {"type": "string"}},
                "required": ["teammate", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_plan",
            "description": "Approve or reject a submitted plan by request_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "feedback": {"type": "string"}
                },
                "required": ["request_id", "approve"],
            },
        },
    },
    # s18 new: worktree tools
    {
        "type": "function",
        "function": {
            "name": "create_worktree",
            "description": "Create an isolated git worktree with its own branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "task_id": {"type": "string"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_worktree",
            "description": "Remove a worktree. Refuses if uncommitted changes unless discard_changes=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "discard_changes": {"type": "boolean"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keep_worktree",
            "description": "Keep a worktree for manual review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
]

TOOL_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "create_task": run_create_task, "list_tasks": run_list_tasks, "get_task": run_get_task,
    "claim_task": run_claim_task, "complete_task": run_complete_task,
    "spawn_teammate": run_spawn_teammate, "send_message": run_send_message,
    "check_inbox": run_check_inbox, "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
    "create_worktree": run_create_worktree, "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
}


# ── Context ──
def update_context(context: dict, messages: list[dict]) -> dict:
    memories = ""
    if MEMORY_INDEX.exists():
        memories = MEMORY_INDEX.read_text(encoding="utf-8")[:2000]
    return {"memories": memories}


# ── Agent Loop ──
def agent_loop(messages: list, context: dict):
    while True:
        context = update_context(context, messages)
        system_message = [{"role": "system", "content": assemble_system_prompt(context)}]
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=system_message + messages,
                tools=TOOLS,
                max_tokens=int(8e3),
                extra_body={"enable_thinking": True},
            )
        except Exception as e:
            messages.append({"role": "system", "content": f"Error: {e}"})
            return

        if response.choices[0].finish_reason != "tool_calls":
            messages.append({
                "role": "assistant",
                "content": response.choices[0].message.content,
            })
            return

        tool_call_messages = [{"role": "assistant", "tool_calls": []}]
        tool_call_results = []
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

            handler = TOOL_HANDLERS.get(tool_name)
            tool_result = handler(**tool_input) if handler else f"Unknown tool: {tool_name}"
            print(f"------------\n[Tool Result] {str(tool_result[:100])}...\n------------")
            tool_call_results.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": str(tool_result),
            })

        messages.extend(tool_call_messages + tool_call_results)


if __name__ == '__main__':
    print("s18: worktree isolation")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    messages = []
    context = {"memories": ""}
    while True:
        try:
            query = input("\033[36ms18 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        messages.append({"role": "user", "content": query})
        agent_loop(messages, context)
        context = update_context(context, messages)
        print(messages[-1]["content"])

        # Consume lead inbox: route protocol + inject into messages
        inbox = consume_lead_inbox()
        if inbox:
            inbox_messages = "\n".join(
                f"From {message['from_agent']} [{message.get('msg_type', 'message')}]: "
                f"{message['content']}"
                for message in inbox
            )
            messages.append({
                "role": "user",
                "content": f"[Inbox]:\n {inbox_messages}]",
            })

        print()
