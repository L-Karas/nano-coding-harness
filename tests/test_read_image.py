"""read_file 图片分支：本地 png/jpg/jpeg/gif/webp → data URL；URL 透传；5 MB 上限。"""
import asyncio
import base64
import os

import pytest

import core.tools.vision as vision_mod
from core.runtime_context import ToolContext
from core.tools import ImageAttachment, ToolResult
from core.tools.base_tools.read import ReadFile

PNG = b"\x89PNG\r\n\x1a\n"


def _read(path, tctx=None):
    return ReadFile(path=str(path)).run(tctx)


def _read_async(path, tctx=None):
    return asyncio.run(ReadFile(path=str(path)).arun(tctx))


@pytest.mark.parametrize(
    ("suffix", "mime"),
    [(".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
     (".gif", "image/gif"), (".webp", "image/webp")],
)
def test_local_image_returns_data_url(tmp_path, suffix, mime):
    path = tmp_path / f"shot{suffix}"
    path.write_bytes(PNG)
    result = _read(path)
    assert isinstance(result, ToolResult)
    assert result.images == (
        ImageAttachment(
            source=str(path.resolve()),
            mime=mime,
            image_url=f"data:{mime};base64,{base64.b64encode(PNG).decode()}",
        ),
    )
    assert result.content.startswith("Image read:")


def test_async_image_read_matches_sync(tmp_path):
    path = tmp_path / "shot.PNG"
    path.write_bytes(PNG)
    assert _read_async(path) == _read(path)


def test_image_at_limit_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_mod, "MAX_IMAGE_BYTES", len(PNG))
    path = tmp_path / "edge.png"
    path.write_bytes(PNG)
    assert not _read(path).is_error


def test_image_over_limit_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_mod, "MAX_IMAGE_BYTES", len(PNG) - 1)
    path = tmp_path / "big.png"
    path.write_bytes(PNG)
    result = _read(path)
    assert result.is_error is True
    assert result.images == ()
    assert result.content.startswith("[Tool Error]:")


def test_http_image_url_passes_through_without_fetch():
    url = "https://example.com/shot.PNG?raw=1"
    result = ReadFile(path=url).run()
    assert result.images == (ImageAttachment(source=url, mime="image/png", image_url=url),)
    assert result.content.startswith("Image read:")


def test_unsupported_http_url_is_error():
    with pytest.raises(ValueError, match="Unsupported image URL"):
        ReadFile(path="https://example.com/page.html").run()


def test_local_filename_with_hash_keeps_image_suffix(tmp_path):
    path = tmp_path / "shot#1.png"
    path.write_bytes(PNG)
    result = _read(path)
    assert isinstance(result, ToolResult)
    assert result.images[0].mime == "image/png"


@pytest.mark.skipif(os.name == "nt", reason="? is not a valid Windows filename")
def test_local_filename_with_question_mark_keeps_image_suffix(tmp_path):
    path = tmp_path / "shot?1.png"
    path.write_bytes(PNG)
    result = _read(path)
    assert isinstance(result, ToolResult)
    assert result.images[0].mime == "image/png"


def test_read_image_without_vision_skips_attachment_and_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_mod, "data_url", lambda *args: pytest.fail("must not encode"))
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = _read(path, ToolContext(vision=False))
    assert result.images == ()
    assert "does not support vision" in result.content


def test_read_url_without_vision_skips_attachment():
    result = _read("https://example.com/shot.png", ToolContext(vision=False))
    assert result.images == ()
    assert "does not support vision" in result.content


def test_async_read_image_without_vision_skips_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_mod, "data_url", lambda *args: pytest.fail("must not encode"))
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    result = _read_async(path, ToolContext(vision=False))
    assert result.images == ()
    assert "does not support vision" in result.content
