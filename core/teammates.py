"""
Teammates
"""
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from core import config, message_bus, protocol_state
from core.base_tools import run_bash, run_read, run_write, call_tool_handler
from core.config import client
from core.protocol_state import get_request_id, ProtocolState
from core.task import TASK_DIR, can_start, claim_task, list_tasks, load_task, complete_task
from core.worktree import WORKTREES_DIR

IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60
ACTIVATE_TEAMMATES: dict[str, bool] = {}

TEAMMATE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read."},
                    "limit": {"type": "integer", "description": "Max lines to read."},
                    "offset": {"type": "integer", "description": "Line offset to start reading from."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to write."},
                    "content": {"type": "string", "description": "Content to write to the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send message to another agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_agent": {"type": "string", "description": "The name of the agent to send the message to."},
                    "content": {"type": "string", "description": "The message content to send."},
                },
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
                    "plan": {"type": "string", "description": "The plan content to submit for approval."},
                },
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks with their status, owner, and worktree.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim a pending task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to claim."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Complete an in-progress task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to complete."},
                },
                "required": ["task_id"],
            },
        },
    },
]


# todo: 使用 Task 类替换 dict
def scan_unclaimed_tasks() -> list[dict]:
    unclaimed_tasks = []
    for task_path in sorted(TASK_DIR.glob("task_*.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if task.get("status") == "pending" and not task.get("owner") and can_start(task.get("id")):
            unclaimed_tasks.append(task)

    return unclaimed_tasks


def idle_poll(
        agent_name: str,
        messages: list[dict],
        name: str,
        role: str,
        worktree_context: Optional[dict] = None
) -> str:
    """
    Autonomous teammates wake up for inbox messages first, then look for
    unclaimed tasks. This keeps direct protocol messages higher priority.
    """
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
        # todo: Message 类替换 dict
        inbox_messages = message_bus.MESSAGE_BUS.read(agent_name)
        if inbox_messages:
            for message in inbox_messages:
                if message.get("msg_type") == "shutdown_request":
                    request_id = message.get("metadata", {}).get("request_id", "")
                    message_bus.MESSAGE_BUS.send(name, "lead", "Shutting down.", "shutdown_response",
                                                 {"request_id": request_id, "approve": True})
                    return "shutdown"

            # todo：优化信息注入
            messages.append({
                "role": "user",
                "content": f"<message_inbox>\n{json.dumps(inbox_messages)}\n</message_inbox>",
            })
            return "work"

        unclaimed_tasks = scan_unclaimed_tasks()
        if unclaimed_tasks:
            task = unclaimed_tasks[0]
            result = claim_task(task.get("id"), agent_name)
            if "claimed" in result.lower():
                wt_info = ""
                if task.get("worktree"):
                    wt_path = WORKTREES_DIR / task.get("worktree")
                    wt_info = f"\nWork directory: {wt_path}"
                    if worktree_context is not None:
                        worktree_context["path"] = str(wt_path)
                messages.append({
                    "role": "user",
                    "content": f"<claimed_task>Task {task.get('id')}: {task.get('subject')} {wt_info}</claimed_task>",
                })
                return "work"

    return "timeout"


def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    if name in ACTIVATE_TEAMMATES:
        return f"Teammate '{name}' already exists."

    # Plan approval is a real gate: after submit_plan, the teammate stops
    # taking model/tool steps until lead sends plan_approval_response.
    protocol_ctx = {"waiting_plan": None}
    system_prompt = (f"You a '{name}', a {role}. Use tools to complete tasks. "
                     f"If a task has worktree, work in that directory.")

    def handle_inbox_message(name: str, msg: dict, messages: list[dict]):
        msg_type = msg.get("msg_type", "message")
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")
        if msg_type == "shutdown_request":
            message_bus.MESSAGE_BUS.send(
                name, "lead", "Shutting down.", "shutdown_response", {"request_id": request_id, "approve": True}
            )
            return True

        if msg_type == "plan_approval_response":
            approve = metadata.get("approve", False)
            if request_id == protocol_ctx["waiting_plan"]:
                protocol_ctx["waiting_plan"] = None
            messages.append({
                "role": "user",
                "content": f"[Plan approved]" if approve else f"[Plan rejected] {msg['content']}",
            })

        return False

    def run():
        wt_ctx = {"work_path": None}

        def _wt_cwd():
            # Once a task with a worktree is claimed, all teammate file tools
            # transparently run inside that isolated directory.
            work_path = wt_ctx["work_path"]
            return Path(work_path) if work_path else None

        def _run_bash(command: str) -> str:
            return run_bash(command, cwd=_wt_cwd())

        def _run_read(path: str) -> str:
            return run_read(path, cwd=_wt_cwd())

        def _run_write(path: str, content: str) -> str:
            return run_write(path, content, cwd=_wt_cwd())

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks:
                return f"No tasks found."
            return "Task List:\n" + "\n".join([
                f"  task {task.id}: {task.subject} [task status: {task.status}]" +
                (f" (task worktree: {task.worktree})" if task.worktree else "")
                for task in tasks
            ])

        def _run_claim_task(task_id: str):
            result = claim_task(task_id, owner=name)
            if "claimed" in result.lower():
                task = load_task(task_id)
                wt_ctx["work_path"] = str(WORKTREES_DIR / task.worktree) if task.worktree else None

            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            wt_ctx["work_path"] = None
            return result

        def _teammate_submit_plan(plan: str) -> str:
            req_id = get_request_id()
            protocol_state.PENDING_REQUESTS[req_id] = ProtocolState(
                request_id=req_id, type="plan_approval", sender=name, target="lead",
                status="pending", payload=plan
            )
            message_bus.MESSAGE_BUS.send(
                name, "lead", plan, "plan_approval_request",
                {"request_id": req_id}
            )
            return f"Plan submitted ({req_id})"

        messages = [{"role": "system", "content": system_prompt}]
        handlers = {
            "base": _run_bash, "read_file": _run_read, "write_file": _run_write,
            "send_message": lambda to_agent, content: (message_bus.MESSAGE_BUS.send(name, to_agent, content), "Sent")[
                1],
            "list_tasks": _run_list_tasks, "claim_task": _run_claim_task, "complete_task": _run_complete_task,
        }

        while True:
            should_shutdown = False
            for _ in range(10):
                inbox_messages = message_bus.MESSAGE_BUS.read(name)
                for msg in inbox_messages:
                    stopped = handle_inbox_message(name, msg, messages)
                    if stopped:
                        should_shutdown = True
                        break

                if should_shutdown:
                    break

                if protocol_ctx["waiting_plan"]:
                    # Poll only for protocol replies while the approval gate is
                    # closed; do not let the model continue with the task.
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue

                if inbox_messages and not should_shutdown:
                    non_protocol = [message for message in inbox_messages if message.get("type") == "message"]
                    if non_protocol:
                        messages.append({
                            "role": "user",
                            "content": "<inbox_message>" + json.dumps(non_protocol,
                                                                      ensure_ascii=False) + "</inbox_message>",
                        })

                try:
                    response = client.chat.completions.create(
                        model=config.SUB_MODEL,
                        messages=messages,
                        tools=TEAMMATE_TOOLS,
                        max_tokens=8000,
                        extra_body={"thinking": {"type": "enabled"}}
                    )
                except Exception:
                    break

                response_message = response.choices[0].message
                if not response_message.tool_calls:
                    messages.append({
                        "role": "assistant",
                        "content": response_message.content
                    })
                else:
                    messages.append(response_message)
                    for tool_call in response_message.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args = json.loads(tool_call.function.arguments)
                        print(f"\033[90m>   [Call tool] (Teammate: {name}) {tool_name}\033[0m")
                        print(f"\033[90m>   [Tool arguments] (Teammate: {name}) {tool_args}\033[0m")

                        if tool_name == "submit_plan":
                            output = _teammate_submit_plan(**tool_args)
                            match = re.search(r"\((req_\d+)\)", output)
                            protocol_ctx["waiting_plan"] = match.group(1) if match else output
                        else:
                            handler = handlers.get(tool_name)
                            output = call_tool_handler(handler, tool_args, tool_name)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(output),
                        })

                        print(f"\033[90m>   [Tool result] (Teammate: {name}) {output[:100]}\033[0m")

                        if protocol_ctx["waiting_plan"]:
                            # Ignore later tool_calls from the same model
                            # response; they belong after approval, not before.
                            break

                if protocol_ctx["waiting_plan"]:
                    break

            if should_shutdown:
                break

            if protocol_ctx["waiting_plan"]:
                continue

            idle_result = idle_poll(name, messages, name, role, wt_ctx)
            if idle_result in ("shutdown", "timeout"):
                break

        summary = "Done."
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg["role"] == "assistant":
                summary = msg["content"]
                break

        message_bus.MESSAGE_BUS.send(name, "lead", summary, "result")
        ACTIVATE_TEAMMATES.pop(name)

    ACTIVATE_TEAMMATES[name] = True
    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}."
