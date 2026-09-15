import asyncio


class AgentInterrupted(Exception):
    pass


class AgentRunContext:

    def __init__(self):
        self.cancelled = asyncio.Event()
        self.tasks: set[asyncio.Task] = set()

    @property
    def interrupted(self):
        return self.cancelled.is_set()

    def raise_if_cancelled(self):
        if self.cancelled.is_set():
            raise AgentInterrupted("User interrupted")

    def track(self, task: asyncio.Task):
        self.tasks.add(task)

        def done(t: asyncio.Task):
            self.tasks.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(done)

    def cancel(self):
        self.cancelled.set()
        for task in self.tasks:
            task.cancel()

    def clean(self):
        pass
