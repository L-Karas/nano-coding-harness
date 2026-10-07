"""工具执行异常式约定的回归保护：

run_* 抛异常 → call_tool_handler 是唯一格式化点，输出统一以 "[Tool Error]:" / "[Unknown Tool]:"
开头；内部消息不得再带 "Error:" 前缀（避免 "[Tool Error]: Error: ..." 双重前缀）。
"""

from core.template import TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX
from core.tools.base_tools import edit, grep, write  # noqa: F401 注册工具
from core.tools.extra_tools import task
from core.tools.tool_loader import call_tool_handler

FAIL_PREFIXES = (TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX)


def test_edit_error_single_prefix():
    out = call_tool_handler(edit.run_edit_file, {"path": "not_exist.py", "old_text": "a", "new_text": "b"},
                            "edit_file")
    assert out.startswith(FAIL_PREFIXES)
    assert "Error: Error" not in out, f"不得双重前缀: {out}"


def test_write_missing_path_single_prefix():
    # 父路径为已有文件 → mkdir 必然失败
    out = call_tool_handler(write.run_write_file, {"path": "pyproject.toml/x.py", "content": "x"}, "write_file")
    assert out.startswith(FAIL_PREFIXES)
    assert "Error: Error" not in out


def test_grep_bad_pattern_single_prefix():
    out = call_tool_handler(grep.run_grep, {"pattern": "(", "path": "."}, "grep")
    assert out.startswith(FAIL_PREFIXES)
    assert "Error: Error" not in out


def test_unknown_tool_prefix():
    assert call_tool_handler(None, {}, "no_such_tool") == f"{UNKNOWN_TOOL_PREFIX} no_such_tool"


def test_task_not_found_raises_not_returns():
    """回归：旧实现 return "Error: ..." 会绕过前缀约定、被当成成功。"""
    out = call_tool_handler(task.run_get_task, {"task_id": "no-such-task"}, "get_task")
    assert out.startswith(FAIL_PREFIXES)
