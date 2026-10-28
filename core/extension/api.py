"""扩展注册 API：register(api) 内缓冲注册，正常返回后由 host 一次性提交。"""
from core.extension.protocol import EVENTS, Registration, validate_registration

__all__ = ["EVENTS", "ExtensionAPI"]


class ExtensionAPI:
    """单个外部扩展模块的参数对象：只做注册缓冲，不直接写入任何注册表。

    外部扩展不能选择 builtin 身份；内置注册入口使用私有的
    `_BuiltinExtensionAPI`（见 core.extension.builtin），从正常扩展缝看不到这个入口。
    """

    _builtin = False

    def __init__(self):
        self._pending: list[Registration] = []

    @property
    def builtin(self) -> bool:
        """是否内置扩展；由 host 选择的子类决定，实例创建后不可改写。"""
        return type(self)._builtin

    def on(
        self,
        event: str,
        callback,
        *,
        stage: str = "user",
        fail_closed: bool = False,
    ) -> None:
        """缓冲一个事件回调；事件名、回调或阶段非法立即报错，使整个扩展作废。

        外部扩展只能注册 user 阶段且不能声明 fail_closed；内置扩展可用全部阶段。
        """
        validate_registration(
            event, callback, stage=stage, fail_closed=fail_closed, builtin=self.builtin)
        self._pending.append(Registration(event, callback, stage, fail_closed))

    def _commit(self) -> list[Registration]:
        """host 提交入口：返回全部缓冲的注册并清空缓冲。"""
        pending, self._pending = self._pending, []
        return pending
