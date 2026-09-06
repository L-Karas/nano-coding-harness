"""
Background tasks

# Slow tools return a placeholder tool_result immediately. Their real output is
# later injected as a task_notification, so the main loop can keep moving.
"""
import json
import threading

from openai.types.chat import ChatCompletionMessageToolCallUnion

from core.tools import call_tool_handler
from core.log import get_logger

BG_COUNTER = 0
BACKGROUND_TASKS: dict[str, dict] = {}
BACKGROUND_RESULTS: dict[str, str] = {}
BACKGROUND_LOCK = threading.Lock()
_LOGER = get_logger(__name__)


def is_slow_operation(tool_name: str, tool_args: dict) -> bool:
    if tool_name != "bash":
        return False

    command = tool_args.get("command", "").lower()
    slow_keywords = ["install", "build", "test", "deploy", "compile", "docker build",
                     "pip install", "cargo build", "pytest", "make"]
    return any(word in command for word in slow_keywords)


def should_run_background(tool_name: str, tool_args: dict) -> bool:
    # if tool_name != "bash":
    #     return False

    return bool(tool_args.get("run_in_background")) or is_slow_operation(tool_name, tool_args)


def start_background_task(tool_call: ChatCompletionMessageToolCallUnion, handlers: dict) -> str:
    global BG_COUNTER
    BG_COUNTER += 1

    bg_id = f"bg-{BG_COUNTER:04d}"
    tool_args = json.loads(tool_call.function.arguments)
    # command = tool_args.get("command", "")
    tool_call_str = f"{tool_call.function.name}({', '.join(f'{k}={v}' for k, v in tool_args.items())})"

    def worker():
        handler = handlers.get(tool_call.function.name)
        result = call_tool_handler(handler, tool_args, tool_call.function.name)
        # trigger_hooks("PostToolUse", tool_call, result)
        with BACKGROUND_LOCK:
            BACKGROUND_TASKS[bg_id]["status"] = "completed"
            BACKGROUND_RESULTS[bg_id] = str(result)

    with BACKGROUND_LOCK:
        BACKGROUND_TASKS[bg_id] = {
            "tool_call_id": tool_call.id,
            "tool_call": tool_call_str,
            "status": "running",
        }

    threading.Thread(target=worker, daemon=True).start()
    # print(f"  \033[33m[Background Task] {bg_id}: {str(tool_call_str)[:60]}\033[0m")
    _LOGER.info(f"[Background Task] {bg_id}: {str(tool_call_str)[:60]}")
    return bg_id


def collect_background_results() -> list[str]:
    with BACKGROUND_LOCK:
        completed_tasks = [bg_id for bg_id, task in BACKGROUND_TASKS.items() if task["status"] == "completed"]

    notifications = []
    for bg_id in completed_tasks:
        with BACKGROUND_LOCK:
            task = BACKGROUND_TASKS.pop(bg_id)
            output = BACKGROUND_RESULTS.pop(bg_id, "")
        summary = output[:200] if len(output) > 200 else output
        notifications.append(
            f"<background-task-notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            f"  <status>{task['status']}</status>\n"
            f"  <tool>{task['tool_call']}</tool>\n"
            f"  <summary>{summary}</summary>\n"
            f"</background-task-notification>"
        )

    _LOGER.info(f"[Collect Background Results] {completed_tasks}]")

    return notifications
