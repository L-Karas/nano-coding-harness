"""Turn 的线程契约：私有事件循环 + 串行锁 + 当前回合的上下文 / 任务登记（ADR-0007）。

模型客户端绑定这里的事件循环；用户回合、cron 自动回合与 /compact 共用同一把锁串行。
调用方只提意图（跑回合 / 压缩 / 中断），不再持有 AGENT_LOCK 或直接操作 loop。
"""
import asyncio
import concurrent.futures
import threading
from typing import Awaitable, Callable

from core.runtime_context import AgentInterrupted, AgentRunContext
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)


class TurnRunner:
    """loop、锁与当前 ctx/task 的唯一 owner；run() 阻塞调用线程至回合结束。"""

    def __init__(self) -> None:
        """创建私有事件循环、串行锁与回合状态槽。"""
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="agent-turn-loop", daemon=True)
        self._thread.start()
        self._thread_id = self._thread.ident
        self._current_ctx: AgentRunContext | None = None
        self._current_task: asyncio.Task | None = None

    # ---------- 只读状态 ----------

    @property
    def current_context(self) -> AgentRunContext | None:
        """当前回合的上下文。

        Returns:
            正在运行的回合上下文；空闲时为 None。
        """
        return self._current_ctx

    def is_running(self) -> bool:
        """是否有回合正在执行。

        Returns:
            当前任务存在且未完成时为 True。
        """
        task = self._current_task
        return task is not None and not task.done()

    # ---------- 驱动 ----------

    def run(self, work: Callable[[AgentRunContext], Awaitable[bool | None]], *,
            prepare: Callable[[], bool] | None = None) -> bool | None:
        """在锁内跑一次工作协程并阻塞取结果。

        prepare 在锁内、提交前执行：返回 False 表示无活可干（auto_loop 用），本次直接跳过。
        被 interrupt() 取消（concurrent.futures.CancelledError）时统一抛 AgentInterrupted。

        Args:
            work: 接收回合上下文的协程函数。
            prepare: 锁内、提交前执行的准备回调；返回 False 时跳过本次工作。

        Returns:
            prepare 返回 False 而跳过时为 False；否则返回工作协程的返回值
            （run_compact 为 bool，run_turn 为 None）。

        Raises:
            RuntimeError: 在回合 loop 线程内重入。
            AgentInterrupted: 回合被用户中断。
        """
        if threading.get_ident() == self._thread_id:
            raise RuntimeError("Agent is already running")  # 回合内重入会死锁：直接拒绝

        with self._lock:
            if prepare is not None and not prepare():
                return False

            ctx = AgentRunContext()

            async def _work() -> bool | None:
                """在回合 loop 上设置当前 ctx/task 并执行工作。

                Returns:
                    工作协程的返回值。
                """
                self._current_ctx = ctx
                self._current_task = asyncio.current_task()
                try:
                    return await work(ctx)
                finally:
                    self._current_task = None
                    self._current_ctx = None

            try:
                return asyncio.run_coroutine_threadsafe(_work(), loop=self._loop).result()
            except concurrent.futures.CancelledError:
                raise AgentInterrupted("User interrupted") from None
            finally:
                self._current_ctx = None

    def interrupt(self) -> bool:
        """Esc：双路取消当前 ctx 与 task；无进行中的工作时返回 False。

        Returns:
            是否成功投递取消。
        """
        ctx, task = self._current_ctx, self._current_task
        if ctx is None or task is None:
            return False
        if task.done() or ctx.interrupted:
            return False

        def cancel_all() -> None:
            """在任务所属 loop 上同时取消上下文与任务。"""
            ctx.cancel()
            task.cancel()

        loop = task.get_loop()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        try:
            if running is loop:
                cancel_all()
            else:
                task.get_loop().call_soon_threadsafe(cancel_all)
        except RuntimeError:
            return False

        return True
