"""ToolResult 携带图片附件；ToolPool 原样透传工具返回的 ToolResult。"""
import asyncio

from core.tools import ImageAttachment, ToolPool
from core.tools.tool_base import BaseTool
from core.tools.tool_result import ToolResult

ATTACHMENT = ImageAttachment(
    source="/tmp/a.png",
    mime="image/png",
    image_url="data:image/png;base64,AAA",
)


class ImageTool(BaseTool):
    """Image result tool."""
    def run(self, tctx=None):
        return ToolResult(content="image ok", images=(ATTACHMENT,))


class TextTool(BaseTool):
    """Text result tool."""
    def run(self, tctx=None):
        return "text ok"


def test_pool_preserves_images_from_tool_result():
    pool = ToolPool({"image_tool": ImageTool})
    expected = ToolResult(content="image ok", images=(ATTACHMENT,))
    assert pool.execute_sync("image_tool", {}) == expected
    assert asyncio.run(pool.execute("image_tool", {})) == expected


def test_pool_still_wraps_plain_strings():
    pool = ToolPool({"text_tool": TextTool})
    assert pool.execute_sync("text_tool", {}) == ToolResult(content="text ok")


def test_error_results_have_no_images():
    assert ToolResult.error("boom").images == ()
