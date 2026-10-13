"""扩展注册 API：register(api) 内缓冲注册，正常返回后由加载器一次性提交。"""
from typing import Callable

from core.extension.dispatcher import STAGES

EVENTS: tuple[str, ...] = ("before_llm", "after_llm", "before_tool", "tool_execute", "after_tool")


class ExtensionAPI:
    """单个扩展模块的参数对象：只做注册缓冲，不直接写入任何注册表。"""

    def __init__(self, module_name: str, *, builtin: bool = False):
        self.module_name = module_name
        self.builtin = builtin
        self._pending: list[tuple[str, Callable, str, bool]] = []

    def on(
        self,
        event: str,
        callback: Callable,
        *,
        stage: str = "user",
        fail_closed: bool = False,
    ) -> None:
        """缓冲一个事件回调；事件名、回调或阶段非法立即报错，使整个扩展作废。

        外部扩展只能注册 user 阶段且不能声明 fail_closed；内置扩展可用全部阶段。
        """
        if event not in EVENTS:
            raise ValueError(f"未知扩展事件: {event!r}（可用: {', '.join(EVENTS)}）")
        if not callable(callback):
            raise TypeError(f"扩展回调必须可调用，收到: {type(callback).__name__}")
        if stage not in STAGES:
            raise ValueError(f"未知扩展阶段: {stage!r}（可用: {', '.join(STAGES)}）")
        if not self.builtin and (stage != "user" or fail_closed):
            raise ValueError("外部扩展只能注册 user 阶段，且不能声明 fail_closed")
        self._pending.append((event, callback, stage, fail_closed))

    def commit(self) -> list[tuple[str, Callable, str, bool]]:
        """返回全部缓冲的注册并清空缓冲。"""
        pending, self._pending = self._pending, []
        return pending
