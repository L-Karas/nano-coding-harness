"""edit_file：唯一匹配、换行符 / 权限保持、无变化短路、同步与异步同语义。"""
import asyncio
import os
import stat

import pytest

from core.runtime_context import ToolContext
from core.tools import ToolPool
from core.tools.base_tools.edit import EditFile


def _pool() -> ToolPool:
    return ToolPool({"edit_file": EditFile})


def _sync(tmp_path, **args):
    return _pool().execute_sync("edit_file", args, ToolContext(cwd=tmp_path))


def _async(tmp_path, **args):
    return asyncio.run(_pool().execute("edit_file", args, ToolContext(cwd=tmp_path)))


def test_empty_old_text_rejected(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    for call in (_sync, _async):
        result = call(tmp_path, path="a.txt", old_text="", new_text="PWNED")
        assert result.is_error
        assert "old_text must not be empty" in result.content
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_missing_file_is_error(tmp_path):
    for call in (_sync, _async):
        result = call(tmp_path, path="missing.txt", old_text="a", new_text="b")
        assert result.is_error
        assert "file not found: missing.txt" in result.content


def test_old_text_not_found(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    for call in (_sync, _async):
        result = call(tmp_path, path="a.txt", old_text="missing", new_text="x")
        assert result.is_error
        assert "old_text not found in a.txt" in result.content
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_multiple_occurrences_rejected_with_line_numbers(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("same\nother\nsame\n", encoding="utf-8")
    for call in (_sync, _async):
        result = call(tmp_path, path="a.txt", old_text="same", new_text="changed")
        assert result.is_error
        assert "multiple times" in result.content
        assert "lines 1, 3" in result.content
    assert target.read_text(encoding="utf-8") == "same\nother\nsame\n"


def test_overlapping_match_is_ambiguous(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("aaa", encoding="utf-8")
    result = _async(tmp_path, path="a.txt", old_text="aa", new_text="b")
    assert result.is_error
    assert "multiple times" in result.content
    assert target.read_text(encoding="utf-8") == "aaa"


def test_sync_entry_delegates_to_same_implementation(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="multiple times"):
        EditFile(path="a.txt", old_text="x", new_text="y").run(ToolContext(cwd=tmp_path))
    assert target.read_text(encoding="utf-8") == "x\nx\n"


def test_lf_file_keeps_lf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"line1\nline2\n")
    result = _async(tmp_path, path="a.txt", old_text="line2", new_text="LINE2")
    assert not result.is_error, result.content
    assert result.content == "Edited a.txt."
    assert target.read_bytes() == b"line1\nLINE2\n"
    assert [path.name for path in tmp_path.iterdir()] == ["a.txt"]


def test_crlf_file_keeps_crlf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"line1\r\nline2\r\n")
    # 模型从 read_file 拿到的是归一化后的 \n，仍应命中 CRLF 文件
    result = _async(tmp_path, path="a.txt", old_text="line1\nline2", new_text="line1\nLINE2")
    assert not result.is_error, result.content
    assert target.read_bytes() == b"line1\r\nLINE2\r\n"


def test_mixed_eol_uses_majority_style(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"a\r\nb\nc\r\n")
    # 混合换行按多数行统一（这里 2 个 CRLF、1 个 LF），行为由文档固定
    result = _async(tmp_path, path="a.txt", old_text="b", new_text="B")
    assert not result.is_error, result.content
    assert target.read_bytes() == b"a\r\nB\r\nc\r\n"


def test_empty_new_text_deletes_match(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("keep\nremove\n", encoding="utf-8")
    result = _async(tmp_path, path="a.txt", old_text="remove\n", new_text="")
    assert not result.is_error, result.content
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_replace_failure_removes_tmp(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", fail_replace)
    result = _async(tmp_path, path="a.txt", old_text="hello", new_text="bye")
    assert result.is_error
    assert "boom" in result.content
    assert [path.name for path in tmp_path.iterdir()] == ["a.txt"]


def test_noop_edit_does_not_rewrite(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    old_ns = 1_700_000_000_000_000_000
    os.utime(target, ns=(old_ns, old_ns))
    before = target.stat().st_mtime_ns
    result = _async(tmp_path, path="a.txt", old_text="abc", new_text="abc")
    assert result.content == "No changes to a.txt."
    assert target.stat().st_mtime_ns == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_edit_preserves_file_mode(tmp_path):
    target = tmp_path / "script.sh"
    target.write_text("echo hi\n", encoding="utf-8")
    os.chmod(target, 0o755)
    result = _async(tmp_path, path="script.sh", old_text="hi", new_text="bye")
    assert not result.is_error, result.content
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
