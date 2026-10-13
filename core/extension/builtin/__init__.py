"""内置扩展：权限门与观察者（由 bootstrap 显式注册，不走外部扩展扫描）。

单次调用契约：`register_builtin_extensions` 由 bootstrap 在启动时调用一次；
测试需先 `reset_extensions()` 清空注册表再调用，避免重复注册产生重复回调与票。
"""
from typing import Callable

from core.config import PERMISSION_FILE
from core.extension.api import ExtensionAPI
import core.extension.dispatcher as dispatcher
from core.extension.builtin import observers, permission
from core.extension.builtin.permission import Rules, load_rules


def register_builtin_extensions(*, rules: Rules | None = None) -> None:
    """注册内置观察者与权限门；rules 为 None 时从 .permissions.json 加载。

    先登记 observers、后登记 permission；实际派发顺序由 stage 决定（observer 先于 gate）。
    """
    if rules is None:
        rules = load_rules(PERMISSION_FILE)
    _register("builtin:observers", observers.register)
    _register("builtin:permission", lambda api: permission.register(api, rules))


def _register(module_name: str, register: Callable[[ExtensionAPI], None]) -> None:
    """用内置模式缓冲注册一个扩展，正常返回后一次性提交到派发器。"""
    api = ExtensionAPI(module_name, builtin=True)
    register(api)
    for event, callback, stage, fail_closed in api.commit():
        dispatcher.add(
            event, callback, module_name, stage=stage, fail_closed=fail_closed, builtin=True)
