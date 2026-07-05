"""
s17: Autonomous Agents — idle poll + auto-claim + WORK/IDLE lifecycle.

Run:  python s17_autonomous_agents/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s16:
  - scan_unclaimed_tasks: find pending, unowned tasks with deps completed
  - idle_poll: 60s polling loop (inbox + task board), dispatches shutdown in IDLE
  - claim_task: owner check + return value verification
  - Teammate lifecycle: WORK → IDLE → SHUTDOWN
  - Teammate tools: + list_tasks, claim_task, complete_task (5→8)
  - consume_lead_inbox: unified inbox consumer for protocol + context injection
  - Identity re-injection after context compression

ASCII lifecycle:
  WORK: inbox → LLM → tools → (tool_use? loop) → (done? → IDLE)
  IDLE: 5s poll → inbox? → WORK / unclaimed? → claim → WORK / 60s? → SHUTDOWN
"""
import json
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from examples.config import TASK_DIR, WORKDIR, MAILBOX_DIR, client, MODEL, MEMORY_INDEX
from examples.tool_schema import BASE_TOOLS, TASK_TOOLS
from examples.tools import BASE_TOOL_HANDLERS


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: Optional[str]
    blockedBy: list[str]


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def save_task(task: Task):
    _task_path(task.id).write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")


def create_task(subject: str, description: str, blockedBy: Optional[list[str]] = None) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject,
        description=description,
        status="pending",
        owner=None,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def load_task(task_id: str) -> Task:
    return Task(**json.loads(_task_path(task_id).read_text(encoding="utf-8")))


def list_tasks() -> list[Task]:
    return [Task(**json.loads(path.read_text(encoding="utf-8"))) for path in TASK_DIR.glob("task_*.json")]


def get_task(task_id: str) -> str:
    task = load_task(task_id)
    return json.dumps(ascii(task), indent=2)


def can_start_task(task_id: str) -> bool:
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
        return f"Task {task_id} not pending, can't claim task"

    if task.owner:
        return f"Task {task_id} already claimed by {task.owner}, can't claim task"

    if not can_start_task(task_id):
        uncompleted_deps = [dep for dep in task.blockedBy if
                            _task_path(dep).exists() and load_task(dep).status != "completed"]
        missing_deps = [dep for dep in task.blockedBy if not _task_path(dep).exists()]
        parts = []
        if uncompleted_deps:
            parts.append(f"Task {task_id} blocked by {', '.join(uncompleted_deps)}")
        if missing_deps:
            parts.append(f"Task {task_id} is missing dependencies: {', '.join(missing_deps)}")
        return f"Can't start task {task_id} - " + ", ".join(parts)

    task.owner = owner
    task.status = "in_progress"
    save_task(task)
    print(f"  \033[36m[Claim] (owner: {owner}) {task.subject} → in_progress\033[0m")

    return f"Claimed task {task_id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is not in progress, can't complete task."
    task.status = "completed"
    save_task(task)
    unblocked = [task.subject for task in list_tasks() if
                 task.status == "pending" and task.blockedBy and can_start_task(task.id)]
    print(f"  \033[32m[Complete] (owner: {task.owner}) {task.subject} ✓\033[0m")
    msg = f"Completed task {task_id} ({task.subject})"
    if unblocked:
        msg += f"\n Unblocked tasks {', '.join(unblocked)}"
    return msg


# ── Prompt Assembly (from s10) ──

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, "
             "create_task, list_tasks, get_task, claim_task, complete_task, "
             "spawn_teammate, send_message, check_inbox, request_shutdown, request_plan, review_plan.",
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


# ── Autonomous Agent (s17 new) ──
IDLE_POLL_INTERVAL_SECS = 5
IDLE_TIMEOUT = 60


def scan_unclaimed_tasks() -> list[dict]:
    """Find pending, unowned tasks with all dependencies completed."""
    unclaimed_tasks = []
    for task_path in sorted(TASK_DIR.glob("task_*.jsonl")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if task.get("status") == "pending" and not task.get("owner") and can_start_task(task.id):
            unclaimed_tasks.append(task)

    return unclaimed_tasks


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
                messages.append({
                    "role": "user",
                    "content": f"<auto-claimed>Task {task.get('id')}: "
                               f"{task.get('subject')}</auto-claimed>"
                })
                print(f"  \033[32m[Idle] {name} auto-claimed: "
                      f"{task['subject']}\033[0m")
                return "work"
            print(f"  \033[33m[Idle] {name} claim failed: {result}\033[0m")

    print(f"  \033[31m[Idle] {name} timeout ({IDLE_TIMEOUT}s)\033[0m")
    return "timeout"


# ── Teammate Thread (from s15 + s16 + s17) ──
def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    if name in _activate_teammates:
        return f"Teammate {name} already exists."

    system = (f"You are '{name}', a '{role}'. "
              f"Use tools to complete tasks. "
              f"You can list and claim tasks from the board. "
              f"Check inbox for protocol messages. "
              f"当 lead 没有分配具体任务时，你可以使用`list_tasks`工具查看是否有代办任务；同样，你也可以等待 lead 分配具体任务给你。"
              f"**注意**：没有任务时，不要做与任务无关的事，你应该优先等待lead分派任务即可。")

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

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks:
                return "No tasks found."
            return "\n".join(f"  {task.id}: {task.subject} [{task.status}]" for task in tasks)

        def _run_claim_task(task_id: str):
            return claim_task(task_id, name)

        def _run_complete_task(task_id: str):
            return complete_task(task_id)

        sub_handlers = BASE_TOOL_HANDLERS | {
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
    print(f"  \033[35m[Protocol] shutdown_request → {teammate} "
          f"({request_id})\033[0m")
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


# ── Basic tool handlers ──
def run_create_task(subject: str, description: str = "", blockedBy: Optional[list[str]] = None) -> str:
    task = create_task(subject, description, blockedBy)
    dependencies = f"  (blockedBy: {', '.join(blockedBy) if blockedBy else ''})"
    print(f"  \033[34m[create] {task.subject}{dependencies}\033[0m")
    return f"Created {task.id}: ({task.subject}). dependencies: {dependencies}."


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks found."
    return "\n".join(f"  {task.id}: {task.subject} [{task.status}]" for task in tasks)


def run_get_task(task_id: str) -> str:
    return get_task(task_id)


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
]

TOOL_HANDLERS = BASE_TOOL_HANDLERS | {
    "create_task": run_create_task, "list_tasks": run_list_tasks, "get_task": run_get_task,
    "claim_task": run_claim_task, "complete_task": run_complete_task,
    "spawn_teammate": run_spawn_teammate, "send_message": run_send_message,
    "check_inbox": run_check_inbox, "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
}


# ── Context ──
def update_context(context: dict, messages: list) -> dict:
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
    print("s17: autonomous agents")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    messages = []
    context = {"memories": ""}
    while True:
        try:
            query = input("\033[36ms17 >> \033[0m")
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
