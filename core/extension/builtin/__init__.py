"""内置扩展：权限门与观察者（由 bootstrap 显式注册，不走外部扩展扫描）。

单次调用契约：`register_builtin_extensions` 由 bootstrap 在启动时调用一次；
测试需先 `reset_extensions()` 清空注册表再调用，避免重复注册产生重复回调与票。
"""
from typing import Callable

from core.config import PATHS
from core.extension.api import ExtensionAPI
from core.extension.host import commit_extension
import core.extension.registry as registry
from core.extension.builtin import observers, permission
from core.extension.builtin.permission import Rules, load_rules


class _BuiltinExtensionAPI(ExtensionAPI):
    """内置注册入口私有的身份；外部扩展的公开缝里没有 builtin 开关。"""

    _builtin = True


def register_builtin_extensions(*, rules: Rules | None = None) -> None:
    """注册内置观察者与权限门；rules 为 None 时从 .permissions.json 加载。

    先登记 observers、后登记 permission；实际派发顺序由 stage 决定（observer 先于 gate）。
    """
    if rules is None:
        rules = load_rules(PATHS.PERMISSION_FILE)
    _register("builtin:observers", observers.register)
    _register("builtin:permission", lambda api: permission.register(api, rules))


def _register(module_name: str, register: Callable[[ExtensionAPI], None]) -> None:
    """用内置模式缓冲注册一个扩展，正常返回后一次性提交到派发器并登记。"""
    api = _BuiltinExtensionAPI()
    register(api)
    registered = commit_extension(api, module_name)
    registry.record(
        module_name.removeprefix("builtin:"),
        "builtin",
        enabled=True,
        events=[registration.event for registration in registered],
    )
