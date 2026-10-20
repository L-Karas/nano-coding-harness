"""core.tools.vision：MIME 判定、data URL 编码、视觉 / 大小门控与图片附件结果。"""
import asyncio
import base64

import pytest

import core.tools.vision as vision
from core.runtime_context import ToolContext
from core.tools import ImageAttachment, ToolResult

PNG = b"\x89PNG\r\n\x1a\n"


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
