"""共享工具输出截断：行数/字节双上限，返回 (text, truncated)，供 read 与其他工具消息共用。"""
from core.context.truncate import MAX_OUTPUT_LINES, truncate_tool_output

KB = 1024


def test_short_text_is_untouched_and_reports_not_truncated():
    text = "a\nb\nc"
    assert truncate_tool_output(text) == (text, False)


def test_exactly_at_line_limit_is_not_truncated():
    text = "\n".join(["x"] * 3)
    assert truncate_tool_output(text, limit=3) == (text, False)


def test_line_limit_appends_offset_notice_and_reports_truncated():
    text = "\n".join(["line"] * 5)
    result, truncated = truncate_tool_output(text, limit=2)
    assert truncated is True
    assert result == "line\nline\n[Truncated: (3) more lines. Use 'offset=3' to continue.]"


def test_default_line_limit_is_max_output_lines():
    text = "\n".join(f"line{i}" for i in range(MAX_OUTPUT_LINES + 10))
    result, truncated = truncate_tool_output(text)
    assert truncated is True
    assert result.endswith(f"[Truncated: (10) more lines. Use 'offset={MAX_OUTPUT_LINES + 1}' to continue.]")


def test_offset_continues_from_given_line():
    text = "\n".join(["a", "b", "c", "d"])
    assert truncate_tool_output(text, limit=2, offset=3) == ("c\nd", False)


def test_byte_limit_cuts_line_and_reports_truncated():
    first = "a" * (KB - 1)
    second = "b" * (2 * KB - 1)
    text = f"{first}\n{second}\nc"
    result, truncated = truncate_tool_output(text, limit=None, max_bytes=2 * KB)
    assert truncated is True
    assert result == (
        f"{first}\n{'b' * KB}\n"
        "[Truncated: line 2 cut at 2 KB, 1 KB omitted; 1 more lines, continue with 'offset=3'.]")


def test_overlong_line_is_cut_on_utf8_boundary():
    text = "é" * 700  # 单行 1400 字节
    result, truncated = truncate_tool_output(text, limit=None, max_bytes=KB)
    assert truncated is True
    assert result == "é" * 512 + "\n[Truncated: line 1 cut at 1 KB, 1 KB omitted.]"
