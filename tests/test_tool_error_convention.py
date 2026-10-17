"""工具失败的唯一信号是异常：ToolPool 把异常编码成 is_error=True 的 ToolResult。

工具返回的字符串一律是内容，不解析前缀（见 docs/adr/0001-tool-failure-is-exception.md）。
内部消息不得再带 "Error:" 前缀（避免 "[Tool Error]: Error: ..." 双重前缀）。
"""

from core.template import TOOL_ABORTED_PROMPT, TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX
from core.tools import ToolResult, ToolPool
from core.tools.base_tools import EditFile, Grep, ReadFile, WriteFile  # noqa: F401 注册工具
from core.tools.tool_base import BaseTool


def _pool():
    return ToolPool({"edit_file": EditFile, "write_file": WriteFile,
                     "grep": Grep, "read_file": ReadFile})


def test_edit_error_single_prefix():
    result = _pool().execute_sync(
        "edit_file", {"path": "not_exist.py", "old_text": "a", "new_text": "b"})
    assert result.is_error
    assert result.content.startswith(TOOL_ERROR_PREFIX)
    assert "Error: Error" not in result.content, f"不得双重前缀: {result.content}"


def test_write_missing_path_single_prefix():
    # 父路径为已有文件 → mkdir 必然失败
    result = _pool().execute_sync(
        "write_file", {"path": "pyproject.toml/x.py", "content": "x"})
    assert result.is_error
    assert result.content.startswith(TOOL_ERROR_PREFIX)
    assert "Error: Error" not in result.content


def test_grep_bad_pattern_single_prefix():
    result = _pool().execute_sync("grep", {"pattern": "(", "path": "."})
    assert result.is_error
    assert result.content.startswith(TOOL_ERROR_PREFIX)
    assert "Error: Error" not in result.content


def test_unknown_tool_prefix():
    assert _pool().execute_sync("no_such_tool", {}) == ToolResult(
        content=f"{UNKNOWN_TOOL_PREFIX} no_such_tool", is_error=True)


def test_aborted_prompt_carries_error_prefix():
    """未执行的 tool call 占位与执行失败同一前缀，TUI 回放按前缀识别 error 卡。"""
    assert TOOL_ABORTED_PROMPT == "[Tool Error]: tool call aborted before execution"


def test_missing_file_raises_not_returns():
    """回归：失败必须由异常上抛、由 ToolPool 编码成前缀错误（旧实现曾 return "Error: ..." 绕过约定）。"""
    result = _pool().execute_sync("read_file", {"path": "no-such-file.txt"})
    assert result.is_error
    assert result.content.startswith(TOOL_ERROR_PREFIX)


def test_prefixed_string_return_is_content():
    """失败的唯一信号是异常：工具返回 "[Tool Error]:" 开头的字符串也按成功内容处理。"""
    class Prefixed(BaseTool):
        """Prefixed."""

        def run(self, tctx=None):
            return f"{TOOL_ERROR_PREFIX} not a real failure"

    result = ToolPool({"prefixed": Prefixed}).execute_sync("prefixed", {})
    assert result == ToolResult(content=f"{TOOL_ERROR_PREFIX} not a real failure")
    assert result.is_error is False
