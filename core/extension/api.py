"""扩展注册 API：register(api) 内缓冲注册，正常返回后由加载器一次性提交。"""
from typing import Callable

EVENTS: tuple[str, ...] = ("before_llm", "after_llm", "before_tool", "after_tool")


class ExtensionAPI:
    """单个扩展模块的参数对象：只做注册缓冲，不直接写入任何注册表。"""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self._pending: list[tuple[str, Callable]] = []

    def on(self, event: str, callback: Callable) -> None:
        """缓冲一个事件回调；事件名或回调非法立即报错，使整个扩展作废。"""
        if event not in EVENTS:
            raise ValueError(f"未知扩展事件: {event!r}（可用: {', '.join(EVENTS)}）")
        if not callable(callback):
            raise TypeError(f"扩展回调必须可调用，收到: {type(callback).__name__}")
        self._pending.append((event, callback))

    def commit(self) -> list[tuple[str, Callable]]:
        """返回全部缓冲的注册并清空缓冲。"""
        pending, self._pending = self._pending, []
        return pending
