"""
Background tasks

# Slow tools return a placeholder tool_result immediately. Their real output is
# later injected as a <background-task-notification> message, so the main loop can
# keep moving.
"""
import asyncio
import json
import re
import threading

from openai.types.chat import ChatCompletionMessageToolCallUnion

from core.log import get_logger
from core.runtime_context import ToolContext
from core.runtime_state import RUNTIME_STATE
from core.template import USER_INTERRUPT_PROMPT
from core.tools import ToolPool, ToolResult

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


def _bg_interrupted(tctx: ToolContext | None) -> bool:
    return bool(tctx and tctx.agent_run and tctx.agent_run.interrupted)


def start_background_task(tool_call: ChatCompletionMessageToolCallUnion, pool: ToolPool,
                         tctx: ToolContext | None = None, loop=None) -> str:
    bg_id = RUNTIME_STATE.allocate_background_id()
    tool_args = json.loads(tool_call.function.arguments)
    tool_call_str = f"{tool_call.function.name}({', '.join(f'{k}={v}' for k, v in tool_args.items())})"
    tool_name = tool_call.function.name

    loop = loop or asyncio.get_running_loop()

    RUNTIME_STATE.register_background(bg_id, tool_call_str)

    _LOGGER.info(f"[Background Task] {bg_id}: {tool_call_str[:60]}")

    if pool.has_native_async(tool_name):
        # 原生异步工具直接挂在调用方的 loop 上（Agent step 就跑在 runtime loop 上），
        # 免掉一个只用来 fut.result() 空等的 worker 线程。
        task = loop.create_task(pool.execute(tool_name, tool_args, tctx))

        def done(t):
            if t.cancelled():
                result = ToolResult(content=USER_INTERRUPT_PROMPT if _bg_interrupted(tctx)
                                    else "[Background task cancelled]")
            else:
                error = t.exception()
                result = ToolResult.error(f"{type(error).__name__}: {error}") if error else t.result()
            RUNTIME_STATE.complete_background(bg_id, result)

        task.add_done_callback(done)
        if tctx and tctx.agent_run:
            tctx.agent_run.track(task)  # Esc 时 ctx.cancel() 直接 task.cancel()
        return bg_id

    def worker():
        try:
            if _bg_interrupted(tctx):
                result = ToolResult(content=USER_INTERRUPT_PROMPT)
            else:
                result = pool.execute_sync(tool_name, tool_args, tctx)
        except BaseException as e:
            result = (ToolResult(content=USER_INTERRUPT_PROMPT) if _bg_interrupted(tctx)
                      else ToolResult.error(f"{type(e).__name__}: {e}"))
        RUNTIME_STATE.complete_background(bg_id, result)

    threading.Thread(target=worker, daemon=True).start()
    return bg_id


def collect_background_results() -> list[str]:
    done = RUNTIME_STATE.pop_completed_backgrounds()
    _LOGGER.info(f"[Collect Background Results] {[item.id for item in done]}")

    notifications = []
    for item in done:
        # todo: limit output size
        summary = item.result.content if item.result is not None else ""
        notifications.append(
            f"<background-task-notification>\n"
            f"  <task_id>{item.id}</task_id>\n"
            f"  <status>completed</status>\n"
            f"  <tool>{item.tool_call}</tool>\n"
            f"  <summary>{summary}</summary>\n"
            f"</background-task-notification>"
        )

    return notifications
