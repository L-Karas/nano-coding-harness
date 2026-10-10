"""
Teammates
"""
import asyncio
import json
import re
import threading
import time

from core.client import shared_model_client
from core.config import CONFIGMANAGER
from core.context.compact import prepare_messages
from core.experimental import message_bus
from core.experimental.task import TASK_DIR, can_start, claim_task
from core.experimental.worktree import WORKTREES_DIR
from core.log.log import get_logger
from core.runtime_context import ToolContext

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
        tctx: ToolContext,
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
                    message_bus.MESSAGE_BUS.send(agent_name, "lead", "Shutting down.", "shutdown_response",
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
                    tctx.cwd = str(wt_path)
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
        from core.agent_step import StepPolicy, StepRenderer, run_agent_step
        from core.tools import assemble_tool_pool

        # 与 main / sub-agent 相同：工具定义与执行统一取自 tool_loader（teammate 级）；
        # 身份与 worktree 目录经调用方持有的 ToolContext 传达，认领任务时由工具更新 cwd。
        tctx = ToolContext(agent_name=name)
        pool = assemble_tool_pool(agent_type="teammate", enable_experimental=True,
                                  exclude=frozenset({"compact", "check_inbox"}))

        def _halt_after(tool_name: str, result) -> bool:
            """submit_plan 一旦落定：关闭审批闸门，本响应剩余工具不再执行。"""
            if tool_name != "submit_plan":
                return False
            match = re.search(r"\((req_\d+)\)", result.content)
            protocol_ctx["waiting_plan"] = match.group(1) if match else result.content
            return True

        policy = StepPolicy(use_extensions=True, permission_hooks=False, background=False,
                            diff_preview=False, should_halt=_halt_after)
        renderer = StepRenderer(
            on_tool_call=lambda tool, args: _LOGGER.info(f">   [Call tool] (Teammate: {name}) {tool} {args}"),
            on_tool_result=lambda text, err: _LOGGER.info(f">   [Tool result] (Teammate: {name}) {text[:100]}"),
        )

        history: list[dict] = []
        if prompt:
            history.append({"role": "user", "content": prompt})

        while True:
            should_shutdown = False
            for _ in range(10):
                inbox_messages = message_bus.MESSAGE_BUS.read(name)
                for msg in inbox_messages:
                    stopped = handle_inbox_message(name, msg, history)
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
                        history.append({
                            "role": "user",
                            "content": f"<inbox_messages>{json.dumps(non_protocol, ensure_ascii=False)}</inbox_messages>",
                        })

                async def _step_once():
                    prepared = await prepare_messages(history[:], None, sub_model=True)
                    history[:] = prepared
                    return await run_agent_step(
                        history, system_prompt,
                        client=shared_model_client(),
                        pool=pool,
                        tctx=tctx,
                        max_tokens=CONFIGMANAGER.config.default_max_tokens,
                        policy=policy,
                        renderer=renderer,
                    )

                try:
                    outcome = asyncio.run(_step_once())
                except Exception:
                    _LOGGER.exception("[Teammate] model step failed")
                    break

                if outcome.aborted:
                    # 扩展阻止了本步：不杀队友，落到外层 idle 轮询等任务/邮件
                    _LOGGER.warning(f"[Teammate] step aborted by extension: {outcome.abort_reason}")
                    break

                history.append(outcome.assistant_message)
                history.extend(outcome.followup_messages)
                if protocol_ctx["waiting_plan"]:
                    break

            if should_shutdown:
                break

            if protocol_ctx["waiting_plan"]:
                continue

            idle_result = idle_poll(name, history, tctx)
            if idle_result in ("shutdown", "timeout"):
                break

        summary = "Done."
        for msg in reversed(history):
            if isinstance(msg, dict) and msg["role"] == "assistant":
                summary = msg["content"]
                break

        message_bus.MESSAGE_BUS.send(name, "lead", summary, "result")
        ACTIVATE_TEAMMATES.pop(name)

    ACTIVATE_TEAMMATES[name] = True
    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}."
