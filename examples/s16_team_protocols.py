"""
s16: Team Protocols — request-response protocol + request_id + dispatch + state machine.

Run:  python s16_team_protocols/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s15:
  - ProtocolState dataclass (request_id, type, sender, status, created_at)
  - pending_requests dict: tracks in-flight protocol requests
  - dispatch_message: routes incoming messages by type to handlers
  - request_shutdown: Lead sends shutdown protocol request
  - request_plan: Lead asks teammate to submit plan
  - handle_shutdown_request / handle_plan_response: teammate receives & responds
  - match_response: Lead correlates response to request via request_id (with type validation)
  - Teammate idle loop: waits for inbox messages instead of exiting after 10 rounds
  - Unified consume_lead_inbox: protocol routing + injection into history
  - 3 new Lead tools: request_shutdown, request_plan, review_plan
  - 1 new teammate tool: submit_plan

ASCII flow:
  Lead: BUS.send("shutdown_request", {request_id}) ──────→ teammate inbox
  Teammate: dispatch → handler → BUS.send("shutdown_response", {request_id}) ─→ Lead inbox
  Lead: consume_lead_inbox → match_response(request_id) → pending_requests[req_id].status = approved
"""
import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from examples.config import client, MODEL
from examples.s13_background_tasks import get_system_prompt, should_run_background, start_background_task, \
    collect_background_results
from examples.s14_cron_scheduler import has_cron_queue, agent_lock, consume_cron_queue, update_context
from examples.s15_agent_teams import MAILBOX_DIR
from examples.tool_schema import BASE_TOOLS
from examples.tools import BASE_TOOL_HANDLERS


# ── MessageBus (from s15) ──
class MessageBus:
    """
    File-based message bus. Each agent has a .jsonl inbox.
    Read is destructive: read_text + unlink (consumes messages).
    Teaching version: no file locking; real CC uses proper-lockfile.
    """

    def send(self, from_agent: str, to_agent: str, content: str, msg_type: str = "message", metadata: dict = None):
        msg = {"from_agent": from_agent, "to_agent": to_agent,
               "content": content, "msg_type": msg_type, "metadata": metadata or {}, "ts": time.time()}
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        print(f"  \033[33m[Bus] {from_agent} → {to_agent}: ({msg_type}) {content[:50]}\033[0m")

    def read_inbox(self, agent: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [json.loads(splitline) for splitline in inbox.read_text(encoding="utf-8").splitlines() if
                splitline.strip()]
        inbox.unlink()
        return msgs


BUS = MessageBus()
activate_teammates: dict[str, bool] = {}


# ── Protocol State (s16 new) ──
@dataclass
class ProtocolState:
    request_id: str
    type: str  # "shutdown" | "plan_approval"
    sender: str
    target: str
    status: str  # pending | approved | rejected
    payload: str  # plan text or shutdown reason
    created_at: float = field(default_factory=time.time)


pending_requests: dict[str, ProtocolState] = {}


def new_request_id() -> str:
    return f"req_{random.randint(0, 10000):06d}"


def match_response(response_type: str, request_id: str, approve: bool):
    """
    Correlate a response to the original request via request_id.
    Validates that response_type matches the request type.
    """
    state = pending_requests.get(request_id, None)
    if not state:
        print(f"  \033[31m[Protocol] unknown request_id: {request_id}\033[0m")
        return

    # Validate response type matches request type
    if state.type == "shutdown" and response_type != "shutdown_response":
        print(f"  \033[31m[Protocol] type mismatch: expected shutdown_response, "
              f"got {response_type}\033[0m")
        return

    if state.type == "plan_approve" and response_type != "plan_approve_response":
        print(f"  \033[31m[Protocol] type mismatch: expected plan_approval_response, "
              f"got {response_type}\033[0m")
        return

    if state.status != "pending":
        print(f"  \033[33m[Protocol] {request_id} already {state.status}, "
              f"ignoring duplicate\033[0m")
        return

    state.status = "approved" if approve else "rejected"
    icon = "✓" if approve else "✗"
    color = "32" if approve else "31"
    print(f"  \033[{color}m[Protocol] {state.type} [{icon}] "
          f"({request_id}: {state.status})\033[0m")


# ── Unified Lead Inbox Consumer (s16 fix) ──
# Both check_inbox tool and main loop call this function.
# Protocol responses are routed via match_response before returning.

def consume_lead_inbox(route_protocol: bool = True) -> list[dict]:
    """
    Read Lead's inbox. Route protocol responses, return all messages.
    Called by both run_check_inbox() and main loop to avoid
    messages being consumed without protocol routing.
    """
    msgs = BUS.read_inbox("lead")
    if not msgs:
        return []

    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("msg_type", "")
            if req_id and msg_type.endswith("_response"):
                approve = meta.get("approve", False)
                match_response(msg_type, req_id, approve)
    return msgs


# ── Teammate Thread (s16: idle loop + dispatch) ──

def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    """
    Spawn a teammate agent in a background thread.
    Uses idle loop: after each LLM turn, waits for inbox messages
    (shutdown_request, new task) instead of exiting.
    """
    if name in activate_teammates:
        return f"Teammate {name} is already running."

    system = (f"You are '{name}', a {role}. "
              f"Use tools to complete tasks. "
              f"Check inbox for protocol messages (shutdown_request, etc)."
              f"When you completed the task, send results via `send_message` to 'lead'.")

    def handle_inbox_message(name: str, msg: dict, messages: list) -> bool:
        """
        Dispatch incoming protocol messages by msg_type.
        Returns True if teammate should stop.
        """
        msg_type = msg.get("msg_type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")

        if msg_type == "shutdown_request":
            BUS.send(name, "lead", "Shutting down gracefully.",
                     "shutdown_response", {"request_id": req_id, "approve": True})
            print(f"  \033[35m[Protocol] {name} approved shutdown ({req_id})\033[0m")
            return True

        if msg_type == "plan_approve_response":
            approve = meta.get("approve", False)
            if approve:
                messages.append({
                    "role": "user", "content": f"[Plan approved] Process with the task."
                })
            else:
                messages.append({
                    "role": "user", "content": f"[Plan rejected] Feedback: {msg['content']}."
                })
            return True

        return False

    def run():
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        sub_tools = BASE_TOOLS + [
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send a message to another agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to_agent": {"type": "string"},
                            "message": {"type": "string"},
                        },
                        "required": ["to_agent", "message"],
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
        ]
        sub_handlers = BASE_TOOL_HANDLERS | {
            "send_message": lambda to_agent, message: (BUS.send(name, to_agent, message), "Sent")[1],
            "submit_plan": lambda plan: _teammate_submit_plan(name, plan),
        }

        shutdown_requested = False
        while not shutdown_requested:
            # check inbox for protocol messages
            inbox = BUS.read_inbox(name)
            should_stop = False
            non_protocol = []
            for msg in inbox:
                if msg.get("msg_type") in ("shutdown_request", "plan_approve_response"):
                    should_stop = handle_inbox_message(name, msg, inbox)
                    if should_stop:
                        break
                else:
                    non_protocol.append(msg)

            if should_stop:
                shutdown_requested = True
                break

            if non_protocol:
                inbox_json = json.dumps(non_protocol, ensure_ascii=False)
                messages.append({"role": "user", "content": f"<inbox>{inbox_json}</inbox>"})

            try:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=sub_tools,
                    extra_body={"enable_thinking": True},
                    max_tokens=int(8e3)
                )
            except Exception as e:
                break

            if response.choices[0].finish_reason != "tool_calls":
                # Idle: wait for inbox messages instead of exiting
                # Real CC sends idle_notification to Lead here
                messages.append({
                    "role": "assistant",
                    "content": response.choices[0].message.content
                })
                while not shutdown_requested:
                    time.sleep(1)
                    inbox = BUS.read_inbox(name)
                    for msg in inbox:
                        if msg.get("msg_type") in ("shutdown_request", "plan_approval_response"):
                            should_stop = handle_inbox_message(name, msg, inbox)
                            if should_stop:
                                shutdown_requested = True
                                break
                        else:
                            non_protocol.append(msg)

                    if shutdown_requested:
                        break

                    if non_protocol:
                        inbox_json = json.dumps(non_protocol, ensure_ascii=False)
                        messages.append({"role": "user", "content": f"<inbox>{inbox_json}</inbox>"})
                        break
                continue

            # Execute tool calls
            tool_results = []
            tool_call_message = [{"role": "assistant", "tool_calls": []}]
            for tool_call in response.choices[0].message.tool_calls:
                tool_call_id = tool_call.id
                tool_name = tool_call.function.name
                tool_input = json.loads(tool_call.function.arguments)

                handler = sub_handlers.get(tool_name, None)
                output = handler(**tool_input) if handler else f"Unknown Tool: {tool_name}"
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(output),
                })
                tool_call_message[0]["tool_calls"].append({
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": tool_input,
                    }
                })

            messages.extend(tool_call_message + tool_results)

        # Send final summary to Lead
        summary = "Done."
        if messages[-1]["role"] == "assistant":
            summary = messages[-1]["content"]
        BUS.send(name, "lead", summary, "result")
        activate_teammates.pop(name, None)
        print(f"  \033[32m[Teammate] {name} finished\033[0m")

    activate_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    print(f"  \033[36m[Teammate] {name} spawned as {role}\033[0m")
    return f"Teammate '{name}' spawned as {role}."


def _teammate_submit_plan(from_agent: str, plan: str) -> str:
    """
    Teammate submits a plan to Lead for approval.

    Note: This is a protocol-level request, not a code-level gate.
    After submitting, the teammate's thread continues running — it can
    still call bash/write/etc. Real enforcement relies on the model
    waiting for the approval response before acting. Code-level tool
    gating would require blocking the teammate's tool dispatch until
    approval arrives.
    """
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id,
        type="plan_approval",
        sender=from_agent,
        target="lead",
        status="pending",
        payload=plan,
    )
    BUS.send(from_agent, "lead", plan, "plan_approval_request", {"request_id": req_id})
    return f"Plan submitted ({req_id}). Waiting for approval..."


# ── Lead Protocol Tools (s16 new) ──
def run_request_shutdown(teammate: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id,
        type="shutdown",
        sender="lead",
        target=teammate,
        status="pending",
        payload="",
    )
    BUS.send("lead", teammate, "Please shut down gracefully.",
             "shutdown_request", {"request_id": req_id})
    print(f"  \033[35m[Protocol] shutdown_request → {teammate} ({req_id})\033[0m")
    return f"Shutdown request sent to {teammate} (request_id={req_id})."


def run_request_plan(teammate: str, task: str) -> str:
    """Lead asks a teammate to submit a plan for a task."""
    BUS.send("lead", teammate, f"Please submit a plan for: {task}")
    return f"Asked {teammate} to submit a plan for: {task}."


def run_review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found."

    if state.status != "pending":
        return f"Request {request_id} already {state.status}."

    state.status = "approved" if approve else "rejected"
    BUS.send("lead", state.sender, feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_response", {"request_id": request_id, "approved": approve})
    icon = "✓" if approve else "✗"
    print(f"  \033[32m[Protocol] plan {icon} ({request_id})\033[0m")
    return f"Plan {'approved' if approve else 'rejected'} ({request_id})."


def run_spawn_teammate(name: str, role: str, prompt: str = "") -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to_agent: str, content: str) -> str:
    BUS.send("lead", to_agent, content)
    return f"Sent to {to_agent}."


def run_check_inbox() -> str:
    """Check Lead's inbox. Routes protocol responses via match_response."""
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return f"No messages received."

    lines = []
    for msg in msgs:
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f"  [{msg['msg_type']} req: {req_id}]" if req_id else f"  [{msg['msg_type']}]"
        lines.append(f"  [{msg['from_agent']}]{tag} {msg['content'][:200]}")

    return "\n".join(lines)


# ── Tool Dispatch ──
def execute_tool(tool_name: str, tool_input: dict) -> str:
    handler = (BASE_TOOL_HANDLERS | {
        "spawn_teammate": run_spawn_teammate, "send_message": run_send_message, "check_inbox": run_check_inbox,
        "request_shutdown": run_request_shutdown, "request_plan": run_request_plan, "review_plan": run_review_plan,
    }).get(tool_name, None)
    if handler:
        return handler(**tool_input)

    return f"No such tool: {tool_name}."


# ── Tool Definitions ──
TOOLS = BASE_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "spawn_teammate",
            "description": "Spawn a teammate agent in a background thread.",
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
                extra_body={"enable_thinking": True},
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
    print("s15: agent teams")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    messages = []
    context = update_context({}, [])
    while True:
        try:
            query = input("\033[36ms14 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        with agent_lock:
            run_agent_turn_locked(query)
