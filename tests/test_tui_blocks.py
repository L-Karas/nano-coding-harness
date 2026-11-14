"""展示值构造（core.tui.blocks）：纯测试，不启动 Textual。"""
from core.tui.blocks import (
    DEFAULT_MAX_LINES,
    TOOL_ARGS_MAX_LINES,
    assistant_markdown,
    background_notice,
    dim_body,
    format_args,
    permission,
    sessions_empty,
    split_trailing_keycap,
    strip_keycaps,
    tool_call,
    tool_result,
    user_input,
)


def test_tool_call_block_carries_merge_id_and_cap():
    block = tool_call("c1", "terminal", {"command": "ls -la"})
    assert block.kind == "tool"
    assert block.tool_call_id == "c1"
    assert block.max_lines == TOOL_ARGS_MAX_LINES
    assert block.head_lines == 1, "Tool Call: 标题行固定不折叠"
    assert block.body.plain.startswith("Tool Call: terminal\n")
    assert "command: ls -la" in block.body.plain


def test_tool_call_keeps_brackets_literal():
    """正文走 Text 而非 Rich 标记：参数里的方括号不得被当成样式标签。"""
    block = tool_call("c1", "terminal", {"command": "echo [bold]x[/bold]"})
    assert "[bold]x[/bold]" in block.body.plain
    assert "bold" not in {span.style for span in block.body.spans}


def test_tool_call_cap_is_overridable():
    assert tool_call("c1", "t", {}).max_lines == TOOL_ARGS_MAX_LINES
    assert tool_call("c1", "t", {}, max_lines=9).max_lines == 9


def test_tool_result_infers_failure_and_keeps_merge_id():
    ok = tool_result("c1", "written")
    assert (ok.kind, ok.tool_call_id) == ("result", "c1")
    assert ok.head_lines == 1 and ok.max_lines == DEFAULT_MAX_LINES
    assert ok.tail is None
    assert ok.body.plain.startswith("Tool Result: \n")

    explicit_error = tool_result("c2", "written", is_error=True)
    assert explicit_error.kind == "error"
    inferred_error = tool_result("c3", "[Tool Error]: boom")
    assert inferred_error.kind == "error"
    unknown = tool_result("c4", "[Unknown Tool]: nope")
    assert unknown.kind == "error", "未知工具按失败渲染"


def test_tool_result_with_diff_puts_result_in_tail():
    block = tool_result("c1", "Wrote 42 bytes to a.py.",
                        diff=[(" ", 1, "def main():"), ("-", 2, "old"), ("+", 2, "new")])
    assert block.kind == "result"
    assert block.tail is not None and "Wrote 42 bytes" in block.tail.plain
    assert "1 │ def main():" in block.body.plain
    assert "-2 │ old" in block.body.plain and "+2 │ new" in block.body.plain
    assert "Wrote 42 bytes" not in block.body.plain, "结果正文固定收尾，不参与折叠"


def test_tool_result_without_diff_has_no_tail():
    assert tool_result("c1", "ok").tail is None


def test_user_and_assistant_blocks():
    user = user_input("run 1️⃣ now")
    assert user.kind == "user" and user.body.plain == "run 1 now"
    assert user.max_lines is None, "用户输入不折叠"

    md = assistant_markdown("## 1️⃣ 最新")
    assert md.kind == "assistant"
    assert md.body == "## 1 最新", "Markdown 正文保持 str，键帽已降级"


def test_notice_and_sessions_blocks():
    notice = background_notice("1️⃣ done")
    assert notice.kind == "notice" and notice.head_lines == 1
    assert notice.max_lines == DEFAULT_MAX_LINES
    assert "🔔 Background Task" in notice.body.plain

    empty = sessions_empty()
    assert empty.kind == "sessions" and empty.body.plain == "No sessions yet"


def test_permission_block_separates_details_with_blank_line():
    block = permission("Allow write?", ["path: /tmp/a", "", "command: rm"])
    assert block.kind == "perm"
    assert block.max_lines is None and block.tool_call_id == ""
    assert block.body.plain == "Allow write?\n\npath: /tmp/a\n\ncommand: rm", \
        "空 detail 跳过，段间空一行"


def test_permission_details_keep_brackets_literal():
    assert "[bold]" in permission("Allow?", ["path: [bold]"]).body.plain


def test_split_trailing_keycap_carries_possible_prefix():
    assert split_trailing_keycap("abc") == ("abc", "")
    assert split_trailing_keycap("ab1") == ("ab", "1"), "裸数字可能是键帽开头，留到下一片"
    assert split_trailing_keycap("ab1\ufe0f") == ("ab", "1\ufe0f")
    assert split_trailing_keycap("ab1️⃣") == ("ab1️⃣", ""), "完整序列不再拆"


def test_keycap_split_and_strip_compose():
    """流式两片拼回后与一次性降级等价（跨 chunk 半截键帽的正确性口径）。"""
    pending = ""
    pieces = []
    for chunk in ("run 1", "\ufe0f", "\u20e3 now"):
        piece, pending = split_trailing_keycap(pending + chunk)
        pieces.append(strip_keycaps(piece))
    assert "".join(pieces) + pending == "run 1 now"


def test_format_args_moves_with_blocks_publicly():
    assert format_args({"a": 1}) == "a: 1"
    assert format_args({}) == "{}"


def test_dim_body_is_the_shared_text_style():
    text = dim_body("x 1️⃣")
    assert text.plain == "x 1" and text.style == "dim #e2e8f0"
