import asyncio
import concurrent.futures


class AgentInterrupted(asyncio.CancelledError):
    pass


class AgentRunContext:

    def __init__(self):
        self.cancelled = asyncio.Event()
        # asyncio.Task（本回合工具协程）或 concurrent.futures.Future（后台任务的跨线程桥）：
        # 两者都有 cancel/done/cancelled/exception，cancel() 对后者经 _chain_future 传回内层 task
        self.tasks: set = set()

    @property
    def interrupted(self):
        return self.cancelled.is_set()

    def raise_if_cancelled(self):
        if self.cancelled.is_set():
            raise AgentInterrupted("User interrupted")

    def track(self, task: asyncio.Future | concurrent.futures.Future):
        self.tasks.add(task)

        def done(t):
            self.tasks.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(done)

    def cancel(self):
        self.cancelled.set()
        for task in list(self.tasks):
            # concurrent.futures.Future.cancel() 会同步跑 done 回调删除 task，
            # 可能导致 self.tasks 发生变化
            task.cancel()
