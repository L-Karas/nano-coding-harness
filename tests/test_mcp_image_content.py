"""MCP 工具结果的图像支持：TextContent / ImageContent → ToolResult 正文 + 图片附件。"""
import asyncio
import base64
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import mcp_types
import pytest

import core.mcp.mcp_client as mcp_client
import core.tools.vision as vision
from core.tools.tool_pool import ToolPool, _McpEntry
from core.tools.tool_result import ImageAttachment, ToolResult

REPO_ROOT = Path(__file__).resolve().parents[1]

PNG = b"\x89PNG\r\n\x1a\n"
GIF = b"GIF89a"
PNG_B64 = base64.b64encode(PNG).decode()
GIF_B64 = base64.b64encode(GIF).decode()


def _text(text: str) -> mcp_types.TextContent:
    return mcp_types.TextContent(type="text", text=text)


def _image(data: str, mime_type: str = "image/png") -> mcp_types.ImageContent:
    return mcp_types.ImageContent(type="image", data=data, mime_type=mime_type)


def _manager(content, *, is_error: bool = False) -> mcp_client.ClientManager:
    """session_group / tool_handlers 都被替换成内存假实现，不建任何连接。"""
    manager = mcp_client.ClientManager({"mcpServers": {}})
    manager.tool_handlers["shot"] = lambda **kwargs: "sync"

    async def call_tool(name, args):
        return mcp_types.CallToolResult(content=list(content), is_error=is_error)

    manager.session_group = SimpleNamespace(call_tool=call_tool)
    return manager


def _call(manager, args: dict | None = None) -> ToolResult:
    return asyncio.run(manager.tool_call_async("shot", None, args if args is not None else {}))


def test_text_blocks_become_tool_content():
    assert _call(_manager([_text("a"), _text("b")])) == ToolResult(content="a\nb")


def test_image_block_becomes_attachment():
    result = _call(_manager([_image(PNG_B64)]))
    assert result == ToolResult(
        content="Image returned by shot (1 image).",
        images=(ImageAttachment(source="shot", mime="image/png",
                                image_url=f"data:image/png;base64,{PNG_B64}"),),
    )


def test_text_and_images_coexist_in_one_result():
    result = _call(_manager([_text("done"), _image(PNG_B64), _image(GIF_B64, "image/gif")]))
    assert result.content == "done", "正文取文本块，不被图片顶掉"
    assert result.images == (
        ImageAttachment(source="shot", mime="image/png", image_url=f"data:image/png;base64,{PNG_B64}"),
        ImageAttachment(source="shot", mime="image/gif", image_url=f"data:image/gif;base64,{GIF_B64}"),
    ), "每张图各自一条附件，不合并成一个 data URL"


def test_call_args_are_part_of_the_attachment_source():
    result = _call(_manager([_image(PNG_B64)]), {"path": "x.png"})
    assert result.images[0].source == "shot({'path': 'x.png'})"


def test_error_result_is_a_tool_error_without_images():
    result = _call(_manager([_text("boom"), _image(PNG_B64)], is_error=True))
    assert result.is_error is True
    assert result.content.startswith("[Tool Error]:") and "boom" in result.content
    assert result.images == ()


def test_error_result_without_text_still_explains_itself():
    result = _call(_manager([], is_error=True))
    assert result.is_error is True and "shot failed" in result.content


def test_empty_result_is_empty_content():
    assert _call(_manager([])) == ToolResult(content="")


def test_sync_tool_call_returns_tool_result():
    manager = _manager([_image(PNG_B64)])
    assert manager.tool_call("shot") == _call(manager)


def _entry(manager, name: str = "mcp__srv__shot") -> _McpEntry:
    """与 assemble_tool_pool 装配处同一形态：sync / async handler 都接到 manager.tool_call*。"""
    return _McpEntry(
        {"type": "function", "function": {"name": name, "parameters": {}}},
        lambda **kw: manager.tool_call("shot", kw),
        lambda *, _name="shot", ctx=None, **kw: manager.tool_call_async(_name, ctx, kw),
    )


def test_pool_passes_mcp_images_through_async():
    manager = _manager([_text("done"), _image(PNG_B64)])
    pool = ToolPool({"mcp__srv__shot": _entry(manager)})
    result = asyncio.run(pool.execute("mcp__srv__shot", {}, None))
    assert result.content == "done", "附件不能被 str() 拍平成 repr"
    assert result.images[0].image_url == f"data:image/png;base64,{PNG_B64}"


def test_pool_passes_mcp_images_through_sync():
    manager = _manager([_image(PNG_B64)])
    pool = ToolPool({"mcp__srv__shot": _entry(manager)})
    assert pool.execute_sync("mcp__srv__shot", {}, None) == _call(manager)


def test_failed_result_skips_the_size_check(monkeypatch):
    """is_error 优先：失败结果里的图片不参与大小校验。"""
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 0)
    result = _call(_manager([_text("boom"), _image(PNG_B64)], is_error=True))
    assert result.is_error is True and "boom" in result.content
    assert result.images == ()


def test_oversized_image_is_downscaled_instead_of_rejected(monkeypatch, noise_png):
    data = noise_png()
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    result = _call(_manager([_text("done"), _image(base64.b64encode(data).decode())]))
    assert result.is_error is False
    (attachment,) = result.images
    assert attachment.mime == "image/png"
    assert len(base64.b64decode(attachment.image_url.split(",", 1)[1])) <= 50_000
    assert result.content == "done\n1 image was downscaled to fit the size limit."


def test_generated_text_counts_the_downscaled_images(monkeypatch, noise_png):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    encoded = _image(base64.b64encode(noise_png()).decode())
    result = _call(_manager([encoded, encoded]))
    assert result.content == ("Image returned by shot (2 images).\n"
                              "2 images were downscaled to fit the size limit.")


def test_oversized_image_is_rejected_without_attachment(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", len(PNG) - 1)
    result = _call(_manager([_text("done"), _image(PNG_B64)]))
    assert result.is_error is True
    assert result.images == ()
    assert result.content == (
        f"[Tool Error]: Image too large: shot (image 1) is {len(PNG)} bytes "
        f"(limit {len(PNG) - 1} bytes).")


def test_image_at_the_limit_is_allowed(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", len(PNG))
    result = _call(_manager([_image(PNG_B64)]))
    assert result.is_error is False
    assert result.images[0].image_url == f"data:image/png;base64,{PNG_B64}"


def test_oversized_image_is_named_by_its_position(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", len(PNG) - 1)
    result = _call(_manager([_image(GIF_B64, "image/gif"), _image(PNG_B64)]), {"n": 1})
    assert result.is_error is True
    assert "shot({'n': 1}) (image 2)" in result.content


def test_unsupported_image_type_is_rejected():
    result = _call(_manager([_text("done"), _image(PNG_B64, "image/bmp")]))
    assert result.is_error is True
    assert result.images == ()
    assert result.content == (
        "[Tool Error]: Unsupported image type: shot (image 1) declares 'image/bmp' "
        "(supported: image/gif, image/jpeg, image/png, image/webp).")


def test_image_type_is_normalized_for_the_data_url():
    result = _call(_manager([_image(PNG_B64, "IMAGE/PNG; charset=binary")]))
    assert result.is_error is False
    assert result.images == (ImageAttachment(source="shot", mime="image/png",
                                            image_url=f"data:image/png;base64,{PNG_B64}"),)


def test_type_check_runs_before_the_size_check(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 0)
    result = _call(_manager([_image(PNG_B64, "image/bmp")]))
    assert "Unsupported image type" in result.content


def test_unknown_tool_raises():
    manager = _manager([_text("x")])
    with pytest.raises(ValueError, match="not found"):
        manager.tool_call("nope")


def test_server_error_propagates_and_pool_encodes_it():
    """失败不在 MCP 层转文本：格式化后抛出，ToolPool 是唯一编码成 [Tool Error] 的位置（ADR-0001）。"""
    manager = _manager([])

    async def raising(name, args):
        raise RuntimeError("server exploded")

    manager.session_group = SimpleNamespace(call_tool=raising)

    with pytest.raises(mcp_client.McpToolError, match="MCP tool 'shot' failed: server exploded") as info:
        asyncio.run(manager.tool_call_async("shot", None, {}))
    assert isinstance(info.value.__cause__, RuntimeError), "原始异常保留在 __cause__"

    result = asyncio.run(ToolPool({"mcp__srv__shot": _entry(manager)}).execute("mcp__srv__shot", {}, None))
    assert result.is_error is True
    assert result.content == "[Tool Error]: MCP tool 'shot' failed: server exploded"


def test_sync_error_propagates():
    manager = _manager([])

    async def raising(name, args):
        raise RuntimeError("sync boom")

    manager.session_group = SimpleNamespace(call_tool=raising)
    with pytest.raises(mcp_client.McpToolError, match="failed: sync boom"):
        manager.tool_call("shot")


def test_exception_without_message_falls_back_to_its_type():
    manager = _manager([])

    async def raising(name, args):
        raise KeyError()

    manager.session_group = SimpleNamespace(call_tool=raising)
    with pytest.raises(mcp_client.McpToolError, match="failed: KeyError"):
        asyncio.run(manager.tool_call_async("shot", None, {}))


def test_async_timeout_propagates(monkeypatch):
    monkeypatch.setattr(mcp_client, "_TOOL_CALL_TIMEOUT", 0.05)
    manager = _manager([])

    async def hang(name, args):
        await asyncio.Event().wait()

    manager.session_group = SimpleNamespace(call_tool=hang)
    with pytest.raises(mcp_client.McpToolError, match="timed out after 0.05s"):
        asyncio.run(manager.tool_call_async("shot", None, {}))


def test_sync_timeout_propagates_and_cancels_the_pending_call(monkeypatch):
    monkeypatch.setattr(mcp_client, "_TOOL_CALL_TIMEOUT", 0.05)
    manager = _manager([])
    cancelled = threading.Event()

    async def hang(name, args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    manager.session_group = SimpleNamespace(call_tool=hang)
    with pytest.raises(mcp_client.McpToolError, match="timed out after 0.05s"):
        manager.tool_call("shot")
    assert cancelled.wait(2), "超时必须取消 MCP loop 上仍在跑的调用，不能悬挂"


@pytest.mark.parametrize("module", ["core.mcp", "core.tools", "core.mcp.mcp_client"])
def test_modules_import_standalone(module):
    """core.mcp 与 core.tools 互不依赖导入顺序（回归：曾因包级循环导入直接 ImportError）。"""
    proc = subprocess.run([sys.executable, "-c", f"import {module}"],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
