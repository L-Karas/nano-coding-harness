"""core.tools.vision：MIME 判定、data URL 编码、视觉 / 大小门控、超限缩小与图片附件结果。"""
import asyncio
import base64
import io
import os

import pytest
from PIL import Image

import core.tools.vision as vision
from core.runtime_context import ToolContext
from core.tools import ImageAttachment, ToolResult

PNG = b"\x89PNG\r\n\x1a\n"


def _noise_jpeg(width: int = 400, height: int = 400) -> bytes:
    buf = io.BytesIO()
    Image.frombytes("RGB", (width, height), os.urandom(width * height * 3)).save(
        buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_max_image_bytes_is_5mb():
    assert vision.MAX_IMAGE_BYTES == 5 * 1024 * 1024


def test_supports_vision_reads_context_and_defaults_to_true():
    assert vision.supports_vision(None) is True
    assert vision.supports_vision(ToolContext()) is True
    assert vision.supports_vision(ToolContext(vision=False)) is False


@pytest.mark.parametrize(
    ("path", "mime"),
    [("a.png", "image/png"), ("a.PNG", "image/png"), ("a.jpg", "image/jpeg"),
     ("a.jpeg", "image/jpeg"), ("a.gif", "image/gif"), ("a.webp", "image/webp"),
     ("/tmp/shot#1.png", "image/png"),
     ("https://example.com/shot.png?raw=1#frag", "image/png"),
     ("a.txt", None), ("https://example.com/page.html", None)],
)
def test_image_mime_from_suffix(path, mime):
    assert vision.image_mime(path) == mime


def test_is_http_url():
    assert vision.is_http_url("https://example.com/a.png") is True
    assert vision.is_http_url("HTTP://example.com/a.png") is True
    assert vision.is_http_url("a.png") is False


def test_data_url_encodes_base64():
    assert vision.data_url("image/png", PNG) == f"data:image/png;base64,{base64.b64encode(PNG).decode()}"


@pytest.mark.parametrize(
    ("mime", "expected"),
    [("image/png", "image/png"), ("IMAGE/PNG", "image/png"),
     ("image/jpeg; charset=binary", "image/jpeg"), (" image/webp ", "image/webp"),
     ("image/bmp", None), ("application/octet-stream", None), ("", None)],
)
def test_declared_image_mime_normalizes_and_validates(mime, expected):
    assert vision.declared_image_mime(mime) == expected


def test_unsupported_type_result_lists_the_supported_types():
    result = vision.unsupported_type_result("shot", "image/bmp")
    assert result.is_error is True
    assert "image/bmp" in result.content
    assert "image/png" in result.content and "image/webp" in result.content


def test_url_image_result_passes_url_through():
    url = "https://example.com/shot.png"
    assert vision.url_image_result(url, vision=True) == ToolResult(
        content=f"Image read: {url} (image/png, remote URL).",
        images=(ImageAttachment(source=url, mime="image/png", image_url=url),),
    )


def test_url_image_result_without_vision_has_no_attachment():
    result = vision.url_image_result("https://example.com/shot.png", vision=False)
    assert result.images == ()
    assert vision.VISION_UNSUPPORTED_MESSAGE in result.content


def test_url_image_result_rejects_non_image_suffix():
    with pytest.raises(ValueError, match="Unsupported image URL"):
        vision.url_image_result("https://example.com/page.html", vision=True)


def test_local_image_result_attaches_data_url(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    assert vision.local_image_result(path, vision=True) == ToolResult(
        content=f"Image read: {path} (image/png, {len(PNG)} bytes).",
        images=(ImageAttachment(source=str(path), mime="image/png",
                                image_url=f"data:image/png;base64,{base64.b64encode(PNG).decode()}"),),
    )


def test_local_image_result_rejects_non_image_path(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hi")
    with pytest.raises(ValueError, match="Unsupported image path"):
        vision.local_image_result(path, vision=True)


def test_local_image_result_without_vision_skips_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "data_url", lambda *args: pytest.fail("must not encode"))
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = vision.local_image_result(path, vision=False)
    assert result.images == ()
    assert "does not support vision" in result.content


def test_local_image_over_limit_is_error_without_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", len(PNG) - 1)
    monkeypatch.setattr(vision, "data_url", lambda *args: pytest.fail("must not encode"))
    path = tmp_path / "big.png"
    path.write_bytes(PNG)
    result = vision.local_image_result(path, vision=True)
    assert result.is_error is True
    assert result.images == ()
    assert f"limit {len(PNG) - 1} bytes" in result.content


def test_shrink_image_fits_oversized_png_under_the_limit(monkeypatch, noise_png):
    data = noise_png()
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    assert len(data) > 50_000
    mime, shrunk = vision.shrink_image(data)
    assert mime == "image/png"
    assert len(shrunk) <= 50_000
    with Image.open(io.BytesIO(shrunk)) as img:
        assert img.size[0] < 400 and img.size[1] < 400


def test_shrink_image_keeps_the_original_format(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    mime, shrunk = vision.shrink_image(_noise_jpeg())
    assert mime == "image/jpeg"
    assert len(shrunk) <= 50_000
    with Image.open(io.BytesIO(shrunk)) as img:
        assert img.format == "JPEG"


def test_shrink_image_leaves_an_image_within_the_limit_untouched(noise_png):
    data = noise_png(20, 20)
    assert vision.shrink_image(data) == ("image/png", data)


def test_shrink_image_returns_none_for_bytes_it_cannot_decode(monkeypatch):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", len(PNG) - 1)
    assert vision.shrink_image(PNG) is None


def test_shrink_image_returns_none_when_even_one_pixel_is_too_big(monkeypatch, noise_png):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 10)
    assert vision.shrink_image(noise_png(40, 40)) is None


def test_shrink_image_refuses_an_animated_gif(monkeypatch):
    frames = [Image.new("RGB", (60, 60), (255, 0, 0)), Image.new("RGB", (60, 60), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100)
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", len(buf.getvalue()) - 1)
    assert vision.shrink_image(buf.getvalue()) is None


def test_local_image_over_limit_is_shrunk_into_an_attachment(tmp_path, monkeypatch, noise_png):
    data = noise_png()
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    path = tmp_path / "big.png"
    path.write_bytes(data)
    result = vision.local_image_result(path, vision=True)
    assert result.is_error is False
    (attachment,) = result.images
    assert attachment.mime == "image/png"
    assert attachment.image_url.startswith("data:image/png;base64,")
    assert len(base64.b64decode(attachment.image_url.split(",", 1)[1])) <= 50_000


def test_local_image_reports_the_shrink_in_its_line(tmp_path, monkeypatch, noise_png):
    data = noise_png()
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    path = tmp_path / "big.png"
    path.write_bytes(data)
    result = vision.local_image_result(path, vision=True)
    attached = base64.b64decode(result.images[0].image_url.split(",", 1)[1])
    assert result.content == (
        f"Image read: {path} (image/png, {len(attached)} bytes, "
        f"downscaled from {len(data)} bytes).")


def test_alocal_image_result_shrinks_like_the_sync_one(tmp_path, monkeypatch, noise_png):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 50_000)
    path = tmp_path / "big.png"
    path.write_bytes(noise_png())
    result = asyncio.run(vision.alocal_image_result(path, vision=True))
    assert result.is_error is False
    assert "downscaled" in result.content
    assert result == vision.local_image_result(path, vision=True)


def test_alocal_image_result_matches_sync(tmp_path):
    path = tmp_path / "shot.webp"
    path.write_bytes(PNG)
    expected = vision.local_image_result(path, vision=True)
    assert asyncio.run(vision.alocal_image_result(path, vision=True)) == expected


def test_alocal_image_result_without_vision_skips_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "data_url", lambda *args: pytest.fail("must not encode"))
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = asyncio.run(vision.alocal_image_result(path, vision=False))
    assert result.images == ()
