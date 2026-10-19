"""read_file 图片分支：本地 png/jpg/jpeg/gif/webp → data URL；URL 透传；5 MB 上限。"""
import asyncio
import base64

import pytest

import core.tools.base_tools.read as read_mod
from core.tools import ImageAttachment, ToolResult
from core.tools.base_tools.read import ReadFile

PNG = b"\x89PNG\r\n\x1a\n"


def _read(path):
    return ReadFile(path=str(path)).run()


def _read_async(path):
    return asyncio.run(ReadFile(path=str(path)).arun())


def test_max_image_bytes_is_5mb():
    assert read_mod.MAX_IMAGE_BYTES == 5 * 1024 * 1024


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
            url=f"data:{mime};base64,{base64.b64encode(PNG).decode()}",
        ),
    )
    assert result.content.startswith("Image read:")


def test_async_image_read_matches_sync(tmp_path):
    path = tmp_path / "shot.PNG"
    path.write_bytes(PNG)
    assert _read_async(path) == _read(path)


def test_image_at_limit_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_IMAGE_BYTES", len(PNG))
    path = tmp_path / "edge.png"
    path.write_bytes(PNG)
    assert not _read(path).is_error


def test_image_over_limit_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_IMAGE_BYTES", len(PNG) - 1)
    path = tmp_path / "big.png"
    path.write_bytes(PNG)
    result = _read(path)
    assert result.is_error is True
    assert result.images == ()
    assert result.content.startswith("[Tool Error]:")


def test_http_image_url_passes_through_without_fetch():
    url = "https://example.com/shot.PNG?raw=1"
    result = ReadFile(path=url).run()
    assert result.images == (ImageAttachment(source=url, mime="image/png", url=url),)
    assert result.content.startswith("Image read:")


def test_unsupported_http_url_is_error():
    with pytest.raises(ValueError, match="Unsupported image URL"):
        ReadFile(path="https://example.com/page.html").run()
