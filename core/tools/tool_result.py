"""工具调用的结局值：内容 + 失败标志 + 取消标志（叶子模块：只依赖模板常量，不 import 其他 core 模块）。

失败一律来自异常：执行器捕获异常后经 error() 构造带前缀的结果；handler 返回的字符串
一律是内容，不解析前缀（见 docs/adr/0001-tool-failure-is-exception.md）。
取消不是失败：被用户停下的工作经 cancelled() 构造，is_cancelled 单独成标志
（见 docs/adr/0009-background-cancel.md）。
"""
from dataclasses import dataclass

from core.template import TOOL_ERROR_MESSAGE_TEMPLATE, UNKNOWN_TOOL_MESSAGE_TEMPLATE


@dataclass(frozen=True)
class ImageAttachment:
    """一次工具读取带回的图片：来源、MIME 与直接可用的 image_url。"""
    source: str  # 本地绝对路径或原始 URL
    mime: str
    image_url: str  # data URL 或 http(s) URL


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结局：交还给模型的内容，加上失败 / 被取消的结构标志。"""
    content: str
    is_error: bool = False
    is_cancelled: bool = False
    images: tuple[ImageAttachment, ...] = ()

    @classmethod
    def error(cls, detail: str) -> "ToolResult":
        return cls(content=TOOL_ERROR_MESSAGE_TEMPLATE.format(detail=detail), is_error=True)

    @classmethod
    def cancelled(cls, content: str) -> "ToolResult":
        return cls(content=content, is_cancelled=True)

    @classmethod
    def unknown(cls, name: str) -> "ToolResult":
        return cls(content=UNKNOWN_TOOL_MESSAGE_TEMPLATE.format(name=name), is_error=True)
