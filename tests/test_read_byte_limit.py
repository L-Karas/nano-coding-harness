"""read 工具的行数 / 字节双上限：任一先到即停，offset 从停止处续读。"""
import asyncio

import core.tools.base_tools.read as read_mod

KB = 1024


def _write(tmp_path, text):
    path = tmp_path / "sample.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_line_limit_stops_first(tmp_path):
    assert read_mod.run_read_file(_write(tmp_path, "a\nb\nc\nd\n"), limit=2) == (
        "a\nb\n[Truncated: (2) more lines. Use 'offset=3' to continue.]")


def test_byte_limit_stops_at_line_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_OUTPUT_BYTES", 2 * KB, raising=False)
    line = "a" * (KB - 1)  # 每行 1 KB（含换行）
    path = _write(tmp_path, "\n".join([line] * 4) + "\n")
    assert read_mod.run_read_file(path, limit=None) == (
        f"{line}\n{line}\n[Truncated: (2) more lines. Use 'offset=3' to continue.]")
    assert read_mod.run_read_file(path, limit=None, offset=3) == f"{line}\n{line}"


def test_byte_limit_cuts_line_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_OUTPUT_BYTES", 2 * KB, raising=False)
    first, second = "a" * (KB - 1), "b" * (2 * KB - 1)
    path = _write(tmp_path, f"{first}\n{second}\nc\n")
    assert read_mod.run_read_file(path, limit=None) == (
        f"{first}\n{'b' * KB}\n"
        "[Truncated: line 2 cut at 2 KB, 1 KB omitted; 1 more lines, continue with 'offset=3'.]")
    assert read_mod.run_read_file(path, limit=None, offset=3) == "c"


def test_overlong_line_is_cut_on_utf8_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_OUTPUT_BYTES", KB, raising=False)
    path = _write(tmp_path, "é" * 700)  # 单个 1400 字节的长行
    assert read_mod.run_read_file(path) == (
        "é" * 512 + "\n[Truncated: line 1 cut at 1 KB, 1 KB omitted.]")


def test_async_uses_the_same_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_OUTPUT_BYTES", 2 * KB, raising=False)
    line = "a" * (KB - 1)
    path = _write(tmp_path, "\n".join([line] * 4) + "\n")
    assert asyncio.run(read_mod.run_read_file_async(path, limit=None)) == (
        f"{line}\n{line}\n[Truncated: (2) more lines. Use 'offset=3' to continue.]")


def test_empty_file_returns_placeholder(tmp_path):
    assert read_mod.run_read_file(_write(tmp_path, "")) == "(Empty file)"


def test_blank_line_returns_placeholder(tmp_path):
    assert read_mod.run_read_file(_write(tmp_path, "\n")) == "(Empty file)"


def test_offset_past_end_returns_placeholder(tmp_path):
    assert read_mod.run_read_file(_write(tmp_path, "a\nb\n"), offset=10) == "(Empty file)"
