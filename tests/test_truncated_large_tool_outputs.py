"""上下文压缩中的工具结果落盘与统一截断：read 不落盘，其余工具超限落盘全文并回填预览。"""
import re

import pytest

import core.context.compact.context_compact as compact_mod
from core.context.compact import truncate_large_tool_outputs
from core.context.truncate import truncate_tool_output
from core.template import PERSIST_TOOL_MESSAGE_PREFIX, PERSIST_TOOL_MESSAGE_SUFFIX
from core.tools.base_tools.read import ReadFile


@pytest.fixture
def tool_results_dir(tmp_path, monkeypatch):
    """把落盘目录指向 tmp_path，避免污染真实 .harness。"""
    monkeypatch.setattr(compact_mod, "TOOL_RESULTS_DIR", tmp_path)
    return tmp_path


def test_persist_tool_output_writes_full_text_and_uses_given_preview(tool_results_dir):
    inline = compact_mod.persist_tool_output("call_preview", "full-content", preview="short")
    assert (tool_results_dir / "call_preview.text").read_text(encoding="utf-8") == "full-content"
    assert "<content-preview>short</content-preview>" in inline
    assert "<saved-path>" in inline


def test_persist_tool_output_persists_small_output_when_caller_asks(tool_results_dir):
    """落盘与否由调用方（是否截断）决定，没有内嵌阈值。"""
    inline = compact_mod.persist_tool_output("call_tiny", "tiny", preview="tiny")
    assert (tool_results_dir / "call_tiny.text").read_text(encoding="utf-8") == "tiny"
    assert inline.startswith(PERSIST_TOOL_MESSAGE_PREFIX)
    assert inline.endswith(PERSIST_TOOL_MESSAGE_SUFFIX)


def test_persist_tool_output_requires_explicit_preview(tool_results_dir):
    """预览必须由调用方传入，避免回退到无边界的全文内联。"""
    with pytest.raises(TypeError):
        compact_mod.persist_tool_output("call_no_preview", "full")


def test_persist_tool_output_is_idempotent(tool_results_dir):
    first = compact_mod.persist_tool_output("call_once", "data", preview="p")
    second = compact_mod.persist_tool_output("call_once", first, preview="ignored")
    assert second == first
    assert (tool_results_dir / "call_once.text").read_text(encoding="utf-8") == "data"
    assert len(list(tool_results_dir.iterdir())) == 1


def _large_text(lines: int = 800) -> str:
    """约 80KB 的文本：超过 50KB 字节上限，但不足 2000 行。"""
    return "\n".join(f"line-{i:04d}-{'x' * 90}" for i in range(lines))


def _messages(tool_name: str, tool_call_id: str, content: str) -> list[dict]:
    """assistant.tool_calls + tool 结果：工具名只能从 tool_calls 映射得到。"""
    return [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": tool_call_id, "type": "function",
             "function": {"name": tool_name, "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": tool_call_id, "content": content},
    ]


def test_read_output_is_not_persisted_and_stays_as_is(tool_results_dir):
    content = truncate_tool_output(_large_text())[0]  # read 工具侧的截断结果
    messages = _messages("read_file", "call_read", content)
    assert truncate_large_tool_outputs(messages) is messages
    assert messages[-1]["content"] == content
    assert list(tool_results_dir.iterdir()) == []


def test_non_read_over_byte_limit_persists_full_output(tool_results_dir):
    content = _large_text()
    messages = _messages("terminal", "call_term", content)
    truncate_large_tool_outputs(messages)

    inline = messages[-1]["content"]
    assert inline.startswith(PERSIST_TOOL_MESSAGE_PREFIX)
    assert inline.endswith(PERSIST_TOOL_MESSAGE_SUFFIX)
    assert "[Truncated:" in inline
    saved = list(tool_results_dir.iterdir())
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8") == content
    assert str(saved[0]) in inline
    assert len(inline) < len(content)


def test_non_read_over_line_limit_persists_full_output(tool_results_dir):
    content = "\n".join(f"row-{i}" for i in range(2500))  # 25KB：触发 2000 行上限
    messages = _messages("grep", "call_lines", content)
    truncate_large_tool_outputs(messages)

    assert messages[-1]["content"].startswith(PERSIST_TOOL_MESSAGE_PREFIX)
    saved = next(iter(tool_results_dir.iterdir()))
    assert saved.read_text(encoding="utf-8") == content


def test_non_read_below_hard_cap_is_untouched(tool_results_dir):
    content = "x" * 10_000  # 超过旧工具结果内联预算，但未超 50KB 硬上限
    messages = _messages("terminal", "call_10k", content)
    truncate_large_tool_outputs(messages)
    assert messages[-1]["content"] == content
    assert list(tool_results_dir.iterdir()) == []


def test_small_tool_output_is_untouched(tool_results_dir):
    messages = _messages("terminal", "call_small", "ok")
    truncate_large_tool_outputs(messages)
    assert messages[-1]["content"] == "ok"
    assert list(tool_results_dir.iterdir()) == []


def test_unknown_tool_call_id_is_treated_as_non_read(tool_results_dir):
    messages = [{"role": "tool", "tool_call_id": "call_orphan", "content": _large_text()}]
    truncate_large_tool_outputs(messages)
    assert messages[0]["content"].startswith(PERSIST_TOOL_MESSAGE_PREFIX)
    assert len(list(tool_results_dir.iterdir())) == 1


def test_second_run_is_idempotent(tool_results_dir):
    content = _large_text()
    messages = _messages("terminal", "call_idem", content)
    truncate_large_tool_outputs(messages)
    first = messages[-1]["content"]
    truncate_large_tool_outputs(messages)
    assert messages[-1]["content"] == first
    assert len(list(tool_results_dir.iterdir())) == 1


def test_saved_file_continues_at_notice_offset(tool_results_dir):
    content = _large_text()
    messages = _messages("terminal", "call_cont", content)
    truncate_large_tool_outputs(messages)

    inline = messages[-1]["content"]
    offset = int(re.search(r"offset=(\d+)", inline).group(1))
    saved = next(iter(tool_results_dir.iterdir()))
    # 截断从 offset=1 起算，落盘全文的行号与提示 offset 对齐，可直接续读
    assert ReadFile.model_construct(path=str(saved), limit=None, offset=offset).run() == \
        "\n".join(content.splitlines()[offset - 1:])
