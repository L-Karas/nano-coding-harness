"""
Teammates
"""
import json
import re
import threading
import time
from functools import partial
from pathlib import Path
from typing import Optional

from core.client import shared_model_client
from core.experimental import message_bus
from core.experimental.task import TASK_DIR, can_start, claim_task, load_task, complete_task
from core.experimental.worktree import WORKTREES_DIR
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60
ACTIVATE_TEAMMATES: dict[str, bool] = {}


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
        # 延迟导入: core.tools -> extra_tools -> core.teammates 存在导入环
        from core.tools import assemble_tool_pool, call_tool_handler

        wt_ctx = {"work_path": None}

        def _wt_cwd():
            # Once a task with a worktree is claimed, all teammate file tools
            # transparently run inside that isolated directory.
            work_path = wt_ctx["work_path"]
            return Path(work_path) if work_path else None

        def _bind_worktree_cwd(fn):
            # tool_loader 中注册的 run_* 均接受 cwd; 认领带 worktree 的任务后统一注入
            def wrapped(**kwargs):
                return fn(**kwargs, cwd=_wt_cwd())

            return wrapped

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

        # 与 main / sub-agent 相同: 工具定义与默认 handler 统一取自 tool_loader(teammate 级);
        # compact / check_inbox 绑定 main 会话与 lead 邮箱语义, 不适用于自治 teammate
        excluded = {"compact", "check_inbox"}
        tools, handlers = assemble_tool_pool(agent_type="teammate")
        tools = [tool for tool in tools if tool["function"]["name"] not in excluded]
        handlers = {tool_name: handler
                    for tool_name, handler in handlers.items()
                    if tool_name not in excluded}
        # 文件类工具随认领的任务 worktree 切换 cwd
        for tool_name in ("bash", "edit_file", "glob", "grep", "read_file", "write_file"):
            handlers[tool_name] = _bind_worktree_cwd(handlers[tool_name])

        # 以下 handler 绑定到当前 teammate 身份
        def _run_send_message(to_agent: str, content: str) -> str:
            # 以队友名义发送(loader 的 run_send_message 固定以 lead 身份发送)
            message_bus.MESSAGE_BUS.send(name, to_agent, content)
            return "Sent"

        handlers["send_message"] = _run_send_message
        handlers["claim_task"] = _run_claim_task
        handlers["complete_task"] = _run_complete_task
        handlers["submit_plan"] = partial(handlers["submit_plan"], from_agent=name)

        messages = [{"role": "system", "content": system_prompt}]
        if prompt:
            messages.append({"role": "user", "content": prompt})

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
                    non_protocol = [message for message in inbox_messages if message.get("msg_type") == "message"]
                    if non_protocol:
                        messages.append({
                            "role": "user",
                            "content": f"<inbox_messages>{json.dumps(non_protocol, ensure_ascii=False)}</inbox_messages>",
                        })

                try:
                    # 模型统一走 shared_model_client（.harness/.setting.json），
                    # thinking 参数由模型配置决定（get_model_client 已按配置带 extra_body），不再单独指定
                    response = shared_model_client().get_model_client()(
                        messages=messages,
                        tools=tools,
                        max_tokens=8000,
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
                        _LOGGER.info(f">   [Call tool] (Teammate: {name}) {tool_name}")
                        _LOGGER.info(f">   [Tool arguments] (Teammate: {name}) {tool_args}")

                        handler = handlers.get(tool_name)
                        output = call_tool_handler(handler, tool_args, tool_name)
                        if tool_name == "submit_plan":
                            # run_submit_plan 返回 "Plan submitted (req_xxx)"; 命中即关闭
                            # approval gate, 等待 lead 的 plan_approval_response
                            match = re.search(r"\((req_\d+)\)", output)
                            protocol_ctx["waiting_plan"] = match.group(1) if match else output

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(output),
                        })

                        _LOGGER.info(f">   [Tool result] (Teammate: {name}) {output[:100]}")
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

            idle_result = idle_poll(name, messages, name, wt_ctx)
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
