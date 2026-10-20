"""图片读取的视觉处理：MIME 判定、大小上限、视觉能力门控与图片附件结果。

`read_file` 只负责「路径 → 分支」，图片分支的判定与文案都下沉到这里。其他方法 / 模块想随
工具结果带回图片（截图、网页抓图等）时，直接调 `local_image_result()` /
`alocal_image_result()` / `url_image_result()`，不必复制这份判定。
"""
import base64
from pathlib import Path
from urllib.parse import urlsplit

import aiofiles

from core.runtime_context import ToolContext
from core.tools.tool_result import ImageAttachment, ToolResult

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_MIME_TYPES = {  # 本地图片后缀 -> MIME
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# 可送进 vision 模型的图片类型；外部来源自报的 MIME 由 declared_image_mime() 按这张表校验
_SUPPORTED_IMAGE_MIME_TYPES = frozenset(IMAGE_MIME_TYPES.values())

VISION_UNSUPPORTED_MESSAGE = "Current model does not support vision input; image not attached."


def supports_vision(tctx: ToolContext | None) -> bool:
    """当前模型能否接收图片；无上下文（直接调用 / 测试）时按支持视觉处理。"""
    return tctx.vision if tctx is not None else True


def is_http_url(path: str) -> bool:
    """是否为 http(s) 地址：图片 URL 不下载、不落本地，也不属于文件系统访问。"""
    return urlsplit(path).scheme.lower() in ("http", "https")


def image_mime(path: str) -> str | None:
    """按后缀判断图片 MIME；不是支持的图片格式时返回 None。

    URL 只看 path 部分（去掉 query / fragment）；本地路径原样取后缀，避免 # / ? 截断。
    """
    candidate = urlsplit(path).path if is_http_url(path) else path
    return IMAGE_MIME_TYPES.get(Path(candidate).suffix.lower())


def declared_image_mime(mime: str) -> str | None:
    """外部来源（MCP 等）自报的 MIME：归一化后不在支持列表内返回 None。

    与 `image_mime()` 对称——一个从后缀看，一个从声明的类型看；
    `IMAGE/PNG; charset=binary` 归一化成 `image/png`。
    """
    normalized = mime.split(";")[0].strip().lower()
    return normalized if normalized in _SUPPORTED_IMAGE_MIME_TYPES else None


def image_too_large(size: int) -> bool:
    """单图是否超过上限：read_file 与 MCP 返回的图片共用这一条规则。"""
    return size > MAX_IMAGE_BYTES


def data_url(mime: str, data: bytes) -> str:
    """原始字节 → `data:<mime>;base64,...`，可直接放进 image_url。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def attachment_result(source: str, mime: str, size: int | None, image_url: str) -> ToolResult:
    """带回图片附件的结果；`size` 是字节数，远程 URL 传 None（不下载，无字节数）。"""
    return ToolResult(
        content=_image_read_line(source, mime, size),
        images=(ImageAttachment(source=source, mime=mime, image_url=image_url),),
    )


def unsupported_vision_result(source: str, mime: str, size: int | None) -> ToolResult:
    """非视觉模型：只说明读到什么，不附图片。"""
    return ToolResult(content=f"{_image_read_line(source, mime, size)} {VISION_UNSUPPORTED_MESSAGE}")


def too_large_result(source: str, size: int) -> ToolResult:
    """超过单图上限：不附图片，报出实际大小与上限。"""
    return ToolResult.error(
        f"Image too large: {source} is {size} bytes (limit {MAX_IMAGE_BYTES} bytes).")


def unsupported_type_result(source: str, mime: str) -> ToolResult:
    """声明类型不在支持列表内：不附图片，列出支持的类型。"""
    supported = ", ".join(sorted(_SUPPORTED_IMAGE_MIME_TYPES))
    return ToolResult.error(
        f"Unsupported image type: {source} declares {mime!r} (supported: {supported}).")


def url_image_result(url: str, *, vision: bool) -> ToolResult:
    """图片 URL → 结果：URL 原样透传给接口，不下载、不校验内容。"""
    mime = _require_image_mime(url)
    if not vision:
        return unsupported_vision_result(url, mime, None)
    return attachment_result(url, mime, None, url)


def local_image_result(path: Path, *, vision: bool) -> ToolResult:
    """本地图片 → 结果（同步）：先过视觉 / 大小门控，再读字节编码成 data URL。"""
    source = str(path)
    mime = _require_image_mime(source)
    size = path.stat().st_size
    if (gated := _gate(source, mime, size, vision)) is not None:
        return gated
    return attachment_result(source, mime, size, data_url(mime, path.read_bytes()))


async def alocal_image_result(path: Path, *, vision: bool) -> ToolResult:
    """本地图片 → 结果（异步）：与 `local_image_result()` 一致，读字节用 aiofiles 不阻塞 loop。"""
    source = str(path)
    mime = _require_image_mime(source)
    size = path.stat().st_size
    if (gated := _gate(source, mime, size, vision)) is not None:
        return gated
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    return attachment_result(source, mime, size, data_url(mime, data))


def _image_read_line(source: str, mime: str, size: int | None) -> str:
    """结果正文的统一前缀：`Image read: <source> (<mime>, <size>).`"""
    size_text = "remote URL" if size is None else f"{size} bytes"
    return f"Image read: {source} ({mime}, {size_text})."


def _require_image_mime(source: str) -> str:
    """后缀不是图片即 ValueError；URL 与本地路径同一判定，只有报错文案区分。"""
    mime = image_mime(source)
    if mime is None:
        kind = "URL" if is_http_url(source) else "path"
        raise ValueError(f"Unsupported image {kind}: {source}")
    return mime


def _gate(source: str, mime: str, size: int, vision: bool) -> ToolResult | None:
    """不能附加图片时的结果（非视觉模型 / 超过上限）；可以附加时返回 None，由调用方读字节。"""
    if not vision:
        return unsupported_vision_result(source, mime, size)
    if image_too_large(size):
        return too_large_result(source, size)
    return None
