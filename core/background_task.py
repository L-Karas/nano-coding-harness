"""
Background tasks

# Slow tools return a placeholder tool_result immediately. Their real output is
# later injected as a <background-task-notification> message, so the main loop can
# keep moving.
"""
import asyncio
import inspect
import json
import re
import threading

from openai.types.chat import ChatCompletionMessageToolCallUnion

from core.log import get_logger
from core.template import TOOL_ERROR_PREFIX, USER_INTERRUPT_PROMPT
from core.tools import call_tool_handler
from core.tools.tool_loader import execute_tool

BG_COUNTER = 0
BACKGROUND_TASKS: dict[str, dict] = {}
BACKGROUND_RESULTS: dict[str, str] = {}
BACKGROUND_LOCK = threading.Lock()
_LOGGER = get_logger(__name__)

# 慢命令判定只看命令位置的词(每条 ; && | 分隔的命令的第一个词)，不看参数/路径/字符串里的词。
_CMD_SEP = re.compile(r"&&|\|\||[;&|\n]")
_WORD = re.compile(r"[A-Za-z0-9_./+@=-]+")
_WRAPPERS = {"sudo", "time", "env", "command", "nohup", "exec"}
_ALWAYS_SLOW = {"make", "pytest", "sleep", "install", "build", "deploy", "compile"}
_SLOW_SUBCMDS = {  # 这些命令只有跟了慢子命令才算慢：npm test 慢，npm run lint 不慢
    "pip": {"install"}, "pip3": {"install"}, "uv": {"sync"},
    "npm": {"install", "ci", "test"}, "pnpm": {"install", "test"}, "yarn": {"install", "test"},
    "bun": {"install"}, "poetry": {"install", "build"}, "conda": {"install"},
    "cargo": {"build", "test", "run"}, "go": {"build", "test"},
    "docker": {"build"}, "docker-compose": {"build"},
    "gradle": {"build", "test"}, "mvn": {"install", "build", "test", "deploy"},
    "apt": {"install", "upgrade"}, "apt-get": {"install", "upgrade"}, "brew": {"install", "upgrade"},
}


def is_slow_operation(tool_name: str, tool_args: dict) -> bool:
    """粗判 terminal 命令会不会跑很久：只看命令词，宁可漏判也不误判。"""
    if tool_name != "terminal":
        return False

    for segment in _CMD_SEP.split(tool_args.get("command") or ""):
        words = [w.lower() for w in _WORD.findall(segment)]
        while words and (words[0] in _WRAPPERS or "=" in words[0]):  # 跳过 sudo/time 前缀和 FOO=1 赋值
            words.pop(0)
        if not words:
            continue
        cmd = words[0].rsplit("/", 1)[-1].removesuffix(".sh")  # ./build.sh → build
        sub = words[1] if len(words) > 1 else ""
        if cmd in _ALWAYS_SLOW or sub in _SLOW_SUBCMDS.get(cmd, ()):
            return True
    return False


def should_run_background(tool_name: str, tool_args: dict) -> bool:
    if tool_name == "spawn_subagent":
        # 子代理固定后台跑：主代理不等待结论，结果稍后经 background notification 注入
        return True
    return bool(tool_args.get("should_run_in_background")) or is_slow_operation(tool_name, tool_args)


def _complete_background(bg_id: str, result) -> None:
    with BACKGROUND_LOCK:
        BACKGROUND_TASKS[bg_id]["status"] = "completed"
        BACKGROUND_RESULTS[bg_id] = str(result)


def start_background_task(tool_call: ChatCompletionMessageToolCallUnion, handlers: dict, ctx=None, loop=None) -> str:
    global BG_COUNTER
    BG_COUNTER += 1

    bg_id = f"bg-{BG_COUNTER:04d}"
    tool_args = json.loads(tool_call.function.arguments)
    tool_call_str = f"{tool_call.function.name}({', '.join(f'{k}={v}' for k, v in tool_args.items())})"

    loop = loop or asyncio.get_running_loop()
    handler = handlers.get(tool_call.function.name)

    with BACKGROUND_LOCK:
        BACKGROUND_TASKS[bg_id] = {
            "tool_call_id": tool_call.id,
            "tool_call": tool_call_str,
            "status": "running",
        }

    _LOGGER.info(f"[Background Task] {bg_id}: {tool_call_str[:60]}")

    if inspect.iscoroutinefunction(handler):
        # 协程 handler 直接挂在调用方的 loop 上（call_tools 就跑在 runtime loop 上），
        # 免掉一个只用来 fut.result() 空等的 worker 线程。
        task = loop.create_task(execute_tool(handler, tool_args, tool_call.function.name, ctx))

        def done(t):
            if t.cancelled():
                result = USER_INTERRUPT_PROMPT if ctx and ctx.interrupted else "[Background task cancelled]"
            else:
                error = t.exception()
                result = f"{TOOL_ERROR_PREFIX} {type(error).__name__}: {error}" if error else t.result()
            _complete_background(bg_id, result)

        task.add_done_callback(done)
        if ctx:
            ctx.track(task)  # Esc 时 ctx.cancel() 直接 task.cancel()
        return bg_id

    def worker():
        try:
            if ctx and ctx.interrupted:
                result = USER_INTERRUPT_PROMPT
            else:
                result = call_tool_handler(handler, tool_args, tool_call.function.name)
        except BaseException as e:
            result = USER_INTERRUPT_PROMPT if ctx and ctx.interrupted else f"{TOOL_ERROR_PREFIX} {type(e).__name__}: {e}"
        _complete_background(bg_id, result)

    threading.Thread(target=worker, daemon=True).start()
    return bg_id


def collect_background_results() -> list[str]:
    with BACKGROUND_LOCK:
        completed_tasks = [bg_id for bg_id, task in BACKGROUND_TASKS.items() if task["status"] == "completed"]

    notifications = []
    for bg_id in completed_tasks:
        with BACKGROUND_LOCK:
            task = BACKGROUND_TASKS.pop(bg_id)
            output = BACKGROUND_RESULTS.pop(bg_id, "")
        # todo: limit output size
        summary = output
        notifications.append(
            f"<background-task-notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            f"  <status>{task['status']}</status>\n"
            f"  <tool>{task['tool_call']}</tool>\n"
            f"  <summary>{summary}</summary>\n"
            f"</background-task-notification>"
        )

    _LOGGER.info(f"[Collect Background Results] {completed_tasks}")

    return notifications
