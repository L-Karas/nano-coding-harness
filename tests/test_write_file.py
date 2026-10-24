"""write_file：整文件写入、换行风格与权限保持、无变化短路、同步与异步同语义。"""
import asyncio
import os
import stat
from pathlib import Path

import pytest

from core.runtime_context import AgentRunContext, AgentInterrupted, ToolContext
from core.tools import ToolPool
from core.tools.base_tools.write import WriteFile


def _pool() -> ToolPool:
    return ToolPool({"write_file": WriteFile})


def _sync(tmp_path, **args):
    return _pool().execute_sync("write_file", args, ToolContext(cwd=tmp_path))


def _async(tmp_path, **args):
    return asyncio.run(_pool().execute("write_file", args, ToolContext(cwd=tmp_path)))


def test_creates_file_and_parent_directories(tmp_path):
    for call in (_sync, _async):
        name = f"{call.__name__}/c.txt"
        result = call(tmp_path, path=name, content="hi\n")
        assert not result.is_error, result.content
        assert result.content == f"Wrote 3 bytes to {name}."
        assert (tmp_path / name).read_text(encoding="utf-8") == "hi\n"


def test_sync_entry_delegates_to_same_implementation(tmp_path):
    """run 不再自带一套语义：同步与异步的消息与短路行为必须一致。"""
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    assert _sync(tmp_path, path="a.txt", content="hi\n").content == "No changes to a.txt."
    assert _async(tmp_path, path="a.txt", content="hi\n").content == "No changes to a.txt."
    # 同一个「新文件」内容不能写两遍：先同步建，再异步看到的就是无变化
    assert _sync(tmp_path, path="b.txt", content="hi\n").content == "Wrote 3 bytes to b.txt."
    assert _async(tmp_path, path="b.txt", content="hi\n").content == "No changes to b.txt."


def test_noop_write_does_not_rewrite(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    old_ns = 1_700_000_000_000_000_000
    os.utime(target, ns=(old_ns, old_ns))
    for call in (_sync, _async):
        assert call(tmp_path, path="a.txt", content="abc").content == "No changes to a.txt."
        assert target.stat().st_mtime_ns == old_ns


def test_empty_content_creates_empty_file(tmp_path):
    """content="" 且文件不存在时不能误判成「无变化」而跳过创建。"""
    for call in (_sync, _async):
        target = tmp_path / f"{call.__name__}.txt"
        assert call(tmp_path, path=target.name, content="").content.endswith("0 bytes to %s." % target.name)
        assert target.read_bytes() == b""


def test_byte_count_is_utf8_bytes(tmp_path):
    result = _async(tmp_path, path="zh.txt", content="中文内容")
    assert result.content == "Wrote 12 bytes to zh.txt."
    assert (tmp_path / "zh.txt").read_text(encoding="utf-8") == "中文内容"


def test_content_written_verbatim_without_trailing_newline(tmp_path):
    for call in (_sync, _async):
        target = tmp_path / f"{call.__name__}.txt"
        call(tmp_path, path=target.name, content="no trailing newline")
        assert target.read_bytes() == b"no trailing newline"


def test_crlf_content_never_doubles_carriage_returns(tmp_path):
    """回归：文本模式会把 content 里的 \\n 再翻一次，Windows 上写成 \\r\\r\\n。"""
    for call in (_sync, _async):
        target = tmp_path / f"{call.__name__}.txt"
        assert not call(tmp_path, path=target.name, content="a\r\nb\n").is_error
        assert target.read_bytes() == b"a\nb\n"


def test_existing_crlf_file_keeps_crlf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"line1\r\nline2\r\n")
    result = _async(tmp_path, path="a.txt", content="line1\nLINE2\n")
    assert not result.is_error, result.content
    assert target.read_bytes() == b"line1\r\nLINE2\r\n"


def test_existing_lf_file_keeps_lf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"line1\nline2\n")
    result = _async(tmp_path, path="a.txt", content="line1\nLINE2\n")
    assert not result.is_error, result.content
    assert target.read_bytes() == b"line1\nLINE2\n"


def test_mixed_eol_uses_majority_style(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"a\r\nb\nc\r\n")
    # 逻辑内容相同时是 no-op：归一化比较命中，不做整文件换行改写
    assert _async(tmp_path, path="a.txt", content="a\nb\nc\n").content == "No changes to a.txt."
    assert target.read_bytes() == b"a\r\nb\nc\r\n"
    # 内容变了才落盘，并按多数行（2 个 CRLF / 1 个 LF）统一为 CRLF
    result = _async(tmp_path, path="a.txt", content="a\nB\nc\n")
    assert not result.is_error, result.content
    assert target.read_bytes() == b"a\r\nB\r\nc\r\n"


@pytest.mark.skipif(os.name != "posix", reason="Windows 只有只读位")
def test_posix_readonly_file_is_replaced_and_keeps_mode(tmp_path):
    """覆盖只读文件靠的是 replace 而非文件写权限：改 mode 必须放在写完以后。"""
    target = tmp_path / "ro.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o444)
    result = _async(tmp_path, path="ro.txt", content="new\n")
    assert not result.is_error, result.content
    assert stat.S_IMODE(target.stat().st_mode) == 0o444
    assert target.read_text(encoding="utf-8") == "new\n"


def test_mode_preserved_after_content_is_written(tmp_path, monkeypatch):
    """POSIX 分支（跨平台可测）：chmod 用原文件 mode，且发生在写完数据之后——
    先改 mode 会让只读原文件的 open("w") 被拒。"""
    import core.shared.file_io as file_io

    target = tmp_path / "run.sh"
    target.write_text("old\n", encoding="utf-8")
    expected = stat.S_IMODE(target.stat().st_mode)
    chmods = []

    def fake_chmod(path, mode):
        chmods.append((Path(path).read_text(encoding="utf-8"), mode))

    monkeypatch.setattr(file_io, "_POSIX", True)
    monkeypatch.setattr(file_io.os, "chmod", fake_chmod)
    result = _async(tmp_path, path="run.sh", content="new\n")
    assert not result.is_error, result.content
    assert chmods == [("new\n", expected)]


@pytest.mark.skipif(os.name != "posix", reason="Windows 只有只读位")
def test_new_file_mode_follows_umask(tmp_path):
    umask = os.umask(0)
    os.umask(umask)
    _async(tmp_path, path="new.txt", content="x")
    assert stat.S_IMODE((tmp_path / "new.txt").stat().st_mode) == 0o666 & ~umask


@pytest.mark.skipif(os.name != "posix", reason="Windows 只有只读位")
def test_write_only_existing_file_is_overwritten(tmp_path):
    """旧文件不可读但可覆盖（0202）：读失败只当「风格未知」，不能让写入一起失败。"""
    target = tmp_path / "wo.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o202)
    result = _async(tmp_path, path="wo.txt", content="new\n")
    assert not result.is_error, result.content
    target.chmod(0o644)
    assert target.read_text(encoding="utf-8") == "new\n"


def test_non_utf8_existing_file_is_overwritten(tmp_path):
    """旧文件不是 UTF-8 时同步与异步都必须能覆盖，而不是报解码错误。"""
    for call in (_sync, _async):
        target = tmp_path / f"{call.__name__}.bin"
        target.write_bytes(b"\xff\xfe raw")
        result = call(tmp_path, path=target.name, content="new")
        assert not result.is_error, result.content
        assert target.read_bytes() == b"new"


def test_directory_target_is_rejected(tmp_path):
    (tmp_path / "d").mkdir()
    for call in (_sync, _async):
        result = call(tmp_path, path="d", content="x")
        assert result.is_error
        assert result.content.endswith("is a directory: d")


def test_error_message_hides_temp_name(tmp_path, monkeypatch):
    """写入失败时：临时文件被清掉、目标保持原样、错误信息不泄露临时文件名。"""
    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError(13, "Permission denied", str(src))

    monkeypatch.setattr(os, "replace", fail_replace)
    result = _async(tmp_path, path="a.txt", content="new\n")
    assert result.is_error
    assert "Permission denied" in result.content
    assert ".tmp" not in result.content
    assert str(tmp_path) in result.content
    assert [path.name for path in tmp_path.iterdir()] == ["a.txt"]
    assert target.read_text(encoding="utf-8") == "old\n"


def test_cancel_after_write_keeps_target_unchanged(tmp_path, monkeypatch):
    """「写完但还没 replace」时被中断：目标文件不动，临时文件不残留。"""
    ctx = AgentRunContext()
    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(os, "fsync", lambda fd: ctx.cancel())

    with pytest.raises(AgentInterrupted):
        asyncio.run(WriteFile(path="a.txt", content="new\n").arun(ToolContext(agent_run=ctx, cwd=tmp_path)))

    assert target.read_text(encoding="utf-8") == "old\n"
    assert [path.name for path in tmp_path.iterdir()] == ["a.txt"]
