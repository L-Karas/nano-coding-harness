"""扩展注册 API：register(api) 内缓冲注册，正常返回后由加载器一次性提交。"""
from typing import Callable

from core.extension.protocol import EVENTS, validate_registration

__all__ = ["EVENTS", "ExtensionAPI"]


class ExtensionAPI:
    """单个扩展模块的参数对象：只做注册缓冲，不直接写入任何注册表。"""

    def __init__(self, *, builtin: bool = False):
        self._builtin = builtin
        self._pending: list[tuple[str, Callable, str, bool]] = []

    @property
    def builtin(self) -> bool:
        """是否内置扩展；只读，实例创建后不可改写（外部扩展无法自升为内置）。"""
        return self._builtin

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
        validate_registration(
            event, callback, stage=stage, fail_closed=fail_closed, builtin=self.builtin)
        self._pending.append((event, callback, stage, fail_closed))

    def commit(self) -> list[tuple[str, Callable, str, bool]]:
        """返回全部缓冲的注册并清空缓冲。"""
        pending, self._pending = self._pending, []
        return pending
