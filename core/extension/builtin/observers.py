"""内置观察者扩展：记录工具调用，只读上下文、不参与裁决。

迁移自旧 hook 的 tool_call_log_hook，日志文案不变；以 observer 阶段注册，先于权限门执行，
因此被拦下的调用同样有日志。
"""
import json

from core.extension.api import ExtensionAPI
from core.extension.context import BeforeToolContext
from core.log.log import get_logger

_LOGGER = get_logger(__name__)


def tool_call_log_hook(ctx: BeforeToolContext) -> None:
    """记录一次工具调用的名称与参数（JSON 原文）。"""
    _LOGGER.info(
        f"[HOOK] call tool {ctx.tool_name} with arguments: "
        f"{json.dumps(ctx.args, ensure_ascii=False)}")


def register(api: ExtensionAPI) -> None:
    """注册调用日志观察者：observer 阶段对裁决无副作用。"""
    api.on("before_tool", tool_call_log_hook, stage="observer")
