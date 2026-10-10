"""工具调用的结局值：内容 + 失败标志（叶子模块：只依赖模板常量，不 import 其他 core 模块）。

失败一律来自异常：执行器捕获异常后经 error() 构造带前缀的结果；handler 返回的字符串
一律是内容，不解析前缀（见 docs/adr/0001-tool-failure-is-exception.md）。
"""
from dataclasses import dataclass

from core.template import TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结局：交还给模型的内容，加上这次调用是否失败的结构标志。"""
    content: str
    is_error: bool = False

    @classmethod
    def error(cls, detail: str) -> "ToolResult":
        return cls(content=f"{TOOL_ERROR_PREFIX} {detail}", is_error=True)

    @classmethod
    def unknown(cls, name: str) -> "ToolResult":
        return cls(content=f"{UNKNOWN_TOOL_PREFIX} {name}", is_error=True)
