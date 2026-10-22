"""图片读取的视觉处理：MIME 判定、大小上限与超限缩小、视觉能力门控与图片附件结果。

`read_file` 只负责「路径 → 分支」，图片分支的判定与文案都下沉到这里。其他方法 / 模块想随
工具结果带回图片（截图、网页抓图等）时，直接调 `local_image_result()` /
`alocal_image_result()` / `url_image_result()`，不必复制这份判定；字节已经在手上的来源（MCP）
用 `fit_image()` + `data_url()` 拼附件。
"""
import asyncio
import base64
import io
import math
from pathlib import Path
from urllib.parse import urlsplit

import aiofiles
from PIL import Image, ImageOps

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

# 重编码时的 MIME 只看 Pillow 识别出的真实格式：后缀与外部来源自报的类型都只是声明
_PIL_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}
_SHRINK_STEP = 0.75  # 按字节数估的缩放比例不够时，每轮再缩 25%

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


def fit_image(mime: str, data: bytes) -> tuple[str, bytes, int | None] | None:
    """字节 → 可附件的 (mime, 字节, 原图大小)：超上限的等比缩小，未超上限的原样返回。

    原图大小只在缩小过时给出，未缩小时为 None；缩不下来（解不开 / 多帧动图 / 1x1 仍超限）
    返回 None，由调用方按「太大」报错。
    """
    if not image_too_large(len(data)):
        return mime, data, None
    shrunk = shrink_image(data)
    if shrunk is None:
        return None
    mime, shrunk_data = shrunk
    return mime, shrunk_data, len(data)


def shrink_image(data: bytes) -> tuple[str, bytes] | None:
    """把超上限的图片等比缩小到上限以内：返回 (真实格式的 mime, 重编码后的字节)。

    已在上限内的图片原样返回。解不开（含多帧动图）/ 缩到 1x1 仍超限时返回 None，
    由调用方按「太大」报错。重编码保留原格式，JPEG / WebP 按 85 质量输出。
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            mime = _PIL_MIME_TYPES.get(img.format or "")
            # 只有多帧插件才有 is_animated；缩放只处理单帧，动图交回「太大」报错
            if mime is None or getattr(img, "is_animated", False):
                return None
            img.load()
            if not image_too_large(len(data)):
                return mime, data
            # 手机照片的方向存在 EXIF 里，重编码会丢掉它，先烧进像素
            frame = ImageOps.exif_transpose(img)
            width, height = frame.size
            # 先按「字节数 ∝ 像素数」估一个比例，通常一次重编码就能落进上限
            scale = math.sqrt(MAX_IMAGE_BYTES / len(data))
            while True:
                size = (max(1, round(width * scale)), max(1, round(height * scale)))
                encoded = _encode(frame.resize(size, Image.Resampling.LANCZOS), mime)
                if not image_too_large(len(encoded)):
                    return mime, encoded
                if size == (1, 1):
                    return None
                scale *= _SHRINK_STEP
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def downscaled_note(count: int) -> str:
    """缩小过的图片在正文里单独一行说明，模型才知道看到的是缩略版。"""
    images = "image" if count == 1 else "images"
    return f"{count} {images} {'was' if count == 1 else 'were'} downscaled to fit the size limit."


def data_url(mime: str, data: bytes) -> str:
    """原始字节 → `data:<mime>;base64,...`，可直接放进 image_url。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def attachment_result(source: str, mime: str, size: int | None, image_url: str,
                      *, original_size: int | None = None) -> ToolResult:
    """带回图片附件的结果；`size` 是字节数，远程 URL 传 None（不下载，无字节数）。

    `original_size` 只在图片被缩小过时给出，正文据此注明原图大小。
    """
    return ToolResult(
        content=_image_read_line(source, mime, size, original_size),
        images=(ImageAttachment(source=source, mime=mime, image_url=image_url),),
    )


def unsupported_vision_result(source: str, mime: str, size: int | None) -> ToolResult:
    """非视觉模型：只说明读到什么，不附图片。"""
    return ToolResult(content=f"{_image_read_line(source, mime, size)} {VISION_UNSUPPORTED_MESSAGE}")


def too_large_result(source: str, size: int) -> ToolResult:
    """超过单图上限且缩不下来：不附图片，报出实际大小与上限。"""
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
    """本地图片 → 结果（同步）：先过视觉门控，超过上限时缩小，再编码成 data URL。"""
    source = str(path)
    mime = _require_image_mime(source)
    size = path.stat().st_size
    if not vision:
        return unsupported_vision_result(source, mime, size)
    return _bytes_result(source, mime, path.read_bytes())


async def alocal_image_result(path: Path, *, vision: bool) -> ToolResult:
    """本地图片 → 结果（异步）：与 `local_image_result()` 一致；读字节用 aiofiles，
    缩小 / 重编码是 CPU 活，放进线程不阻塞 loop。"""
    source = str(path)
    mime = _require_image_mime(source)
    size = path.stat().st_size
    if not vision:
        return unsupported_vision_result(source, mime, size)
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    return await asyncio.to_thread(_bytes_result, source, mime, data)


def _image_read_line(source: str, mime: str, size: int | None,
                     original_size: int | None = None) -> str:
    """结果正文的统一前缀：`Image read: <source> (<mime>, <size>[, downscaled from <size>]).`"""
    size_text = "remote URL" if size is None else f"{size} bytes"
    shrink_text = "" if original_size is None else f", downscaled from {original_size} bytes"
    return f"Image read: {source} ({mime}, {size_text}{shrink_text})."


def _require_image_mime(source: str) -> str:
    """后缀不是图片即 ValueError；URL 与本地路径同一判定，只有报错文案区分。"""
    mime = image_mime(source)
    if mime is None:
        kind = "URL" if is_http_url(source) else "path"
        raise ValueError(f"Unsupported image {kind}: {source}")
    return mime


def _bytes_result(source: str, mime: str, data: bytes) -> ToolResult:
    """字节 → 附件结果：超限先缩小（MIME 以缩小后的编码为准），缩不下来按「太大」报错。"""
    fitted = fit_image(mime, data)
    if fitted is None:
        return too_large_result(source, len(data))
    mime, data, original_size = fitted
    return attachment_result(source, mime, len(data), data_url(mime, data),
                             original_size=original_size)


def _encode(img: Image.Image, mime: str) -> bytes:
    """按 mime 重编码缩小后的图：JPEG / WebP 走 85 质量，JPEG 不收 alpha，RGB / L 之外先转 RGB。"""
    buf = io.BytesIO()
    if mime == "image/jpeg":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=85)
    elif mime == "image/webp":
        img.save(buf, format="WEBP", quality=85)
    elif mime == "image/gif":
        img.save(buf, format="GIF", optimize=True)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()
