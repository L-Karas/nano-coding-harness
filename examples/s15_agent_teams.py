"""
s15: Agent Teams — MessageBus + spawn_teammate_thread + inbox injection.

Run:  python s15_agent_teams/code.py
Need: pip install anthropic python-dotenv + .env with ANTHROPIC_API_KEY

Changes from s14:
  - MessageBus class: file-based mailboxes (.mailboxes/*.jsonl)
  - spawn_teammate_thread: creates teammate in background thread
  - Teammate runs own simplified agent_loop (bash, read, write, send_message)
  - Lead tools: spawn_teammate, send_message, check_inbox (3 new)
  - Lead inbox: teammate messages injected into history (not just printed)
  - Teaching version: teammates limited to 10 rounds (real CC uses idle loop)

ASCII flow:
  Lead: cron_queue → messages → prompt → LLM → TOOLS ────→ loop
                ↑                     ↓                        |
                └── inbox ← MessageBus ← teammate.send_message ←┘
  Teammate: inbox → LLM → bash/read/write/send → loop (max 10 turns)
"""
import json
import threading
import time
from typing import Optional

from config import WORKDIR, client, MODEL
from s14_cron_scheduler import update_context, agent_lock, run_agent_turn_locked, has_cron_queue, execute_tool, \
    should_run_background, start_background_task, consume_cron_queue, get_system_prompt, collect_background_results
from tool_schema import BASE_TOOLS
from tools import BASE_TOOL_HANDLERS

# ── MessageBus (s15 new) ──
# Teaching version uses simple file append + unlink.
# Real CC uses proper-lockfile for concurrent write safety.

MAILBOX_DIR = WORKDIR / ".mailboxes"
MAILBOX_DIR.mkdir(parents=True, exist_ok=True)


class MessageBus:
    """
    File-based message bus. Each agent has a .jsonl inbox.
    Read is destructive: read_text + unlink (consumes messages).
    Teaching version: no file locking; real CC uses proper-lockfile.
    """

    def send(self, from_agent: str, to_agent: str, message: str, message_type: str = "message"):
        msg = {"from_agent": from_agent, "to_agent": to_agent,
               "message": message, "message_type": message_type, "timestamp": time.time()}
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        print(f"  \033[33m[Message Bus] {from_agent} → {to_agent}: {message[:50]}\033[0m")

    def read_inbox(self, agent: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []

        msgs = [json.loads(splitline) for splitline in inbox.read_text(encoding="utf-8").splitlines() if
                splitline.strip()]
        inbox.unlink()
        return msgs


MESSAGE_BUS = MessageBus()

# Track spawned teammates
activate_teammates: dict[str, bool] = {}


# ── Teammate Thread (s15 new) ──
def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    """
    Spawn a teammate agent in a background thread.
    Teaching version: max 10 rounds per teammate.
    Real CC: teammates use idle loop (wait for inbox, work, repeat)
    until shutdown_request.
    """
    if name in activate_teammates:
        return f"Teammate {name} is already spawned."

    system_message = (f"You are '{name}', a {role}. Use tools to complete tasks. "
                      f"When you completed the task, send results via `send_message` to 'lead'.")

    def run():
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]
        sub_tools = BASE_TOOLS + [{
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
        }]
        sub_handlers = (BASE_TOOL_HANDLERS |
                        {"send_message": lambda to_agent, message: (MESSAGE_BUS.send(name, to_agent, message), "Sent")[
                            1]})

        for _ in range(10):
            inbox = MESSAGE_BUS.read_inbox(name)
            if inbox:
                messages.append({
                    "role": "user",
                    "content": f"<inbox>{json.dumps(inbox)}</inbox>",
                })

            try:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=sub_tools,
                    max_tokens=int(8e3)
                )
            except Exception as e:
                break

            if response.choices[0].finish_reason != "tool_calls":
                messages.append({
                    "role": "assistant",
                    "content": response.choices[0].message.content,
                })
                break

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
        MESSAGE_BUS.send(name, "lead", summary, "result")
        activate_teammates.pop(name, None)
        print(f"  \033[32m[Teammate] {name} finished\033[0m")

    activate_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    print(f"  \033[36m[Teammate] {name} spawned as {role}\033[0m")
    return f"Teammate {name} is spawned as {role}"


# ── Team Tool Handlers (s15 new) ──
def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)


def run_send_message(to_agent: str, message: str) -> str:
    MESSAGE_BUS.send("lead", to_agent, message)
    return f"Sent message to {to_agent}"


def run_check_inbox() -> str:
    msgs = MESSAGE_BUS.read_inbox("lead")
    if not msgs:
        return "(Inbox empty)"

    lines = []
    for msg in msgs:
        lines.append(f"  [{msg['from_agent']}] {msg['message'][:200]}")

    return "\n".join(lines)


# ── Tool Definitions ──
LEAD_TOOLS = BASE_TOOLS + [
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
                               "message": {"type": "string"}},
                "required": ["to_agent", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_inbox",
            "description": "Check Lead's inbox for teammate messages.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
HANDLERS = BASE_TOOL_HANDLERS | {
    "spawn_teammate": run_spawn_teammate,
    "send_message": run_send_message,
    "check_inbox": run_check_inbox,
}


def execute_tool(tool_name: str, tool_input: dict) -> str:
    handler = HANDLERS.get(tool_name, None)
    if handler:
        return handler(**tool_input)
    return f"Unknown tool: {tool_name}"


# ── Agent Loop ──
# Teaching code keeps a basic agent loop. S11's full error recovery is omitted.
# Cron queue is consumed when agent_loop is called; real CC auto-wakes via
# queue processor (useQueueProcessor.ts) when items arrive.

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
                tools=LEAD_TOOLS,
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
