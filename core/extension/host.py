"""扩展 host 侧编排：loader / builtin 共用的原子提交入口。"""
from core.extension.api import ExtensionAPI
from core.extension.protocol import Registration
import core.extension.dispatcher as dispatcher

__all__ = ["commit_extension"]


def commit_extension(api: ExtensionAPI, module_name: str) -> list[Registration]:
    """把 api 缓冲的注册原子提交到派发器，返回已提交的记录。"""
    registrations = api._commit()
    dispatcher.add_many(module_name, registrations, builtin=api.builtin)
    return registrations
